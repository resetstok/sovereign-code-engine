"""
Módulo: pci_engine/orchestrator.py
Executor de Contenção Estrita (Little-Coder Pattern) e Gestor de Sandboxes Git Worktree.
Projetado especificamente para modelos de 3B em hardwares limitados (8GB RAM / GTX 1650 4GB VRAM).
Elimina prompts inflados (<800 tokens), aplica invariantes de runtime (read-before-edit e ast.parse)
e garante isolamento em branches e worktrees temporários sem tocar na branch principal de trabalho.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from pci_engine.database import DatabaseManager
from pci_engine.router import PCIRouterAndBroker

logger = logging.getLogger("pci_engine.orchestrator")

# Prompt de Sistema Cirúrgico: ~75 palavras / ~100 tokens (< 800 tokens)
SYSTEM_PROMPT_MINIMAL = """Você é o PCI Strict Code Orchestrator, um gerador cirúrgico para hardware restrito (GTX 1650 4GB).
Seu objetivo é implementar código Python conciso que passe estritamente no teste unitário.

REGRAS RÍGIDAS DE CONTENÇÃO:
1. Retorne APENAS o bloco de código Python em ```python ... ``` contendo a implementação completa.
2. Não inclua texto conversacional, saudações, explicações ou notas de rodapé.
3. Não importe dependências pesadas não declaradas.
4. TDD Reverso: implemente estritamente o necessário para que o teste fornecido passe.
"""


class RuntimeInvariantViolation(Exception):
    """Lançada quando uma invariante de segurança de execução é violada."""
    pass


# =============================================================================
# Esquemas de Compatibilidade Retroativa (DAG e Decomposição)
# =============================================================================
class MicrotaskSpec(BaseModel):
    task_id: str = Field(..., description="Identificador único, ex: TASK_MAP_001")
    depends_on_task_id: Optional[str] = Field(None, description="Task ID da dependência ou null")
    task_type: str = Field(..., description="EXTRACTION, CRUD, REFACTOR, PARSER_OSINT ou ARCHITECTURE")
    privacy_level: str = Field("PUBLIC", description="STRICT_LOCAL, SANITIZED_DERIVATIVE ou PUBLIC")
    permission_level: int = Field(2, ge=1, le=4, description="Nível de permissão 1-4")
    estimated_lines: int = Field(25, ge=5, le=120, description="Linhas estimadas (15 a 35 ideal)")
    target_file_path: str = Field(..., description="Caminho relativo do arquivo, ex: src/parser.py")
    spec_markdown: str = Field(..., description="Especificação em markdown do que deve ser feito")
    test_code: str = Field(..., description="Código Pytest que valida a implementação")


class MasterbookDecompositionSchema(BaseModel):
    parent_masterbook: str = Field(..., description="Identificador do Masterbook de origem")
    tasks: List[MicrotaskSpec] = Field(..., description="Lista ordenada de microtarefas em DAG")


@dataclass
class MicrotaskDAGNode:
    task_id: str
    parent_masterbook: str
    depends_on_task_id: Optional[str]
    task_type: str
    privacy_level: str
    permission_level: int
    estimated_lines: int
    spec_markdown: str
    test_code: str
    target_file_path: str


# =============================================================================
# EXECUTOR DE CONTENÇÃO ESTRITA (LITTLE-CODER PATTERN + GIT WORKTREE)
# =============================================================================
class TaskExecutionOrchestrator:
    """
    Motor de execução de microtarefas com contenção cirúrgica de contexto (<800 tokens),
    validação sintática prévia via ast.parse(), respeito rígido à regra read-before-edit
    e isolamento em branches/worktrees do Git.
    """

    def __init__(
        self,
        repo_root: Path | str = ".",
        db_path: Path | str = "data/jobs.db",
        ollama_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5-coder:3b",
        openai_endpoint: Optional[str] = None,
        openai_api_key: Optional[str] = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.db_path = Path(db_path).resolve()
        self.db = DatabaseManager(db_path=self.db_path)
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.openai_endpoint = openai_endpoint.rstrip("/") if openai_endpoint else None
        self.openai_api_key = openai_api_key
        self.read_files_session: Set[str] = set()

    def reset_session(self) -> None:
        """Limpa o rastreamento de arquivos lidos na sessão atual."""
        self.read_files_session.clear()

    # -------------------------------------------------------------------------
    # 1. Regra Runtime Invariant: Read-Before-Edit
    # -------------------------------------------------------------------------
    def read_target_file(self, target_rel_path: str) -> Optional[str]:
        """
        Lê o arquivo-alvo no repositório e registra na sessão.
        Essencial para cumprir a regra read-before-edit.
        """
        full_path = (self.repo_root / target_rel_path).resolve()
        norm_path = target_rel_path.replace("\\", "/")
        if not full_path.exists():
            return None

        content = full_path.read_text(encoding="utf-8", errors="ignore")
        self.read_files_session.add(norm_path)
        return content

    def verify_read_before_edit(self, target_rel_path: str) -> None:
        """
        Valida que o arquivo-alvo foi lido se ele já existir no repositório.
        Lança RuntimeInvariantViolation se houver tentativa de editar sem ler.
        """
        norm_path = target_rel_path.replace("\\", "/")
        full_path = (self.repo_root / target_rel_path).resolve()
        if full_path.exists() and norm_path not in self.read_files_session:
            raise RuntimeInvariantViolation(
                f"INVARIANT_VIOLATION: O arquivo '{target_rel_path}' já existe no repositório "
                f"mas não foi lido previamente nesta sessão. Chame read_target_file() antes de editar."
            )

    # -------------------------------------------------------------------------
    # 2. Verificação Sintática via ast.parse() e Extração de Código
    # -------------------------------------------------------------------------
    @staticmethod
    def validate_syntax(code: str) -> Tuple[bool, Optional[str]]:
        """
        Analisa a Árvore Sintática Abstrata (AST) do Python antes de acionar o Pytest.
        Detecta erros de indentação, parênteses ou sintaxe quebrada em 0 milissegundos.
        """
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as exc:
            return False, f"SyntaxError na linha {exc.lineno}: {exc.msg}"
        except Exception as exc:
            return False, f"SyntaxError: {str(exc)}"

    @staticmethod
    def extract_clean_code(raw_text: str) -> str:
        """Extrai o bloco limpo de código python delimitado por markdown."""
        if "```python" in raw_text:
            parts = raw_text.split("```python")
            return parts[1].split("```")[0].strip()
        elif "```" in raw_text:
            parts = raw_text.split("```")
            return parts[1].split("```")[0].strip()
        return raw_text.strip()

    # -------------------------------------------------------------------------
    # 3. Construção do Prompt Minimalista (<800 tokens)
    # -------------------------------------------------------------------------
    def build_prompt(
        self,
        spec_markdown: str,
        test_code: str,
        target_file_path: str,
        current_content: Optional[str] = None,
        previous_error_summary: Optional[str] = None,
    ) -> str:
        """
        Monta o payload estrito com especificação, teste, código atual e no máximo 3 linhas
        de erro anterior. Sem histórico conversacional prolixo para proteger a VRAM de 4GB.
        """
        file_block = ""
        if current_content:
            file_block = f"### CONTEÚDO ATUAL DE '{target_file_path}':\n```python\n{current_content}\n```\n\n"

        error_block = ""
        if previous_error_summary:
            # Captura no máximo as últimas 3 linhas relevantes
            lines = [ln.strip() for ln in previous_error_summary.strip().splitlines() if ln.strip()]
            short_lines = lines[-3:] if lines else []
            if short_lines:
                error_block = (
                    "### DIÁRIO DE ERRO ANTERIOR (NÃO REPITA ESTA FALHA):\n"
                    + "\n".join(f"- {l}" for l in short_lines)
                    + "\n\n"
                )

        prompt = (
            f"{file_block}"
            f"### ESPECIFICAÇÃO DA MICROTAREFA:\n{spec_markdown}\n\n"
            f"### TESTE OBRIGATÓRIO (PYTEST):\n```python\n{test_code}\n```\n\n"
            f"{error_block}"
            f"Retorne apenas o bloco ```python com a implementação completa:"
        )
        return prompt

    # -------------------------------------------------------------------------
    # 4. Inferência (Ollama Local ou OpenAI-Compatible em Nuvem)
    # -------------------------------------------------------------------------
    def _call_llm(self, prompt: str, route: Optional[str] = None) -> str:
        """Roteia a geração para OpenAI-compatible API ou Ollama local com isolamento de proxy."""
        if route and route.startswith("CLOUD_API:") and self.openai_endpoint:
            return self._call_openai_compatible(prompt)
        return self._call_ollama(prompt)

    def _call_ollama(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "system": SYSTEM_PROMPT_MINIMAL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 1024, "top_p": 0.9},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "")

    def _call_openai_compatible(self, prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.openai_api_key:
            headers["Authorization"] = f"Bearer {self.openai_api_key}"

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_MINIMAL},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1024,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.openai_endpoint}/chat/completions",
            data=payload,
            headers=headers,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""

    # -------------------------------------------------------------------------
    # 5. Isolamento e Execução em Git Worktree / Sandbox
    # -------------------------------------------------------------------------
    def execute_in_worktree(
        self,
        task: Dict[str, Any],
        candidate_code: str,
        timeout_sec: int = 30,
    ) -> Dict[str, Any]:
        """
        Executa a validação em worktree destacada ou branch seguro pci-task/<task_id>.
        Se o teste passar, executa commit atômico no branch, promove no banco e atualiza a matriz.
        """
        task_id = task["task_id"]
        target_rel_path = task["target_file_path"]
        test_code = task["test_code"]
        branch_name = f"pci-task/{task_id}"

        worktree_dir = (self.repo_root / "data" / "sandboxes" / f"sandbox_{task_id}").resolve()
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        # Limpa worktree órfã anterior se existir
        if worktree_dir.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_dir)],
                cwd=str(self.repo_root),
                capture_output=True,
            )

        # Tenta criar git worktree com branch dedicado
        cmd_add = ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD"]
        res_add = subprocess.run(cmd_add, cwd=str(self.repo_root), capture_output=True, text=True)

        using_worktree = (res_add.returncode == 0)
        sandbox_dir = worktree_dir if using_worktree else Path(tempfile.mkdtemp(prefix=f"pci_sb_{task_id}_"))

        try:
            # 1. Escreve arquivo-alvo no sandbox
            target_path = sandbox_dir / target_rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(candidate_code, encoding="utf-8")

            # Espelha na raiz se estiver em subdiretório para suportar imports diretos
            target_root = sandbox_dir / Path(target_rel_path).name
            if target_root != target_path:
                target_root.write_text(candidate_code, encoding="utf-8")

            # 2. Escreve arquivo de teste
            test_file = sandbox_dir / f"test_{Path(target_rel_path).name}"
            test_file.write_text(test_code, encoding="utf-8")

            # 3. Executa Pytest isolado
            cmd_pytest = [
                sys.executable,
                "-m", "pytest",
                "-q",
                "--tb=short",
                "-o", "pythonpath=.",
                "--disable-warnings",
                str(test_file),
            ]
            res_test = subprocess.run(
                cmd_pytest,
                cwd=str(sandbox_dir),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )

            if res_test.returncode == 0:
                # SUCESSO: Commit atômico na branch da tarefa
                if using_worktree:
                    subprocess.run(["git", "add", target_rel_path], cwd=str(sandbox_dir), capture_output=True)
                    subprocess.run(
                        ["git", "commit", "-m", f"feat(pci-task): verified implementation for {task_id}"],
                        cwd=str(sandbox_dir),
                        capture_output=True,
                    )

                # Atualiza fila jobs.db e métricas
                self.db.update_task_status(
                    task_id=task_id,
                    status="VERIFIED",
                    candidate_patch_diff=candidate_code,
                )
                self._record_capability_sample(task["task_type"], success=True)
                self.db.unlock_ready_tasks()

                return {
                    "success": True,
                    "exit_code": 0,
                    "task_id": task_id,
                    "branch": branch_name if using_worktree else None,
                }
            else:
                # FALHA: Paginação do log mantendo apenas os últimos 500 caracteres
                full_output = (res_test.stdout or "") + ("\n" + res_test.stderr if res_test.stderr else "")
                traceback_tail = full_output[-500:].strip() or "Falha nos testes Pytest."

                return {
                    "success": False,
                    "exit_code": res_test.returncode,
                    "traceback_tail": traceback_tail,
                    "task_id": task_id,
                }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "traceback_tail": "Timeout de 30 segundos excedido na execução do Pytest.",
                "task_id": task_id,
            }
        finally:
            if using_worktree:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree_dir)],
                    cwd=str(self.repo_root),
                    capture_output=True,
                )

    def _record_capability_sample(self, task_type: str, success: bool) -> None:
        """Atualiza a métrica ponderada na capability_matrix para o MASTER_NODE."""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO nodes (
                    node_id, node_name, role, ram_total_mb, vram_total_mb,
                    ssd_free_gb, supported_models, status
                ) VALUES ('MASTER_NODE', 'PC_MASTER', 'MASTER', 8192, 4096, 100.0, '["qwen2.5-coder:3b"]', 'ONLINE')
                """
            )
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
                ) VALUES ('MASTER_NODE', ?, ?, ?, ?, 18.0, 'MEASURED', 'v2.1')
                ON CONFLICT(node_id, model_name, task_type) DO UPDATE SET
                    sample_size = excluded.sample_size,
                    success_rate = excluded.success_rate,
                    verification_state = 'MEASURED'
                """,
                (self.model, task_type, new_sample, new_rate),
            )
            conn.commit()

    # -------------------------------------------------------------------------
    # 6. Ciclo Completo de Execução com Diário de Falhas (TDD)
    # -------------------------------------------------------------------------
    def execute_microtask(self, task_id: str, max_attempts: int = 3) -> Dict[str, Any]:
        """
        Executa o ciclo completo da microtarefa:
        1. Enforça read-before-edit se o arquivo-alvo já existir.
        2. Gera código com prompt minimalista (<800 tokens).
        3. Valida sintaxe via ast.parse().
        4. Executa em sandbox Git Worktree com timeout de 30s.
        5. Atualiza jobs.db e capability_matrix.
        """
        task = self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Tarefa '{task_id}' não encontrada na fila jobs.db.")

        target_file = task["target_file_path"]

        # Trava Invariante: Lê arquivo se existir antes de aceitar edições
        current_content = self.read_target_file(target_file)

        self.db.update_task_status(task_id, status="VALIDATING")

        error_journal = ""
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            logger.info(f"Executando microtarefa {task_id} - Tentativa {attempt}/{max_attempts}")

            prompt = self.build_prompt(
                spec_markdown=task["spec_markdown"],
                test_code=task["test_code"],
                target_file_path=target_file,
                current_content=current_content,
                previous_error_summary=error_journal,
            )

            try:
                raw_text = self._call_llm(prompt=prompt, route=task.get("assigned_route"))
            except Exception as exc:
                last_error = f"Erro de conexão com LLM: {str(exc)}"
                error_journal = last_error[-500:]
                continue

            code = self.extract_clean_code(raw_text)

            # Invariante: Validação Sintática prévia via ast.parse()
            is_valid_syntax, syntax_err = self.validate_syntax(code)
            if not is_valid_syntax:
                last_error = syntax_err or "SyntaxError detectado via ast.parse()"
                error_journal = last_error
                logger.warning(f"Sintaxe inválida rejeitada antes do Pytest: {syntax_err}")
                continue

            # Execução em Worktree isolado
            run_res = self.execute_in_worktree(task=task, candidate_code=code, timeout_sec=30)
            if run_res["success"]:
                return {
                    "success": True,
                    "task_id": task_id,
                    "attempts": attempt,
                    "code": code,
                    "branch": run_res.get("branch"),
                }
            else:
                last_error = run_res.get("traceback_tail", "Falha nos testes Pytest")
                error_journal = last_error

        # Falha persistente após esgotar tentativas
        new_status = (
            "BLOCKED_HUMAN_REVIEW"
            if (task["attempt_count"] + max_attempts) >= task["max_attempts"]
            else "FAILED_RETRY"
        )
        with self.db.get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks_queue
                SET status = ?, attempt_count = attempt_count + ?, last_error_log = ?
                WHERE task_id = ?
                """,
                (new_status, max_attempts, last_error, task_id),
            )
            conn.commit()

        self._record_capability_sample(task["task_type"], success=False)
        return {
            "success": False,
            "task_id": task_id,
            "attempts": max_attempts,
            "last_error": last_error,
        }

    # -------------------------------------------------------------------------
    # 7. Suporte ao Aider / OpenCode CLI
    # -------------------------------------------------------------------------
    def launch_aider_cli(
        self,
        target_files: List[str],
        test_cmd: str = "pytest -q --tb=short",
        extra_flags: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Tuple[int, List[str]]:
        """
        Invoca o Aider ou OpenCode CLI com restrições severas de contenção:
        --no-repomap: Elimina a montagem de grafos que estouram o contexto do 3B.
        --message-history-size 3: Trunca histórico de conversa em 3 mensagens.
        --test-cmd: Validação TDD automática com pytest.
        """
        cmd = [
            "aider",
            "--no-repomap",
            "--message-history-size", "3",
            "--test-cmd", test_cmd,
        ]
        if extra_flags:
            cmd.extend(extra_flags)
        cmd.extend(target_files)

        if dry_run:
            return 0, cmd

        res = subprocess.run(cmd, cwd=str(self.repo_root))
        return res.returncode, cmd


# =============================================================================
# Subclasse Retrocompatível para Orquestrador de Masterbook
# =============================================================================
class MasterbookOrchestrator(TaskExecutionOrchestrator):
    """
    Subclasse com métodos retrocompatíveis de enfileiramento DAG e despacho
    para manter compatibilidade com testes já existentes.
    """

    def enqueue_microtask(self, node: MicrotaskDAGNode) -> str:
        with self.db.get_connection() as conn:
            initial_status = "WAITING_DEPS" if node.depends_on_task_id else "PENDING"
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks_queue (
                    task_id, parent_masterbook, depends_on_task_id, task_type,
                    privacy_level, permission_level, estimated_lines,
                    spec_markdown, test_code, target_file_path, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.task_id,
                    node.parent_masterbook,
                    node.depends_on_task_id,
                    node.task_type,
                    node.privacy_level,
                    node.permission_level,
                    node.estimated_lines,
                    node.spec_markdown,
                    node.test_code,
                    node.target_file_path,
                    initial_status,
                ),
            )
            conn.commit()
            logger.info(f"Microtarefa enfileirada: {node.task_id} [{initial_status}]")
            return node.task_id

    def update_dag_dependencies(self) -> int:
        return self.db.unlock_ready_tasks()

    def dispatch_next_cycle(self, api_quotas: Dict[str, Any], online_mode: bool = False) -> Optional[Dict[str, Any]]:
        router = PCIRouterAndBroker(db_path=self.db_path, repo_root=self.repo_root)
        self.update_dag_dependencies()
        router.reclaim_expired_leases()

        with self.db.get_connection() as conn:
            task = conn.execute(
                """
                SELECT * FROM tasks_queue
                WHERE status = 'PENDING'
                ORDER BY created_at ASC LIMIT 1
                """
            ).fetchone()

            if not task:
                return None

            route = router.route_task(task, api_quotas=api_quotas, online_mode=online_mode)
            conn.execute(
                "UPDATE tasks_queue SET assigned_route = ? WHERE task_id = ?",
                (route, task["task_id"]),
            )
            conn.commit()

            return {
                "task_id": task["task_id"],
                "route": route,
                "task_type": task["task_type"],
                "privacy": task["privacy_level"],
                "target": task["target_file_path"],
            }


# Aliases
CodeOrchestrator = TaskExecutionOrchestrator
StrictExecutionOrchestrator = TaskExecutionOrchestrator
