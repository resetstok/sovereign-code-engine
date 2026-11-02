"""
Módulo: code_engine/durable_queue/db.py
Camada SQLite com suporte a transações atômicas, WAL e isolamento estrito.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from code_engine.durable_queue.models import CheckpointRecord, JobRecord, JobStatus

logger = logging.getLogger("code_engine.durable_queue.db")


class JobsDatabase:
    """Gerenciador SQLite da fila durável."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    params TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL UNIQUE,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    worker_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    step_name TEXT NOT NULL,
                    state_delta TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_task_type ON jobs(task_type);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_job ON checkpoints(job_id, step_index);
                """
            )

    def insert_job(self, job: JobRecord) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, task_type, entity_id, status, params, payload_sha256,
                    retry_count, max_retries, worker_id, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.task_type,
                    job.entity_id,
                    job.status.value,
                    json.dumps(job.params),
                    job.payload_sha256,
                    job.retry_count,
                    job.max_retries,
                    job.worker_id,
                    job.created_at,
                    job.updated_at,
                    job.last_error,
                ),
            )

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    def get_job_by_sha256(self, sha256: str) -> Optional[JobRecord]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE payload_sha256 = ?", (sha256,)).fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    def update_job_status(
        self, job_id: str, status: JobStatus, worker_id: Optional[str] = None, last_error: Optional[str] = None
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = COALESCE(?, worker_id), last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (status.value, worker_id, last_error, job_id),
            )

    def claim_next_job(self, worker_id: str) -> Optional[JobRecord]:
        """Reivindica atomicamente o próximo job PENDING ou RETRYING."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT job_id FROM jobs
                WHERE status IN ('PENDING', 'RETRYING')
                ORDER BY created_at ASC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                return None

            job_id = row["job_id"]
            cursor.execute(
                """
                UPDATE jobs
                SET status = 'RUNNING', worker_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status IN ('PENDING', 'RETRYING')
                """,
                (worker_id, job_id),
            )
            if cursor.rowcount == 0:
                return None

            return self.get_job(job_id)

    def insert_checkpoint(self, cp: CheckpointRecord) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (checkpoint_id, job_id, step_index, step_name, state_delta, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cp.checkpoint_id, cp.job_id, cp.step_index, cp.step_name, json.dumps(cp.state_delta), cp.created_at),
            )

    def get_checkpoints_for_job(self, job_id: str) -> List[CheckpointRecord]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE job_id = ? ORDER BY step_index ASC", (job_id,)
            ).fetchall()
            return [
                CheckpointRecord(
                    checkpoint_id=r["checkpoint_id"],
                    job_id=r["job_id"],
                    step_index=r["step_index"],
                    step_name=r["step_name"],
                    state_delta=json.loads(r["state_delta"]),
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def _row_to_job(self, r: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=r["job_id"],
            task_type=r["task_type"],
            entity_id=r["entity_id"],
            status=JobStatus(r["status"]),
            params=json.loads(r["params"]),
            payload_sha256=r["payload_sha256"],
            retry_count=r["retry_count"],
            max_retries=r["max_retries"],
            worker_id=r["worker_id"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            last_error=r["last_error"],
        )
