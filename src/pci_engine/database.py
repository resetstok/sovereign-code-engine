"""
Módulo: pci_engine/database.py
Gerenciador SQLite da Fundação PCI Master System v2.0 com modo WAL e integridade referencial.
Implementa as 5 tabelas da especificação consolidada e métodos CRUD atômicos.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pci_engine.database")

SCHEMA_SQL = """
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


class DatabaseManager:
    """Gerenciador de banco de dados SQLite WAL do PCI Master System v2.0."""

    def __init__(self, db_path: Path | str = "data/jobs.db"):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Abre conexão com isolamento estrito, modo WAL e suporte a chaves estrangeiras."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self) -> None:
        """Inicializa todo o esquema de 5 tabelas da v2.0."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    # -------------------------------------------------------------------------
    # CRUD: 1. NÓS (Nodes)
    # -------------------------------------------------------------------------
    def register_node(
        self,
        node_id: str,
        node_name: str,
        role: str,
        ram_total_mb: int,
        vram_total_mb: int,
        ssd_free_gb: float,
        supported_models: List[str],
        status: str = "ONLINE",
    ) -> None:
        with self.get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO nodes (
                    node_id, node_name, role, ram_total_mb, vram_total_mb,
                    ssd_free_gb, supported_models, status, last_heartbeat
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    node_name = excluded.node_name,
                    role = excluded.role,
                    ram_total_mb = excluded.ram_total_mb,
                    vram_total_mb = excluded.vram_total_mb,
                    ssd_free_gb = excluded.ssd_free_gb,
                    supported_models = excluded.supported_models,
                    status = excluded.status,
                    last_heartbeat = excluded.last_heartbeat
                """,
                (
                    node_id,
                    node_name,
                    role,
                    ram_total_mb,
                    vram_total_mb,
                    ssd_free_gb,
                    json.dumps(supported_models),
                    status,
                    now,
                ),
            )
            conn.commit()

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            res["supported_models"] = json.loads(res["supported_models"])
            return res

    def list_nodes(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            if status:
                rows = conn.execute("SELECT * FROM nodes WHERE status = ?", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM nodes").fetchall()
            result = []
            for r in rows:
                item = dict(r)
                item["supported_models"] = json.loads(item["supported_models"])
                result.append(item)
            return result

    # -------------------------------------------------------------------------
    # CRUD: 2. MATRIZ DE DESEMPENHO MEDIDA (Capability Matrix)
    # -------------------------------------------------------------------------
    def record_capability(
        self,
        node_id: str,
        model_name: str,
        task_type: str,
        sample_size: int,
        success_rate: float,
        avg_tokens_per_sec: float = 0.0,
        verification_state: str = "ESTIMATED",
        benchmark_version: str = "v2.0",
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO capability_matrix (
                    node_id, model_name, task_type, sample_size,
                    success_rate, avg_tokens_per_sec, verification_state, benchmark_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id, model_name, task_type) DO UPDATE SET
                    sample_size = excluded.sample_size,
                    success_rate = excluded.success_rate,
                    avg_tokens_per_sec = excluded.avg_tokens_per_sec,
                    verification_state = excluded.verification_state,
                    benchmark_version = excluded.benchmark_version
                """,
                (
                    node_id,
                    model_name,
                    task_type,
                    sample_size,
                    success_rate,
                    avg_tokens_per_sec,
                    verification_state,
                    benchmark_version,
                ),
            )
            conn.commit()

    def get_capability(self, node_id: str, model_name: str, task_type: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM capability_matrix
                WHERE node_id = ? AND model_name = ? AND task_type = ?
                """,
                (node_id, model_name, task_type),
            ).fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------------------------
    # CRUD: 3. FILA ATÔMICA DE TAREFAS (Tasks Queue)
    # -------------------------------------------------------------------------
    def insert_task(
        self,
        task_id: str,
        parent_masterbook: str,
        task_type: str,
        privacy_level: str,
        estimated_lines: int,
        spec_markdown: str,
        test_code: str,
        target_file_path: str,
        depends_on_task_id: Optional[str] = None,
        permission_level: int = 2,
        status: str = "PENDING",
        max_attempts: int = 3,
    ) -> str:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks_queue (
                    task_id, parent_masterbook, depends_on_task_id, task_type,
                    privacy_level, permission_level, estimated_lines,
                    spec_markdown, test_code, target_file_path, status, max_attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    parent_masterbook = excluded.parent_masterbook,
                    depends_on_task_id = excluded.depends_on_task_id,
                    task_type = excluded.task_type,
                    privacy_level = excluded.privacy_level,
                    permission_level = excluded.permission_level,
                    estimated_lines = excluded.estimated_lines,
                    spec_markdown = excluded.spec_markdown,
                    test_code = excluded.test_code,
                    target_file_path = excluded.target_file_path,
                    status = excluded.status,
                    max_attempts = excluded.max_attempts
                """,
                (
                    task_id,
                    parent_masterbook,
                    depends_on_task_id,
                    task_type,
                    privacy_level,
                    permission_level,
                    estimated_lines,
                    spec_markdown,
                    test_code,
                    target_file_path,
                    status,
                    max_attempts,
                ),
            )
            conn.commit()
            return task_id

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM tasks_queue WHERE task_id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            if status:
                rows = conn.execute("SELECT * FROM tasks_queue WHERE status = ? ORDER BY created_at ASC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks_queue ORDER BY created_at ASC").fetchall()
            return [dict(r) for r in rows]

    def update_task_status(
        self,
        task_id: str,
        status: str,
        assigned_route: Optional[str] = None,
        leased_by_node: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
        candidate_patch_diff: Optional[str] = None,
        last_error_log: Optional[str] = None,
    ) -> None:
        with self.get_connection() as conn:
            completed_clause = ", completed_at = CURRENT_TIMESTAMP" if status == "VERIFIED" else ""
            conn.execute(
                f"""
                UPDATE tasks_queue
                SET status = ?,
                    assigned_route = COALESCE(?, assigned_route),
                    leased_by_node = COALESCE(?, leased_by_node),
                    lease_expires_at = COALESCE(?, lease_expires_at),
                    candidate_patch_diff = COALESCE(?, candidate_patch_diff),
                    last_error_log = COALESCE(?, last_error_log)
                    {completed_clause}
                WHERE task_id = ?
                """,
                (
                    status,
                    assigned_route,
                    leased_by_node,
                    lease_expires_at,
                    candidate_patch_diff,
                    last_error_log,
                    task_id,
                ),
            )
            conn.commit()

    def unlock_ready_tasks(self) -> int:
        """
        Promove tarefas de 'WAITING_DEPS' para 'PENDING' cujas dependências
        já estejam com status 'VERIFIED'.
        """
        with self.get_connection() as conn:
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

    # -------------------------------------------------------------------------
    # CRUD: 4. LIVRO-RAZÃO DE KUDOS (Kudos Ledger)
    # -------------------------------------------------------------------------
    def record_kudos(self, node_id: str, task_id: str, kudos_awarded: float, compute_seconds: float) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO kudos_ledger (node_id, task_id, kudos_awarded, compute_seconds)
                VALUES (?, ?, ?, ?)
                """,
                (node_id, task_id, kudos_awarded, compute_seconds),
            )
            tx_id = cursor.lastrowid
            conn.execute(
                "UPDATE nodes SET kudos_balance = kudos_balance + ? WHERE node_id = ?",
                (kudos_awarded, node_id),
            )
            conn.commit()
            return tx_id

    def get_kudos_ledger_for_node(self, node_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM kudos_ledger WHERE node_id = ? ORDER BY tx_id DESC", (node_id,)).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # CRUD: 5. CADEIA DE CUSTÓDIA E SANITIZAÇÃO (Evidence Manifest)
    # -------------------------------------------------------------------------
    def register_evidence(
        self,
        evidence_id: str,
        original_sha256: str,
        source_url: str,
        custody_status: str,
        derivative_sha256: Optional[str] = None,
        metadata_stripped: bool = False,
        stripped_categories: Optional[str] = None,
        tool_version: str = "PCI-Sanitizer-v2.0",
        drive_sync_status: str = "LOCAL_SPOOL",
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO evidence_manifest (
                    evidence_id, original_sha256, source_url, custody_status,
                    derivative_sha256, metadata_stripped, stripped_categories,
                    tool_version, drive_sync_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    original_sha256 = excluded.original_sha256,
                    source_url = excluded.source_url,
                    custody_status = excluded.custody_status,
                    derivative_sha256 = excluded.derivative_sha256,
                    metadata_stripped = excluded.metadata_stripped,
                    stripped_categories = excluded.stripped_categories,
                    tool_version = excluded.tool_version,
                    drive_sync_status = excluded.drive_sync_status
                """,
                (
                    evidence_id,
                    original_sha256,
                    source_url,
                    custody_status,
                    derivative_sha256,
                    1 if metadata_stripped else 0,
                    stripped_categories,
                    tool_version,
                    drive_sync_status,
                ),
            )
            conn.commit()

    def get_evidence(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM evidence_manifest WHERE evidence_id = ?", (evidence_id,)).fetchone()
            return dict(row) if row else None
