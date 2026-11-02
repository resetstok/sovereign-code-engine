"""
Módulo: pci_engine/nightly_colibri.py
Worker em lote para a madrugada (Método Colibrì via llama-server --mmap 1).
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pci_engine.db import JobsDatabaseV2
from pci_engine.router import PCIRouterAndBroker

logger = logging.getLogger("pci_engine.nightly_colibri")


class NightlyColibriWorker:
    """
    Executa tarefas pesadas (REFACTOR, >120 linhas, modelos 14B/32B com mmap)
    de madrugada enquanto o usuário não utiliza o computador interativamente.
    """

    def __init__(self, db_path: Path | str = "data/jobs.db", repo_root: Path | str = "."):
        self.db_path = Path(db_path).resolve()
        self.repo_root = Path(repo_root).resolve()
        self.db = JobsDatabaseV2(db_path=self.db_path)
        self.router = PCIRouterAndBroker(db_path=self.db_path)

    def fetch_nightly_tasks(self) -> List[Dict[str, Any]]:
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

    def execute_batch(self, max_tasks: int = 5) -> int:
        tasks = self.fetch_nightly_tasks()
        if not tasks:
            logger.info("Nenhuma tarefa agendada para a Fila Noturna Colibrì.")
            return 0

        logger.info(f"Iniciando processamento noturno: {len(tasks[:max_tasks])} tarefas na esteira.")
        processed = 0

        for task in tasks[:max_tasks]:
            task_id = task["task_id"]
            logger.info(f"Processando tarefa noturna: {task_id} [{task['task_type']}]")

            # Em execução real de produção, conecta via HTTP no llama-server --mmap 1
            # Aqui simulamos o wrapper de execução segura e verificação
            t0 = time.perf_counter()
            # Placeholder estrutural: o modelo executa e produz candidate_code
            candidate_code = f"# Refatoração noturna otimizada\n# Task: {task_id}\n"
            compute_sec = round(time.perf_counter() - t0, 2)

            # Valida com pytest no sandbox
            verified = self.router.verify_and_integrate_submission(
                task_id=task_id,
                node_id="MASTER_NIGHTLY_COLIBRI",
                candidate_code=candidate_code,
                compute_sec=compute_sec,
            )

            if verified:
                processed += 1
                logger.info(f"Tarefa noturna concluída e verificada com sucesso: {task_id}")
            else:
                logger.warning(f"Tarefa noturna reprovada no Pytest: {task_id}")

        return processed
