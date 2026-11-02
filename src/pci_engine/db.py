"""
Módulo: pci_engine/db.py
Gerenciador SQLite do Broker v2.0 com modo WAL e integridade transacional.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pci_engine.db")

SCHEMA_V2_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1. NÓS E CAPACIDADES MEDIDAS (Master + Doadores)
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    node_name TEXT NOT NULL,
    role TEXT CHECK(role IN ('MASTER', 'DONOR')) NOT NULL,
    ram_total_mb INTEGER NOT NULL,
    vram_total_mb INTEGER NOT NULL,
    ssd_free_gb REAL NOT NULL,
    supported_models TEXT NOT NULL,
    kudos_balance REAL DEFAULT 0.0,
    last_heartbeat DATETIME,
    status TEXT CHECK(status IN ('ONLINE', 'BUSY', 'OFFLINE', 'BLOCKED')) DEFAULT 'OFFLINE'
);

-- 2. MATRIZ DE DESEMPENHO MEDIDA (Meta 86% - 96%)
CREATE TABLE IF NOT EXISTS capability_matrix (
    node_id TEXT REFERENCES nodes(node_id),
    model_name TEXT NOT NULL,
    task_type TEXT CHECK(task_type IN ('EXTRACTION', 'CRUD', 'REFACTOR', 'PARSER_OSINT', 'ARCHITECTURE')) NOT NULL,
    sample_size INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0.0,
    avg_tokens_per_sec REAL DEFAULT 0.0,
    verification_state TEXT CHECK(verification_state IN ('ESTIMATED', 'MEASURED', 'VERIFIED')) DEFAULT 'ESTIMATED',
    benchmark_version TEXT NOT NULL,
    PRIMARY KEY (node_id, model_name, task_type)
);

-- 3. FILA ATÔMICA DE TAREFAS (DAG + Pull with Lease)
CREATE TABLE IF NOT EXISTS tasks_queue (
    task_id TEXT PRIMARY KEY,
    parent_masterbook TEXT NOT NULL,
    depends_on_task_id TEXT REFERENCES tasks_queue(task_id),
    task_type TEXT NOT NULL,
    privacy_level TEXT CHECK(privacy_level IN ('PUBLIC', 'SANITIZED_DERIVATIVE', 'STRICT_LOCAL')) NOT NULL,
    permission_level INTEGER CHECK(permission_level BETWEEN 1 AND 4) DEFAULT 2,
    estimated_lines INTEGER NOT NULL,
    spec_markdown TEXT NOT NULL,
    test_code TEXT NOT NULL,
    target_file_path TEXT NOT NULL,
    status TEXT CHECK(status IN (
        'PENDING', 'WAITING_DEPS', 'LEASED', 'NIGHTLY_BATCH', 
        'VALIDATING', 'VERIFIED', 'FAILED_RETRY', 'BLOCKED_HUMAN_REVIEW', 'BLOCKED_EXTERNAL'
    )) DEFAULT 'PENDING',
    assigned_route TEXT,
    leased_by_node TEXT REFERENCES nodes(node_id),
    lease_expires_at DATETIME,
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    candidate_patch_diff TEXT,
    last_error_log TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

-- 4. LIVRO-RAZÃO DE KUDOS (Auditoria de Contribuição de Doadores)
CREATE TABLE IF NOT EXISTS kudos_ledger (
    tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT REFERENCES nodes(node_id),
    task_id TEXT REFERENCES tasks_queue(task_id),
    kudos_awarded REAL NOT NULL,
    compute_seconds REAL NOT NULL,
    awarded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 5. CADEIA DE CUSTÓDIA E SANITIZAÇÃO (OSINT / Forense)
CREATE TABLE IF NOT EXISTS evidence_manifest (
    evidence_id TEXT PRIMARY KEY,
    original_sha256 TEXT NOT NULL,
    source_url TEXT NOT NULL,
    acquired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    custody_status TEXT CHECK(custody_status IN ('FULL_BYTES_STORED', 'HASH_ONLY')) NOT NULL,
    derivative_sha256 TEXT,
    metadata_stripped BOOLEAN DEFAULT 0,
    stripped_categories TEXT,
    tool_version TEXT,
    drive_sync_status TEXT CHECK(drive_sync_status IN ('LOCAL_SPOOL', 'UPLOADED_VERIFIED')) DEFAULT 'LOCAL_SPOOL'
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks_queue(status);
CREATE INDEX IF NOT EXISTS idx_tasks_deps ON tasks_queue(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
"""


class JobsDatabaseV2:
    """Interface SQLite otimizada com WAL mode e transações atômicas."""

    def __init__(self, db_path: Path | str = "data/jobs.db"):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_V2_SQL)
            conn.commit()
