"""
Módulo: pci_engine/micro_harness.py
Harness Ultraleve para Modelos 3B (Inspirado em little-coder / pi-coding-agent).
Elimina overhead de System Prompt (< 1.000 tokens) e aplica travas em tempo de execução
(Runtime Invariants: read-before-edit e truncamento estrito de terminal).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import urllib.request
import urllib.error

from pci_engine.database import DatabaseManager
from pci_engine.runtime_utils import get_python_runtime, get_subprocess_creation_flags

logger = logging.getLogger("pci_engine.micro_harness")

# Prompt de Sistema Cirúrgico: ~280 tokens (Zero inflação de KV Cache na GTX 1650)
SYSTEM_PROMPT = """Você é o PCI Micro-Coder, um engenheiro de software Python cirúrgico com Zero Alucinação.
Seu objetivo é implementar código Python limpo, completo e exato que passe 100% nos testes unitários Pytest.

REGRAS RÍGIDAS DE CODIFICAÇÃO:
1. TRAVA INVARIANTE: Retorne APENAS o código-fonte do módulo solicitado dentro de um único bloco ```python ... ``` (respeitando read-before-edit).
2. TDD REVERSO: Implemente exatamente a lógica exigida para os testes passarem, incluindo todos os imports necessários no topo (ex: import re, import math).
3. PROIBIDO importar o próprio módulo dentro dele mesmo (nunca faça self-import) e NÃO copie funções def test_* para dentro do módulo de produção.
4. PRESERVAÇÃO INCREMENTAL: Se o arquivo já possuir funções anteriores, mantenha todas elas intactas e adicione a nova função solicitada.
"""


@dataclass
class JournalEntry:
    attempt: int
    failed_test_output: str
    action_taken: str


class MicroHarness:
    """
    Executor minimalista com controle de estado append-only e runtime invariants.
    Substitui agentes inflados (Aider/Cline/OpenHands) para execução 100% local com 3B.
    """

    def __init__(
        self,
        repo_root: Path | str,
        db_manager: Optional[DatabaseManager] = None,
        db_path: Optional[Path | str] = None,
        ollama_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5-coder:3b",
    ):
        self.repo_root = Path(repo_root).resolve()
        if db_manager:
            self.db = db_manager
        elif db_path:
            self.db = DatabaseManager(db_path=db_path)
        else:
            self.db = None
        self.ollama_url = ollama_url
        self.model = model
        self.read_files_session: Set[str] = set()
        self.journal: List[JournalEntry] = []

    def reset_session(self) -> None:
        self.read_files_session.clear()
        self.journal.clear()

    # -------------------------------------------------------------------------
    # Runtime Invariants & Ferramentas Estritas
    # -------------------------------------------------------------------------
    def tool_read(self, filepath: str) -> str:
        target = (self.repo_root / filepath).resolve()
        if not target.exists():
            return f"ERRO: Arquivo '{filepath}' não existe."
        content = target.read_text(encoding="utf-8", errors="ignore")
        self.read_files_session.add(filepath.replace("\\", "/"))
        return content

    def tool_write(self, filepath: str, content: str) -> str:
        norm_path = filepath.replace("\\", "/")
        # Invariante: Só permite escrever/editar se o arquivo for novo OU tiver sido lido antes
        target = (self.repo_root / filepath).resolve()
        if target.exists() and norm_path not in self.read_files_session:
            return (
                f"INVARIANT_VIOLATION: O arquivo '{filepath}' já existe e NÃO PODE ser alterado "
                f"sem uma chamada prévia a tool_read('{filepath}') nesta sessão."
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.read_files_session.add(norm_path)
        return f"OK: '{filepath}' gravado com sucesso ({len(content)} bytes)."

    def tool_edit(self, filepath: str, target_snippet: str, replacement: str) -> str:
        norm_path = filepath.replace("\\", "/")
        if norm_path not in self.read_files_session:
            return (
                f"INVARIANT_VIOLATION: Proibido editar '{filepath}' sem leitura prévia. "
                f"Execute tool_read('{filepath}') primeiro para mapear as linhas reais."
            )

        target_file = (self.repo_root / filepath).resolve()
        if not target_file.exists():
            return f"ERRO: Arquivo '{filepath}' não encontrado."

        current_content = target_file.read_text(encoding="utf-8")
        if target_snippet not in current_content:
            return (
                f"ERRO: Trecho-alvo não encontrado exatamente no arquivo. "
                f"Releia o arquivo para capturar a indentação e os caracteres exatos."
            )

        new_content = current_content.replace(target_snippet, replacement, 1)
        target_file.write_text(new_content, encoding="utf-8")
        return f"OK: Edição aplicada em '{filepath}'."

    def tool_bash(self, command: str, timeout_sec: int = 25) -> str:
        """Executa comando shell paginando a saída (ShellLog) e limitando o retorno para a IA."""
        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            res = subprocess.run(
                command,
                shell=True,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                creationflags=creation_flags,
            )
            stdout_lines = res.stdout.strip().splitlines()
            stderr_lines = res.stderr.strip().splitlines()

            # Pega as últimas 14 linhas relevantes para capturar a linha exata E AssertionError do Pytest
            tail_stdout = "\n".join(stdout_lines[-14:]) if stdout_lines else ""
            tail_stderr = "\n".join(stderr_lines[-10:]) if stderr_lines else ""

            output_summary = tail_stdout
            if tail_stderr:
                output_summary += f"\nSTDERR TAIL:\n{tail_stderr}"

            return f"[EXIT_CODE: {res.returncode}]\n{output_summary[-600:]}"
        except subprocess.TimeoutExpired:
            return "[EXIT_CODE: -1]\nTIMEOUT: O comando excedeu 25 segundos."
        except Exception as exc:
            return f"[EXIT_CODE: -1]\nERRO AO EXECUTAR: {str(exc)}"

    # -------------------------------------------------------------------------
    # Sanitizador AST & Fusão Incremental de Módulos (Zero-Regression)
    # -------------------------------------------------------------------------
    @staticmethod
    def _sanitize_and_merge_ast(clean_code: str, baseline_code: str, target_file: str) -> str:
        """
        Realiza cirurgia AST determinística no código gerado pelo modelo 3B:
        1. Remove auto-importações circulares (ex: 'from src.mod import func' dentro de 'src/mod.py').
        2. Remove funções 'def test_*' ecoadas acidentalmente para dentro de módulos de produção.
        3. Injeta imports da biblioteca padrão (re, math, json, typing, pathlib) se usados e esquecidos.
        4. Preserva funções/classes/imports já existentes em baseline_code que não foram redefinidos.
        """
        new_tree = ast.parse(clean_code)
        norm_target = target_file.replace("\\", "/").removesuffix(".py")
        target_mod_dotted = norm_target.replace("/", ".")
        target_stem = Path(target_file).stem
        is_prod_module = not target_stem.startswith("test_")

        filtered_new_body: List[ast.stmt] = []
        new_defined_names: Set[str] = set()
        imported_names: Set[str] = set()

        for node in new_tree.body:
            # 1. Filtra self-import circular
            if isinstance(node, ast.ImportFrom):
                mod_name = node.module or ""
                if mod_name in (target_mod_dotted, target_stem) or mod_name.endswith(f".{target_stem}"):
                    continue
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                kept_aliases = [
                    a for a in node.names
                    if a.name not in (target_mod_dotted, target_stem) and not a.name.endswith(f".{target_stem}")
                ]
                if not kept_aliases:
                    continue
                node.names = kept_aliases
                for alias in kept_aliases:
                    imported_names.add(alias.asname or alias.name.split(".")[0])
            # 2. Filtra funções de teste copiadas para dentro de módulo de produção
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if is_prod_module and node.name.startswith("test_"):
                    continue
                new_defined_names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                if is_prod_module and node.name.startswith("Test"):
                    continue
                new_defined_names.add(node.name)

            filtered_new_body.append(node)

        # 3. Se havia código anterior aprovado (baseline_code), preserva imports e funções não redefinidas
        preserved_imports: List[ast.stmt] = []
        preserved_defs: List[ast.stmt] = []
        if baseline_code.strip():
            try:
                base_tree = ast.parse(baseline_code)
                for bnode in base_tree.body:
                    if isinstance(bnode, (ast.Import, ast.ImportFrom)):
                        preserved_imports.append(bnode)
                        for alias in bnode.names:
                            imported_names.add(alias.asname or alias.name.split(".")[0])
                    elif isinstance(bnode, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if bnode.name not in new_defined_names:
                            preserved_defs.append(bnode)
                            new_defined_names.add(bnode.name)
            except SyntaxError:
                pass

        # 4. Separa imports novos das definições novas e desduplica imports idênticos
        new_imports = [n for n in filtered_new_body if isinstance(n, (ast.Import, ast.ImportFrom))]
        new_other = [n for n in filtered_new_body if not isinstance(n, (ast.Import, ast.ImportFrom))]

        seen_imp_src: Set[str] = set()
        dedup_imports: List[ast.stmt] = []
        for imp_node in preserved_imports + new_imports:
            imp_str = ast.unparse(imp_node).strip()
            if imp_str not in seen_imp_src:
                seen_imp_src.add(imp_str)
                dedup_imports.append(imp_node)

        combined_body = dedup_imports + preserved_defs + new_other
        combined_tree = ast.Module(body=combined_body, type_ignores=[])

        # 5. Detecta módulos stdlib usados no AST mas esquecidos nos imports pelo modelo 3B
        used_names: Set[str] = {
            n.id for n in ast.walk(combined_tree) if isinstance(n, ast.Name)
        }
        auto_imports: List[str] = []
        for std_mod in ("re", "math", "json", "hashlib", "collections", "itertools", "statistics"):
            if std_mod in used_names and std_mod not in imported_names:
                auto_imports.append(f"import {std_mod}")
        if "Path" in used_names and "Path" not in imported_names:
            auto_imports.append("from pathlib import Path")
        typing_needed = {"Any", "Dict", "List", "Optional", "Set", "Tuple", "Union", "Callable"} & (used_names - imported_names)
        if typing_needed:
            auto_imports.append(f"from typing import {', '.join(sorted(typing_needed))}")

        unparsed = ast.unparse(combined_tree)
        if auto_imports:
            unparsed = "\n".join(auto_imports) + "\n\n" + unparsed
        return unparsed.strip() + "\n"

    # -------------------------------------------------------------------------
    # Diário Append-Only & Prompt Compacto
    # -------------------------------------------------------------------------
    def build_prompt_with_journal(
        self,
        task_spec: str,
        test_code: str,
        target_file: str,
        baseline_content: Optional[str] = None,
    ) -> str:
        """Monta o payload sem histórico de chat inflado, apenas Estado Base + Diário de Falhas."""
        journal_text = ""
        if self.journal:
            journal_text = "### DIÁRIO DE TENTATIVAS ANTERIORES QUE FALHARAM (CORRIJA O ERRO EXATO ABAIXO):\n"
            for entry in self.journal[-3:]:
                journal_text += (
                    f"- Tentativa {entry.attempt} ({entry.action_taken}):\n"
                    f"{entry.failed_test_output}\n"
                )
            journal_text += (
                "Corrija a lógica de transformação/retorno para satisfazer exatamente os asserts acima!\n"
            )

        file_snapshot = ""
        if baseline_content is None:
            target_path = (self.repo_root / target_file).resolve()
            if target_path.exists():
                baseline_content = target_path.read_text(encoding="utf-8", errors="ignore")
        if baseline_content and baseline_content.strip():
            file_snapshot = (
                f"### CONTEÚDO JÁ APROVADO DE '{target_file}' (MANTENHA ESTAS FUNÇÕES E ADICIONE A NOVA):\n"
                f"```python\n{baseline_content.strip()}\n```\n"
            )

        prompt = f"""{file_snapshot}
