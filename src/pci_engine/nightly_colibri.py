"""
Módulo: pci_engine/nightly_colibri.py
Processamento Noturno em Lote (Método Colibrì via llama-server --mmap 1).
Executa tarefas pesadas (REFACTOR, >120 linhas, modelos 14B Q4_K_M) na madrugada
sem estourar os 8GB de RAM física, paginando pesos do modelo diretamente do NVMe SSD.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

from pci_engine.database import DatabaseManager
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
        """Envia o prompt para o servidor local llama-server ou Ollama."""
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
        with opener.open(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("content", "")

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
        try:
            raw_output = self._call_inference_engine(prompt)
            candidate_code = self._extract_code(raw_output)
        except Exception as exc:
            logger.warning(f"Inference engine offline ou erro ({exc}). Tentando fallback estruturado.")
            candidate_code = f"# Fallback noturno: {task_id}\ndef execute():\n    return True\n"

        compute_sec = round(time.perf_counter() - t0, 2)

        # Valida candidate_code no sandbox Git Worktree (nanocoder pattern)
        verified = self.router.verify_and_integrate_submission(
            task_id=task_id,
            node_id="MASTER_NIGHTLY_COLIBRI",
            candidate_code=candidate_code,
            compute_sec=compute_sec,
        )

        if verified:
            self.db.update_task_status(task_id, status="VERIFIED", candidate_patch_diff=candidate_code)
            self.db.unlock_ready_tasks()
            logger.info(f"Tarefa noturna {task_id} VERIFICADA e integrada com sucesso.")
            return True
        else:
            logger.warning(f"Tarefa noturna {task_id} reprovada no Pytest.")
            return False

    def execute_batch(self, max_tasks: int = 5) -> int:
        """Executa um lote de tarefas noturnas e retorna o total de sucessos."""
        tasks = self.fetch_nightly_tasks()
        if not tasks:
            logger.info("Nenhuma tarefa agendada para a Fila Noturna Colibrì.")
            return 0

        logger.info(f"Iniciando lote noturno: {len(tasks[:max_tasks])} tarefas a processar.")
        success_count = 0

        for task in tasks[:max_tasks]:
            if self.process_task(task):
                success_count += 1

        return success_count

    @staticmethod
    def _extract_code(text: str) -> str:
        if "```python" in text:
            parts = text.split("```python")
            return parts[1].split("```")[0].strip()
        elif "```" in text:
            parts = text.split("```")
            return parts[1].split("```")[0].strip()
        return text.strip()
