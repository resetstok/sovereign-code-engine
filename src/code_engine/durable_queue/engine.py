"""
Módulo: code_engine/durable_queue/engine.py
Mecanismo de Execução Resiliente com Checkpoints e Recuperação de Órfãos.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from code_engine.durable_queue.db import JobsDatabase
from code_engine.durable_queue.guardian import ResourceGuardian
from code_engine.durable_queue.models import CheckpointRecord, JobRecord, JobStatus

logger = logging.getLogger("code_engine.durable_queue.engine")


class JobExecutionContext:
    """Contexto de execução injetado no handler de cada job."""

    def __init__(self, job: JobRecord, db: JobsDatabase):
        self.job = job
        self.db = db
        self.checkpoints = db.get_checkpoints_for_job(job.job_id)

    def get_last_checkpoint(self) -> Optional[CheckpointRecord]:
        return self.checkpoints[-1] if self.checkpoints else None

    def save_checkpoint(self, step_index: int, step_name: str, delta: Dict[str, Any]) -> CheckpointRecord:
        cp_id = f"cp_{self.job.job_id}_{step_index}_{uuid.uuid4().hex[:6]}"
        cp = CheckpointRecord(
            checkpoint_id=cp_id,
            job_id=self.job.job_id,
            step_index=step_index,
            step_name=step_name,
            state_delta=delta,
        )
        self.db.insert_checkpoint(cp)
        self.db.update_job_status(self.job.job_id, JobStatus.CHECKPOINT)
        self.checkpoints.append(cp)
        logger.info(f"Checkpoint gravado: Job={self.job.job_id} | Step={step_index} ({step_name})")
        return cp


class DurableJobEngine:
    """Motor de execução com ciclo de vida garantido e concorrência estrita N=1."""

    def __init__(self, db: JobsDatabase, guardian: ResourceGuardian, worker_id: str = "master-worker"):
        self.db = db
        self.guardian = guardian
        self.worker_id = worker_id
        self.handlers: Dict[str, Callable[[JobRecord, JobExecutionContext], Dict[str, Any]]] = {}

    def register_handler(
        self, task_type: str, handler: Callable[[JobRecord, JobExecutionContext], Dict[str, Any]]
    ) -> None:
        self.handlers[task_type] = handler

    def submit_job(self, task_type: str, entity_id: str, params: Dict[str, Any]) -> Tuple[JobRecord, Dict[str, Any]]:
        payload_str = json.dumps({"task_type": task_type, "entity_id": entity_id, "params": params}, sort_keys=True)
        sha256 = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        existing = self.db.get_job_by_sha256(sha256)
        if existing:
            return existing, {"action": "EXISTING_RETURNED", "status": existing.status.value}

        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            entity_id=entity_id,
            status=JobStatus.PENDING,
            params=params,
            payload_sha256=sha256,
        )
        self.db.insert_job(job)
        return job, {"action": "CREATED", "status": JobStatus.PENDING.value}

    def recover_orphaned_jobs(self) -> List[JobRecord]:
        """Detecta jobs abandonados em RUNNING após reinício do host e devolve para RETRYING."""
        recovered = []
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT job_id FROM jobs WHERE status = 'RUNNING'").fetchall()
            for r in rows:
                job_id = r["job_id"]
                self.db.update_job_status(
                    job_id=job_id,
                    status=JobStatus.RETRYING,
                    last_error="Host reinicializado durante execução. Retomando do último checkpoint.",
                )
                recovered.append(self.db.get_job(job_id))
        return recovered

    def run_once(self, max_jobs: int = 1) -> int:
        """Executa estritamente N jobs (default 1 para contenção de hardware)."""
        processed_count = 0

        for _ in range(max_jobs):
            preflight = self.guardian.preflight_check()
            if not preflight["passed"]:
                logger.warning(f"Pré-voo reprovado. Abortando despacho: {preflight}")
                break

            job = self.db.claim_next_job(worker_id=self.worker_id)
            if not job:
                break

            handler = self.handlers.get(job.task_type)
            if not handler:
                err_msg = f"Nenhum handler registrado para task_type: {job.task_type}"
                self.db.update_job_status(job.job_id, JobStatus.FAILED, last_error=err_msg)
                continue

            ctx = JobExecutionContext(job=job, db=self.db)
            try:
                result = handler(job, ctx)
                self.db.update_job_status(job.job_id, JobStatus.COMPLETED)
                processed_count += 1
            except Exception as exc:
                logger.error(f"Falha na execução do job {job.job_id}: {exc}", exc_info=True)
                new_retry = job.retry_count + 1
                if new_retry < job.max_retries:
                    with self.db.get_connection() as conn:
                        conn.execute("UPDATE jobs SET retry_count = ? WHERE job_id = ?", (new_retry, job.job_id))
                    self.db.update_job_status(job.job_id, JobStatus.RETRYING, last_error=str(exc))
                else:
                    self.db.update_job_status(job.job_id, JobStatus.FAILED, last_error=str(exc))

        return processed_count
