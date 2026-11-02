"""
Módulo: pci_engine/router.py
Roteador Cognitivo, Broker com Pull & Lease e Validação Isolada via Git Worktree (Entregável 2 / v2.1).
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import psutil

from pci_engine.db import JobsDatabaseV2
from pci_engine.preflight import PreflightChecker

logger = logging.getLogger("pci_engine.router")


class PCIRouterAndBroker:
    """
    Roteador Cognitivo e Broker de Tarefas da arquitetura v2.1.
    Coordena decisões de envio (Local 3B, Cloud API, Fila Noturna e Doador),
    mecanismo de Pull com Lease e verificação em sandbox via Git Worktree (nanocoder pattern).
    """

    def __init__(self, db_path: Path | str = "data/jobs.db", repo_root: Path | str = "."):
        self.db_path = Path(db_path).resolve()
        self.repo_root = Path(repo_root).resolve()
        self.db = JobsDatabaseV2(db_path=self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        return self.db.get_connection()

    # 1. PREFLIGHT E CONTENÇÃO DE HARDWARE
    def check_master_health(self) -> Dict[str, Any]:
        health = PreflightChecker.check_health(self.db_path.parent)
        if health["ssd_critical"]:
            raise SystemError(
                f"BLOCKED_EXTERNAL: Espaço em SSD crítico ({health['ssd_free_gb']:.1f}GB < 40GB). Abortando fila."
            )
        return health

    # 2. RECUPERAÇÃO DE ÓRFÃOS (Pull with Lease Expiration)
    def reclaim_expired_leases(self) -> int:
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tasks_queue
                SET status = CASE 
                        WHEN (attempt_count + 1) >= max_attempts THEN 'BLOCKED_HUMAN_REVIEW'
                        ELSE 'PENDING'
                    END,
                    leased_by_node = NULL,
                    lease_expires_at = NULL,
                    attempt_count = attempt_count + 1,
                    last_error_log = 'Timeout de Lease: Nó doador não respondeu dentro da janela estipulada.'
                WHERE status = 'LEASED' AND lease_expires_at < ?
                """,
                (now,),
            )
            reclaimed = cursor.rowcount
            conn.commit()
            if reclaimed > 0:
                logger.warning(f"Leases expirados recuperados: {reclaimed}")
            return reclaimed

    # 3. ÁRVORE DE DECISÃO DE ROTEAMENTO
    def route_task(self, task: sqlite3.Row, api_quotas: Dict[str, Any], online_mode: bool) -> str:
        privacy = task["privacy_level"]
        lines = task["estimated_lines"]
        task_type = task["task_type"]
        attempts = task["attempt_count"]

        # Regra Zero: Limite de falhas atingido vai direto para revisão humana
        if attempts >= task["max_attempts"]:
            return "BLOCKED_HUMAN_REVIEW"

        # Regra 1: Dados estritamente locais NUNCA saem do Master PC
        if privacy == "STRICT_LOCAL":
            if lines <= 35 and attempts == 0:
                return "LOCAL_3B"
            return "NIGHTLY_14B"

        # Regra 2: Tarefas curtas e diretas (Micro-escopo) -> Ollama Local 3B
        if lines <= 30 and task_type in ("EXTRACTION", "CRUD") and attempts == 0:
            if self._is_model_qualified("MASTER_NODE", "qwen2.5-coder:3b", task_type, min_rate=0.86):
                return "LOCAL_3B"

        # Regra 3: Refatoração estrutural pesada sem urgência imediata -> Fila Noturna Colibrì
        if task_type == "REFACTOR" or lines > 120:
            return "NIGHTLY_14B"

        # Regra 4: Modo Online ativo + Tarefa complexa ou falha prévia -> API Gratuita
        if online_mode and privacy in ("PUBLIC", "SANITIZED_DERIVATIVE"):
            for provider in ("groq", "cerebras", "openrouter"):
                remaining = api_quotas.get(provider, {}).get("remaining_requests", 0)
                if remaining > 5:
                    return f"CLOUD_API:{provider}"

        # Regra 5: APIs esgotadas (ou modo Offline) -> Verifica Pool de Doadores
        if self._has_eligible_donor(task_type, min_rate=0.86):
            return "DONOR_POOL"

        # Fallback Seguro: agenda para a madrugada no disco via mmap
        return "NIGHTLY_14B"

    def _is_model_qualified(self, node_id: str, model: str, task_type: str, min_rate: float) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT success_rate, verification_state FROM capability_matrix
                WHERE node_id = ? AND model_name = ? AND task_type = ?
                """,
                (node_id, model, task_type),
            ).fetchone()
            if not row:
                return True
            return row["success_rate"] >= min_rate

    def _has_eligible_donor(self, task_type: str, min_rate: float) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT n.node_id FROM nodes n
                JOIN capability_matrix c ON n.node_id = c.node_id
                WHERE n.role = 'DONOR' AND n.status = 'ONLINE'
                  AND c.task_type = ? AND c.success_rate >= ?
                LIMIT 1
                """,
                (task_type, min_rate),
            ).fetchone()
            return row is not None

    # 4. PULL COM LEASE (Chamado pelo PC Doador via FastAPI/Tailscale)
    def lease_task_for_donor(self, donor_node_id: str, lease_minutes: int = 45) -> Optional[Dict[str, Any]]:
        self.reclaim_expired_leases()
        with self._get_conn() as conn:
            task = conn.execute(
                """
                SELECT * FROM tasks_queue t
                WHERE t.status = 'PENDING'
                  AND t.privacy_level IN ('PUBLIC', 'SANITIZED_DERIVATIVE')
                  AND (t.depends_on_task_id IS NULL OR EXISTS (
                      SELECT 1 FROM tasks_queue dep 
                      WHERE dep.task_id = t.depends_on_task_id AND dep.status = 'VERIFIED'
                  ))
                ORDER BY t.created_at ASC LIMIT 1
                """
            ).fetchone()

            if not task:
                return None

            expires = (datetime.now(timezone.utc) + timedelta(minutes=lease_minutes)).isoformat()
            conn.execute(
                """
                UPDATE tasks_queue
                SET status = 'LEASED', leased_by_node = ?, lease_expires_at = ?, assigned_route = 'DONOR_POOL'
                WHERE task_id = ?
                """,
                (donor_node_id, expires, task["task_id"]),
            )
            conn.commit()

            return {
                "task_id": task["task_id"],
                "spec_markdown": task["spec_markdown"],
                "test_code": task["test_code"],
                "target_filename": Path(task["target_file_path"]).name,
                "target_rel_path": task["target_file_path"],
                "lease_expires_at": expires,
            }

    # 5. VALIDAÇÃO ISOLADA NO MASTER (Git Worktree + Pytest Sandbox)
    def verify_and_integrate_submission(
        self, task_id: str, node_id: str, candidate_code: str, compute_sec: float
    ) -> bool:
        """
        O código vindo do Doador ou da IA nunca vai direto para a branch de trabalho.
        É testado em sandbox isolado via Git Worktree (nanocoder pattern) com timeout estrito.
        """
        with self._get_conn() as conn:
            task = conn.execute("SELECT * FROM tasks_queue WHERE task_id = ?", (task_id,)).fetchone()
            if not task or task["status"] not in ("LEASED", "PENDING", "VALIDATING"):
                return False

            success, stdout, stderr = self._run_in_worktree_or_fallback(
                task_id=task_id,
                target_file_path=task["target_file_path"],
                candidate_code=candidate_code,
                test_code=task["test_code"],
            )

            if success:
                kudos = round((task["estimated_lines"] * 0.5) + (compute_sec * 0.05), 2)
                conn.execute(
                    """
                    UPDATE tasks_queue 
                    SET status = 'VERIFIED', candidate_patch_diff = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                    """,
                    (candidate_code, task_id),
                )
                conn.execute(
                    "UPDATE nodes SET kudos_balance = kudos_balance + ? WHERE node_id = ?",
                    (kudos, node_id),
                )
                conn.execute(
                    """
                    INSERT INTO kudos_ledger (node_id, task_id, kudos_awarded, compute_seconds)
                    VALUES (?, ?, ?, ?)
                    """,
                    (node_id, task_id, kudos, compute_sec),
                )
                conn.commit()
                return True
            else:
                short_err = (stdout[-600:] if stdout else stderr[-600:]) or "Falha nos testes."
                new_status = (
                    "BLOCKED_HUMAN_REVIEW"
                    if (task["attempt_count"] + 1) >= task["max_attempts"]
                    else "PENDING"
                )
                conn.execute(
                    """
                    UPDATE tasks_queue
                    SET status = ?, leased_by_node = NULL, lease_expires_at = NULL,
                        attempt_count = attempt_count + 1, last_error_log = ?
                    WHERE task_id = ?
                    """,
                    (new_status, short_err, task_id),
                )
                conn.commit()
                return False

    def _run_in_worktree_or_fallback(
        self, task_id: str, target_file_path: str, candidate_code: str, test_code: str
    ) -> Tuple[bool, str, str]:
        """Tenta executar via Git Worktree destacada (nanocoder). Em falha, recorre a tempfile."""
        worktree_dir = (self.repo_root / "data" / "sandboxes" / f"sandbox_{task_id}").resolve()
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        # 1. Cria git worktree destacada
        cmd_add = ["git", "worktree", "add", "--detach", str(worktree_dir), "HEAD"]
        res_add = subprocess.run(cmd_add, cwd=str(self.repo_root), capture_output=True, text=True)

        if res_add.returncode != 0:
            # Fallback transparente para TemporaryDirectory
            return self._run_in_tempdir_fallback(target_file_path, candidate_code, test_code)

        try:
            target_path = worktree_dir / target_file_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(candidate_code, encoding="utf-8")

            test_file = worktree_dir / f"test_{target_path.name}"
            test_file.write_text(test_code, encoding="utf-8")

            res = subprocess.run(
                ["pytest", "-q", "--tb=short", "--disable-warnings", str(test_file)],
                cwd=str(worktree_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return (res.returncode == 0), res.stdout, res.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Timeout de 30s excedido no Pytest do worktree."
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_dir)],
                cwd=str(self.repo_root),
                capture_output=True,
            )

    def _run_in_tempdir_fallback(
        self, target_file_path: str, candidate_code: str, test_code: str
    ) -> Tuple[bool, str, str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            target_file = tmp_path / Path(target_file_path).name
            test_file = tmp_path / f"test_{target_file.name}"

            target_file.write_text(candidate_code, encoding="utf-8")
            test_file.write_text(test_code, encoding="utf-8")

            try:
                res = subprocess.run(
                    ["pytest", "-q", "--tb=short", "--disable-warnings", str(test_file)],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return (res.returncode == 0), res.stdout, res.stderr
            except subprocess.TimeoutExpired:
                return False, "", "Timeout de 30s excedido em sandbox temporário."