### TAREFA ATÔMICA:
{task_spec}

### TESTE OBRIGATÓRIO (PYTEST) QUE VALIDARÁ SEU CÓDIGO:
```python
{test_code}
```

{journal_text}
Implemente o código de produção para '{target_file}' (NÃO inclua a função de teste nem auto-importação de '{target_file}') dentro de um bloco ```python ... ``` para que o Pytest passe.
"""
        return prompt

    def _call_cloud_api_if_configured(self, prompt: str) -> Optional[str]:
        """
        Se o operador ativou o Modo Híbrido e configurou uma chave de API (Gemini, Groq, OpenRouter,
        DeepSeek, OpenAI, Cerebras, Mistral), tenta gerar via API para máxima velocidade.
        Retorna None se nenhuma chave estiver ativa, fazendo fallback transparente para o Ollama local.
        """
        # 1. Google Gemini API (AI Studio)
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
                body = json.dumps({
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
                }).encode("utf-8")
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=35) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as exc:
                logger.warning(f"Fallback Gemini -> Ollama local ({exc})")

        # 2. Provedores compatíveis com OpenAI Chat Completions (Groq, OpenRouter, DeepSeek, OpenAI, Cerebras, Mistral)
        openai_compat = [
            ("GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
            ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions", "qwen/qwen-2.5-coder-32b-instruct:free"),
            ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1/chat/completions", "deepseek-chat"),
            ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
            ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", "llama3.1-8b"),
            ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions", "codestral-latest"),
        ]
        for env_name, endpoint, model_id in openai_compat:
            api_key = os.environ.get(env_name, "").strip()
            if api_key:
                try:
                    body = json.dumps({
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2048,
                    }).encode("utf-8")
                    req = urllib.request.Request(
                        endpoint,
                        data=body,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                            "User-Agent": "PCI-Sovereign-Engine/2.3",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=35) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        return data["choices"][0]["message"]["content"]
                except Exception as exc:
                    logger.warning(f"Fallback {env_name} -> próximo/Ollama local ({exc})")

        return None

    def _call_llm(self, prompt: str) -> str:
        """Executa a chamada HTTP para API Cloud (se Modo Híbrido ativo) ou Ollama 3B local em VRAM."""
        if os.environ.get("PCI_HYBRID_MODE") == "1":
            cloud_resp = self._call_cloud_api_if_configured(prompt)
            if cloud_resp:
                return cloud_resp

        attempt = getattr(self, "_current_attempt", 1)
        temp = 0.1 if attempt == 1 else (0.3 if attempt == 2 else 0.45)

        payload = json.dumps({
            "model": self.model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temp,
                "num_predict": 1024,
                "top_p": 0.9,
                "repeat_penalty": 1.12,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=60) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            return resp_data.get("response", "")

    def execute_task_cycle(
        self,
        task_id: str,
        spec_markdown: str,
        test_code: str,
        target_file_path: str,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """Executa o ciclo completo TDD usando o modelo local com travas AST e Pytest ativas."""
        self.reset_session()
        target_file = target_file_path.replace("\\", "/")

        # 1. Garante leitura inicial e captura snapshot base para fusão incremental AST sem perda de código anterior
        baseline_content = ""
        if (self.repo_root / target_file).exists():
            baseline_content = self.tool_read(target_file)

        # 2. Escreve o arquivo de teste no sandbox (garantindo import pytest se usado)
        if "pytest." in test_code and "import pytest" not in test_code:
            test_code = "import pytest\n" + test_code
        test_file_path = f"tests/unit/test_{Path(target_file).name}"
        (self.repo_root / test_file_path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo_root / test_file_path).write_text(test_code, encoding="utf-8")

        for attempt in range(1, max_attempts + 1):
            logger.info(f"MicroHarness ciclo {task_id} - Tentativa {attempt}/{max_attempts}")
            prompt = self.build_prompt_with_journal(
                spec_markdown,
                test_code,
                target_file,
                baseline_content=baseline_content,
            )

            # Chama o LLM
            self._current_attempt = attempt
            try:
                generated_text = self._call_llm(prompt)
            except Exception as exc:
                return {
                    "success": False,
                    "task_id": task_id,
                    "attempts": attempt,
                    "error": f"Erro de conexão com Ollama: {exc}",
                }

            # Extrai o código Python gerado de blocos markdown
            clean_code = self._extract_python_code(generated_text)

            # Invariante AST + Fusão Incremental: Valida sintaxe, remove self-imports e preserva funções anteriores
            try:
                clean_code = self._sanitize_and_merge_ast(clean_code, baseline_content, target_file)
            except SyntaxError as syn_err:
                self.journal.append(
                    JournalEntry(
                        attempt=attempt,
                        failed_test_output=f"SyntaxError na linha {syn_err.lineno}: {syn_err.msg}",
                        action_taken="Código rejeitado na barreira AST antes do Pytest",
                    )
                )
                continue

            # Grava no arquivo (respeitando a invariante read-before-edit)
            self.tool_write(target_file, clean_code)

            # Roda o teste isolado via tool_bash com inclusão do repositório no pythonpath
            python_bin = get_python_runtime()
            test_output = self.tool_bash(f'"{python_bin}" -m pytest -q --tb=short -o pythonpath=. {test_file_path}')
            if "[EXIT_CODE: 0]" in test_output:
                return {
                    "success": True,
                    "task_id": task_id,
                    "attempts": attempt,
                    "code": clean_code,
                    "test_file": test_file_path,
                }
            else:
                # Extrai apenas as linhas relevantes de falha (assert / E ...) para foco cirúrgico
                raw_lines = test_output.split("\n", 1)[-1].splitlines()
                err_lines = [
                    ln for ln in raw_lines
                    if ln.strip().startswith(("E ", "assert ", "FAILED ", "TypeError", "ValueError", "KeyError", "NameError"))
                ]
                short_fail = "\n".join(err_lines[-8:]) if err_lines else "\n".join(raw_lines[-8:])
                self.journal.append(
                    JournalEntry(
                        attempt=attempt,
                        failed_test_output=short_fail[:500],
                        action_taken=f"Geradas {len(clean_code.splitlines())} linhas",
                    )
                )

        return {
            "success": False,
            "task_id": task_id,
            "attempts": max_attempts,
            "last_error": self.journal[-1].failed_test_output if self.journal else "Falha desconhecida",
        }

    # -------------------------------------------------------------------------
    # Integração com Fila jobs.db e Capability Matrix
    # -------------------------------------------------------------------------
    def execute_queued_task(self, task_id: str) -> Dict[str, Any]:
        """Executa uma tarefa da fila jobs.db e atualiza os status e métricas."""
        if not self.db:
            raise RuntimeError("DatabaseManager não configurado no MicroHarness.")

        task = self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Tarefa '{task_id}' não encontrada no jobs.db.")

        # Transição para VALIDATING
        self.db.update_task_status(task_id, status="VALIDATING", assigned_route="LOCAL_3B")

        start_time = time.time()
        res = self.execute_task_cycle(
            task_id=task["task_id"],
            spec_markdown=task["spec_markdown"],
            test_code=task["test_code"],
            target_file_path=task["target_file_path"],
            max_attempts=task["max_attempts"],
        )
        duration_sec = time.time() - start_time

        if res["success"]:
            # Sucesso: atualiza status para VERIFIED, grava attempt_count e desbloqueia tarefas dependentes
            self.db.update_task_status(
                task_id=task_id,
                status="VERIFIED",
                candidate_patch_diff=res.get("code"),
            )
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE tasks_queue SET attempt_count = ? WHERE task_id = ?",
                    (res.get("attempts", 1), task_id),
                )
                conn.commit()
            promoted = self.db.unlock_ready_tasks()
            logger.info(f"Tarefa {task_id} VERIFIED com sucesso! {promoted} tarefa(s) dependente(s) desbloqueada(s).")
            self._update_capability_matrix(task["task_type"], success=True, duration_sec=duration_sec)
        else:
            current_attempts = task["attempt_count"] + res.get("attempts", 1)
            new_status = (
                "BLOCKED_HUMAN_REVIEW"
                if current_attempts >= task["max_attempts"]
                else "FAILED_RETRY"
            )
            err_msg = res.get("error") or res.get("last_error", "Falha de execução")
            with self.db.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tasks_queue
                    SET status = ?, attempt_count = ?, last_error_log = ?
                    WHERE task_id = ?
                    """,
                    (new_status, current_attempts, err_msg, task_id),
                )
                conn.commit()
            self._update_capability_matrix(task["task_type"], success=False, duration_sec=duration_sec)

        return res

    def run_next_local_task(self) -> Optional[Dict[str, Any]]:
        """Busca e executa a próxima tarefa elegível da fila cujas dependências estejam VERIFIED."""
        if not self.db:
            return None

        with self.db.get_connection() as conn:
            task = conn.execute(
                """
                SELECT task_id FROM tasks_queue
                WHERE status = 'PENDING'
                  AND (assigned_route IS NULL OR assigned_route != 'DONOR_POOL')
                  AND (depends_on_task_id IS NULL OR depends_on_task_id IN (
                      SELECT task_id FROM tasks_queue WHERE status = 'VERIFIED'
                  ))
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()

        if not task:
            return None

        return self.execute_queued_task(task["task_id"])

    def _update_capability_matrix(self, task_type: str, success: bool, duration_sec: float) -> None:
        """Atualiza a taxa de sucesso e métricas na capability_matrix para o MASTER_NODE."""
        if not self.db:
            return

        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT sample_size, success_rate FROM capability_matrix
                WHERE node_id = 'MASTER_NODE' AND model_name = ? AND task_type = ?
                """,
                (self.model, task_type),
            ).fetchone()

            if row:
                old_sample = row["sample_size"]
                old_rate = row["success_rate"]
                new_sample = old_sample + 1
                new_rate = round(((old_rate * old_sample) + (1.0 if success else 0.0)) / new_sample, 3)
            else:
                new_sample = 1
                new_rate = 1.0 if success else 0.0

            conn.execute(
                """
                INSERT INTO capability_matrix (
                    node_id, model_name, task_type, sample_size,
                    success_rate, avg_tokens_per_sec, verification_state, benchmark_version
                ) VALUES ('MASTER_NODE', ?, ?, ?, ?, 18.5, 'MEASURED', 'v2.1')
                ON CONFLICT(node_id, model_name, task_type) DO UPDATE SET
                    sample_size = excluded.sample_size,
                    success_rate = excluded.success_rate,
                    verification_state = 'MEASURED'
                """,
                (self.model, task_type, new_sample, new_rate),
            )
            conn.commit()

    @staticmethod
    def _extract_python_code(text: str) -> str:
        """Extrai o bloco ```python ... ``` ou retorna o texto puro se não houver marcação."""
        if "```python" in text:
            parts = text.split("```python")
            code = parts[1].split("```")[0]
            return code.strip()
        elif "```" in text:
            parts = text.split("```")
            code = parts[1].split("```")[0]
            return code.strip()
        return text.strip()
