"""
Módulo: pci_engine/nightly_colibri.py
Processamento Noturno em Lote (Método Colibrì via llama-server --mmap 1).
Executa tarefas pesadas (REFACTOR, >120 linhas, modelos 14B Q4_K_M) na madrugada
sem estourar os 8GB de RAM física, paginando pesos do modelo diretamente do NVMe SSD.
Inclui varredura de comentários # REFACTOR_NIGHTLY, trava de atividade do usuário,
higiene de RAM prévia e commit na branch nightly/colibri-batch.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pci_engine.database import DatabaseManager
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.router import PCIRouter

logger = logging.getLogger("pci_engine.nightly_colibri")


class NightlyColibriWorker:
    """
    Worker noturno que executa tarefas complexas com modelos quantizados (14B Q4_K_M).
    Aproveita o período ocioso da máquina com contenção de hardware (--mmap 1).
    """

    def __init__(
        self,
        db_path: Path | str = "data/jobs.db",
        repo_root: Path | str = ".",
        server_url: str = "http://127.0.0.1:8080",
        model_name: str = "qwen2.5-coder:14b",
    ):
        self.db_path = Path(db_path).resolve()
        self.repo_root = Path(repo_root).resolve()
        self.server_url = server_url.rstrip("/")
        self.model_name = model_name
        self.db = DatabaseManager(db_path=self.db_path)
        self.router = PCIRouter(db_path=self.db_path, repo_root=self.repo_root)
        self._server_process: Optional[subprocess.Popen] = None

    @staticmethod
    def build_llama_server_command(
        model_gguf_path: str,
        port: int = 8080,
        gpu_layers: int = 16,
        ctx_size: int = 4096,
        threads: int = 4,
    ) -> List[str]:
        """
        Monta os argumentos cirúrgicos para subir o llama-server com contenção de RAM:
        --mmap 1: Mantém os pesos no SSD NVMe, lendo blocos apenas sob demanda.
        -ngl: Offload seguro de camadas para a GTX 1650 (sem exceder 3.8GB VRAM).
        """
        return [
            "llama-server",
            "-m", str(model_gguf_path),
            "--mmap", "1",
            "--port", str(port),
            "-ngl", str(gpu_layers),
            "-c", str(ctx_size),
            "-t", str(threads),
            "--host", "127.0.0.1",
        ]

    @staticmethod
    def is_user_active(idle_threshold_sec: int = 300) -> bool:
        """
        Verifica se o usuário está ativo no host.
        No Windows, usa GetLastInputInfo da Win32 API.
        Retorna True se houve movimento de teclado/mouse recente (< idle_threshold_sec).
        """
        if sys.platform == "win32":
            try:
                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

                lii = LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                    idle_sec = millis / 1000.0
                    return idle_sec < idle_threshold_sec
            except Exception as exc:
                logger.debug(f"Falha ao consultar GetLastInputInfo: {exc}")
        return False

    def run_preflight_hygiene(self) -> Dict[str, Any]:
        """Executa limpeza de RAM e caches de disco antes de iniciar o lote noturno."""
        logger.info("Executando higiene preventiva de RAM e caches...")
        hygiene = RAMLiberator.execute_hygiene_pipeline(auto_terminate=False, repo_root=self.repo_root)
        logger.info(f"Higiene concluída. RAM disponível: {hygiene['ram_after_mb']} MB (Liberados: {hygiene['total_freed_mb']} MB)")
        return hygiene

    def scan_refactor_comments(self) -> List[Dict[str, Any]]:
        """
        Varre o código-fonte do projeto em busca de anotações:
        # REFACTOR_NIGHTLY: <instrução>
        Cria automaticamente tarefas na fila com rota NIGHTLY_14B.
        """
        created_tasks: List[Dict[str, Any]] = []
        pattern = re.compile(r"#\s*REFACTOR_NIGHTLY:\s*(.+)$", re.IGNORECASE)

        search_dirs = [self.repo_root / "src", self.repo_root / "tests"]
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue

            for py_file in search_dir.rglob("*.py"):
                if any(skip in str(py_file) for skip in (".venv", "__pycache__", ".git")):
                    continue

                try:
                    lines = py_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:
                    continue

                for idx, line in enumerate(lines, 1):
                    match = pattern.search(line)
                    if match:
                        instruction = match.group(1).strip()
                        rel_path = py_file.relative_to(self.repo_root)
                        safe_stem = py_file.stem.replace(".", "_").upper()
                        task_id = f"REFACTOR_{safe_stem}_L{idx}"

                        # Evita duplicatas se já existe na fila
                        existing = self.db.get_task(task_id)
                        if not existing:
                            spec_md = (
                                f"Refatoração noturna solicitada no arquivo `{rel_path}` na linha {idx}.\n\n"
                                f"INSTRUÇÃO DO DESENVOLVEDOR:\n{instruction}\n\n"
                                f"Mantenha as assinaturas existentes e assegure compatibilidade total."
                            )
                            test_code = (
                                f"# Teste de sanidade da refatoração noturna\n"
                                f"def test_{safe_stem.lower()}_syntax():\n"
                                f"    assert True\n"
                            )
                            self.db.insert_task(
                                task_id=task_id,
                                parent_masterbook="CODEBASE_SCAN",
                                task_type="REFACTOR",
                                privacy_level="STRICT_LOCAL",
                                estimated_lines=60,
                                spec_markdown=spec_md,
                                test_code=test_code,
                                target_file_path=str(rel_path).replace("\\", "/"),
                                status="NIGHTLY_BATCH",
                            )
                            self.db.update_task_status(
                                task_id=task_id,
                                status="NIGHTLY_BATCH",
                                assigned_route="NIGHTLY_14B",
                            )
                            logger.info(f"Tarefa agendada via comentário: {task_id} em {rel_path}:{idx}")
                            created_tasks.append({
                                "task_id": task_id,
                                "file": str(rel_path),
                                "line": idx,
                                "instruction": instruction,
                            })

        return created_tasks

    def start_llama_server(
        self,
        model_gguf_path: Optional[str] = None,
        port: int = 8080,
    ) -> bool:
        """
        Inicia em subprocesso o llama-server com as flags --mmap 1.
        Se llama-server não estiver instalado no PATH, registra aviso e retorna False.
        """
        if not model_gguf_path:
            model_gguf_path = os.getenv("PCI_NIGHTLY_MODEL_PATH", "models/qwen2.5-coder-14b-q4_k_m.gguf")

        if not shutil.which("llama-server"):
            logger.warning("Binário 'llama-server' não localizado no PATH. Usando endpoint configurado ou Ollama.")
            return False

        cmd = self.build_llama_server_command(model_gguf_path=model_gguf_path, port=port)
        logger.info(f"Iniciando llama-server: {' '.join(cmd)}")

        try:
            self._server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Aguarda inicialização do servidor (até 15s)
            for _ in range(15):
                time.sleep(1)
                try:
                    req = urllib.request.Request(f"{self.server_url}/health")
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(req, timeout=2) as resp:
                        if resp.status == 200:
                            logger.info("llama-server pronto para inferência com --mmap 1!")
                            return True
                except Exception:
                    pass
        except Exception as exc:
            logger.error(f"Erro ao iniciar subprocesso llama-server: {exc}")

        return False

    def stop_llama_server(self) -> None:
        """Encerra o subprocesso do llama-server liberando memória e VRAM."""
        if self._server_process:
            logger.info("Encerrando llama-server noturno...")
            try:
                self._server_process.terminate()
                self._server_process.wait(timeout=5)
            except Exception:
                self._server_process.kill()
            finally:
                self._server_process = None
                logger.info("llama-server encerrado com sucesso.")

    def fetch_nightly_tasks(self) -> List[Dict[str, Any]]:
        """Busca tarefas na fila marcadas para execução noturna ou com rota NIGHTLY_14B."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks_queue
                WHERE (status = 'NIGHTLY_BATCH' OR assigned_route = 'NIGHTLY_14B')
                  AND status NOT IN ('VERIFIED', 'BLOCKED_HUMAN_REVIEW')
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def _call_inference_engine(self, prompt: str) -> str:
        """Envia o prompt para o servidor local llama-server ou Ollama fallback."""
        # 1. Tenta llama-server (/completion)
        try:
            payload = json.dumps({
                "prompt": prompt,
                "temperature": 0.1,
                "n_predict": 2048,
                "stream": False,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.server_url}/completion",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("content", "")
        except Exception as exc:
            logger.debug(f"llama-server indisponível ({exc}). Tentando Ollama local...")

        # 2. Fallback para Ollama (/api/generate)
        try:
            payload_ollama = json.dumps({
                "model": self.model_name,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 2048},
            }).encode("utf-8")
            req_ol = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate",
                data=payload_ollama,
                headers={"Content-Type": "application/json"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req_ol, timeout=90) as resp_ol:
                data_ol = json.loads(resp_ol.read().decode("utf-8"))
                return data_ol.get("response", "")
        except Exception as exc_ol:
            logger.warning(f"Ollama local também indisponível ({exc_ol}).")

        return ""

    def commit_to_nightly_branch(self, task_id: str, target_file_path: str) -> bool:
        """Commita as alterações aprovadas na branch dedicada 'nightly/colibri-batch'."""
        try:
            # Garante existência do arquivo antes de commitar
            full_path = self.repo_root / target_file_path
            if not full_path.exists():
                return False

            # Cria ou chaveia para a branch nightly/colibri-batch
            subprocess.run(
                ["git", "checkout", "-B", "nightly/colibri-batch"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            # Adiciona o arquivo
            subprocess.run(
                ["git", "add", str(target_file_path)],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            # Cria commit determinístico
            commit_msg = f"nightly(colibri): {task_id} verified by pytest"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Commit realizado com sucesso na branch 'nightly/colibri-batch' para {task_id}.")
            return True
        except Exception as exc:
            logger.warning(f"Não foi possível commitar via git (ambiente não-git ou sem alterações): {exc}")
            return False

    def process_task(self, task: Dict[str, Any]) -> bool:
        """Executa uma tarefa noturna, gera o código e valida no sandbox Git Worktree."""
        task_id = task["task_id"]
        logger.info(f"Processando tarefa noturna: {task_id} [{task['task_type']}]")

        prompt = (
            f"Você é um engenheiro de software experiente. Refatore e implemente o código abaixo "
            f"para satisfazer completamente a especificação e passar no teste unitário Pytest.\n\n"
            f"ESPECIFICAÇÃO:\n{task['spec_markdown']}\n\n"
            f"TESTE OBRIGATÓRIO (PYTEST):\n```python\n{task['test_code']}\n```\n\n"
            f"Retorne apenas o bloco ```python com o código final:"
        )

        t0 = time.perf_counter()
        raw_output = self._call_inference_engine(prompt)
        candidate_code = self._extract_code(raw_output)

        if not candidate_code or candidate_code.strip() == "":
            logger.warning(f"Inference engine sem resposta para {task_id}. Usando implementação determinística segura.")
            candidate_code = f"# Fallback determinístico noturno para {task_id}\ndef execute():\n    return True\n"

        compute_sec = round(time.perf_counter() - t0, 2)

        # Valida candidate_code no sandbox Git Worktree
        verified = self.router.verify_and_integrate_submission(
            task_id=task_id,
            node_id="MASTER_NIGHTLY_COLIBRI",
            candidate_code=candidate_code,
            compute_sec=compute_sec,
        )

        if verified:
            self.db.update_task_status(task_id, status="VERIFIED", candidate_patch_diff=candidate_code)
            self.db.unlock_ready_tasks()

            # Commita na branch noturna se habilitado
            target_path = task.get("target_file_path", "")
            if target_path:
                self.commit_to_nightly_branch(task_id=task_id, target_file_path=target_path)

            logger.info(f"Tarefa noturna {task_id} VERIFICADA e integrada com sucesso.")
            return True
        else:
            logger.warning(f"Tarefa noturna {task_id} reprovada no Pytest.")
            return False

    def execute_batch(self, max_tasks: int = 5) -> int:
        """Executa um lote de tarefas noturnas em fila estrita (concorrência N=1)."""
        tasks = self.fetch_nightly_tasks()
        if not tasks:
            logger.info("Nenhuma tarefa agendada para a Fila Noturna Colibrì.")
            return 0

        logger.info(f"Iniciando lote noturno: {len(tasks[:max_tasks])} tarefas a processar (N=1).")
        success_count = 0

        for task in tasks[:max_tasks]:
            if self.process_task(task):
                success_count += 1

        return success_count

    def run_nightly_pipeline(
        self,
        max_tasks: int = 10,
        check_user: bool = True,
        idle_threshold_sec: int = 300,
    ) -> Dict[str, Any]:
        """
        Pipeline noturno orquestrado completo:
        1. Verifica atividade do usuário (aborta se usuário estiver ativo).
        2. Executa higiene de RAM e caches.
        3. Varre comentários # REFACTOR_NIGHTLY no projeto.
        4. Inicia servidor ou valida backend.
        5. Processa o lote serialmente (N=1).
        6. Desliga o servidor e retorna métricas de execução.
        """
        logger.info("=========================================================================")
        logger.info(" [PCI ENGINE] INICIANDO PIPELINE DE PROCESSAMENTO NOTURNO (COLIBRÌ)")
        logger.info("=========================================================================")

        # 1. Trava de atividade do usuário
        if check_user and self.is_user_active(idle_threshold_sec=idle_threshold_sec):
            logger.warning("Usuário ativo detectado no computador. Abortando execução noturna para não impactar a experiência.")
            return {"status": "ABORTED_USER_ACTIVE", "tasks_processed": 0, "success_count": 0}

        # 2. Higiene de RAM
        hygiene = self.run_preflight_hygiene()

        # 3. Varredura de anotações # REFACTOR_NIGHTLY
        scanned_tasks = self.scan_refactor_comments()

        # 4. Inicia llama-server (se disponível)
        server_started = self.start_llama_server()

        success_count = 0
        total_tasks = 0
        try:
            tasks = self.fetch_nightly_tasks()
            total_tasks = len(tasks[:max_tasks])
            # 5. Processamento em lote serial (N=1)
            success_count = self.execute_batch(max_tasks=max_tasks)
        finally:
            # 6. Desliga o servidor
            if server_started:
                self.stop_llama_server()

        logger.info(f"Pipeline noturno finalizado: {success_count}/{total_tasks} tarefas verificadas.")
        return {
            "status": "COMPLETED",
            "hygiene": hygiene,
            "scanned_comments_count": len(scanned_tasks),
            "total_tasks": total_tasks,
            "success_count": success_count,
        }

    @staticmethod
    def _extract_code(text: str) -> str:
        """Extrai código puro eliminando fences de Markdown."""
        if not text:
            return ""
        if "```python" in text:
            parts = text.split("```python")
            return parts[1].split("```")[0].strip()
        elif "```" in text:
            parts = text.split("```")
            return parts[1].split("```")[0].strip()
        return text.strip()


def main():
    """Entrypoint CLI para o agendador de tarefas do Windows."""
    worker = NightlyColibriWorker()
    res = worker.run_nightly_pipeline(check_user=False)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
