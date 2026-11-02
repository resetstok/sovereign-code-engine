"""
Módulo: pci_engine/orchestrator.py
Decompositor de Masterbook em DAG de microtarefas e despachante da fila v2.0.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from pci_engine.db import JobsDatabaseV2
from pci_engine.router import PCIRouterAndBroker

logger = logging.getLogger("pci_engine.orchestrator")


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


class MasterbookOrchestrator:
    """
    Orquestrador v2.0 que ingere um Masterbook, quebra em DAG atômica
    e alimenta a tabela tasks_queue do jobs.db.
    """

    def __init__(self, db_path: Path | str = "data/jobs.db"):
        self.db_path = Path(db_path).resolve()
        self.db = JobsDatabaseV2(db_path=self.db_path)
        self.router = PCIRouterAndBroker(db_path=self.db_path)

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
        """Promove tarefas WAITING_DEPS para PENDING assim que o pai for VERIFIED."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tasks_queue
                SET status = 'PENDING'
                WHERE status = 'WAITING_DEPS'
                  AND depends_on_task_id IN (
                      SELECT task_id FROM tasks_queue WHERE status = 'VERIFIED'
                  )
                """
            )
            promoted = cursor.rowcount
            conn.commit()
            return promoted

    def dispatch_next_cycle(self, api_quotas: Dict[str, Any], online_mode: bool = False) -> Optional[Dict[str, Any]]:
        """Processa o próximo passo do ciclo determinístico."""
        self.update_dag_dependencies()
        self.router.reclaim_expired_leases()

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

            route = self.router.route_task(task, api_quotas=api_quotas, online_mode=online_mode)
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
