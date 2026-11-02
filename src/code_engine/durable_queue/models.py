"""
Módulo: code_engine/durable_queue/models.py
Definições de modelos de dados, estados e contratos de jobs atômicos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CHECKPOINT = "CHECKPOINT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    job_id: str
    step_index: int
    step_name: str
    state_delta: Dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class JobRecord:
    job_id: str
    task_type: str
    entity_id: str
    status: JobStatus
    params: Dict[str, Any]
    payload_sha256: str
    retry_count: int = 0
    max_retries: int = 3
    worker_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_error: Optional[str] = None
