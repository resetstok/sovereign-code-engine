"""
Módulo: pci_engine/micro_harness.py
Harness Ultraleve para Modelos 3B (Inspirado em little-coder / pi-coding-agent).
Elimina overhead de System Prompt (< 1.000 tokens) e aplica travas em tempo de execução
(Runtime Invariants: read-before-edit e truncamento estrito de terminal).
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import urllib.request
import urllib.error

logger = logging.getLogger("pci_engine.micro_harness")

# Prompt de Sistema Cirúrgico: ~350 tokens (Zero inflação de KV Cache na GTX 1650)
SYSTEM_PROMPT = """Você é o PCI Micro-Coder, um programador cirúrgico para hardware restrito.
Seu objetivo é implementar código Python conciso que passe estritamente nos testes unitários.

REGRAS RÍGIDAS DE EXECUÇÃO:
1. TRAVA INVARIANTE: Você NÃO PODE editar ou sobrescrever um arquivo sem antes chamá-lo com a ferramenta 'read'.
2. CONCISÃO ABSOLUTA: Retorne apenas chamadas de ferramentas JSON ou respostas mínimas.
3. SEM FORMATAÇÃO CONVERSACIONAL: Não diga 'Claro', 'Aqui está o código' ou comentários prolixos.
4. TDD REVERSO: Implemente apenas o estritamente necessário para o teste fornecido passar.

FERRAMENTAS DISPONÍVEIS:
- read(filepath: str) -> str
- write(filepath: str, content: str) -> str
- edit(filepath: str, target: str, replacement: str) -> str
- bash(command: str) -> str (retorna exit_code e últimas 15 linhas)
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
        ollama_url: str = "http://127.0.0.1:11434",
        model: str = "huihui_ai/qwen2.5-coder-abliterate:3b",
    ):
        self.repo_root = Path(repo_root).resolve()
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
            res = subprocess.run(
                command,
                shell=True,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            stdout_lines = res.stdout.strip().splitlines()
            stderr_lines = res.stderr.strip().splitlines()

            # Pega as últimas 10 linhas relevantes para não explodir o contexto
            tail_stdout = "\n".join(stdout_lines[-10:]) if stdout_lines else ""
            tail_stderr = "\n".join(stderr_lines[-10:]) if stderr_lines else ""

            output_summary = tail_stdout
            if tail_stderr:
                output_summary += f"\nSTDERR TAIL:\n{tail_stderr}"

            return f"[EXIT_CODE: {res.returncode}]\n{output_summary[:600]}"
        except subprocess.TimeoutExpired:
            return "[EXIT_CODE: -1]\nTIMEOUT: O comando excedeu 25 segundos."
        except Exception as exc:
            return f"[EXIT_CODE: -1]\nERRO AO EXECUTAR: {str(exc)}"

    # -------------------------------------------------------------------------
    # Diário Append-Only & Prompt Compacto
    # -------------------------------------------------------------------------
    def build_prompt_with_journal(self, task_spec: str, test_code: str, target_file: str) -> str:
        """Monta o payload sem histórico de chat inflado, apenas Estado Atual + Diário de Falhas."""
        journal_text = ""
        if self.journal:
            journal_text = "### DIÁRIO DE TENTATIVAS ANTERIORES QUE FALHARAM (NÃO REPITA ESTES ERROS):\n"
            for entry in self.journal[-3:]:  # Mantém no máximo os últimos 3 registros
                journal_text += (
                    f"- Tentativa {entry.attempt}:\n"
                    f"  Falha no teste: {entry.failed_test_output}\n"
                    f"  Ação tentada: {entry.action_taken}\n"
                )

        file_snapshot = ""
        target_path = (self.repo_root / target_file).resolve()
        if target_path.exists():
            content = target_path.read_text(encoding="utf-8", errors="ignore")
            file_snapshot = f"### CONTEÚDO ATUAL DE '{target_file}':\n```python\n{content}\n```\n"

        prompt = f"""{file_snapshot}
### TAREFA ATÔMICA:
{task_spec}

### TESTE OBRIGATÓRIO (PYTEST):
```python
{test_code}
```

{journal_text}
Com base nisso, retorne a implementação completa e correta de '{target_file}' para que o Pytest passe.
"""
        return prompt

    def execute_task_cycle(
        self,
        task_id: str,
        spec_markdown: str,
        test_code: str,
        target_file_path: str,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """Executa o ciclo completo TDD usando o modelo local com travas ativas."""
        self.reset_session()
        target_file = target_file_path.replace("\\", "/")

        # 1. Garante leitura inicial para respeitar a invariante de edição
        if (self.repo_root / target_file).exists():
            self.tool_read(target_file)

        # 2. Escreve o arquivo de teste no sandbox
        test_file_path = f"tests/unit/test_{Path(target_file).name}"
        (self.repo_root / test_file_path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo_root / test_file_path).write_text(test_code, encoding="utf-8")

        for attempt in range(1, max_attempts + 1):
            logger.info(f"MicroHarness ciclo {task_id} - Tentativa {attempt}/{max_attempts}")
            prompt = self.build_prompt_with_journal(spec_markdown, test_code, target_file)

            # Chama Ollama local
            try:
                payload = json.dumps({
                    "model": self.model,
                    "system": SYSTEM_PROMPT,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 1024,
                        "top_p": 0.9,
                    },
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.ollama_url}/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=45) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    generated_text = resp_data.get("response", "")
            except Exception as exc:
                return {"success": False, "error": f"Erro de conexão com Ollama: {exc}"}

            # Extrai o código Python gerado de blocos markdown
            clean_code = self._extract_python_code(generated_text)

            # Grava no arquivo (respeitando a invariante)
            self.tool_write(target_file, clean_code)

            # Roda o teste isolado via tool_bash
            test_output = self.tool_bash(f"pytest -q --tb=short {test_file_path}")
            if "[EXIT_CODE: 0]" in test_output:
                return {
                    "success": True,
                    "task_id": task_id,
                    "attempts": attempt,
                    "code": clean_code,
                    "test_file": test_file_path,
                }
            else:
                # Registra no diário append-only e tenta a próxima iteração
                short_fail = test_output.split("\n", 1)[-1][:300]
                self.journal.append(
                    JournalEntry(
                        attempt=attempt,
                        failed_test_output=short_fail,
                        action_taken=f"Geradas {len(clean_code.splitlines())} linhas",
                    )
                )

        return {
            "success": False,
            "task_id": task_id,
            "attempts": max_attempts,
            "last_error": self.journal[-1].failed_test_output if self.journal else "Falha desconhecida",
        }

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
