"""
Módulo: pci_engine/router.py
Roteador Cognitivo e Broker de Tarefas (PCIRouter) da Arquitetura PCI Master System v2.0.
Implementa a árvore de decisão determinística, recuperação de Leases expirados
e validação em sandbox isolado com Git Worktree (nanocoder pattern).
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import psutil

from pci_engine.database import DatabaseManager
from pci_engine.runtime_utils import get_python_runtime, get_subprocess_creation_flags
from pci_engine.preflight import PreflightChecker

logger = logging.getLogger("pci_engine.router")


class PCIRouter:
    """
    Roteador Cognitivo e Gestor de Leases do PCI Engine.
    Decide o roteamento determinístico entre Ollama Local 3B, APIs Gratuitas,
    Fila Noturna Colibrì e Pool de Doadores via Tailscale.
    """

    def __init__(self, db_path: Path | str = "data/jobs.db", repo_root: Path | str = "."):
        self.db_path = Path(db_path).resolve()
        self.repo_root = Path(repo_root).resolve()
        self.db = DatabaseManager(db_path=self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        return self.db.get_connection()

    # -------------------------------------------------------------------------
    # 1. Recuperação de Leases Expirados (Reclaim Expired Leases)
    # -------------------------------------------------------------------------
    def reclaim_expired_leases(self) -> int:
        """
        Devolve tarefas travadas em nós doadores com lease vencido para PENDING
        (ou BLOCKED_HUMAN_REVIEW se atingir max_attempts), incrementando attempt_count.
        """
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

    # -------------------------------------------------------------------------
    # 2. Árvore de Decisão Determinística de Roteamento
    # -------------------------------------------------------------------------
    def route_task(
        self,
        task: sqlite3.Row | Dict[str, Any],
        api_quotas: Dict[str, Any],
        online_mode: bool = False,
    ) -> str:
        """
        Árvore de decisão determinística de roteamento:
        1. Se attempt_count >= max_attempts -> BLOCKED_HUMAN_REVIEW
        2. Se privacy_level == 'STRICT_LOCAL' -> nunca sai do Master PC (LOCAL_3B se <= 35 linhas, senão NIGHTLY_14B)
        3. Se estimated_lines <= 30 e taxa na capability_matrix >= 0.86 -> LOCAL_3B
        4. Se task_type == 'REFACTOR' ou > 120 linhas -> NIGHTLY_14B
        5. Se Modo Online ativo e cota em APIs gratuitas (Groq, Cerebras, OpenRouter) e tarefa permite -> CLOUD_API:<provider>
        6. Se APIs esgotadas ou Modo Offline, mas houver Doador ONLINE qualificado (>= 0.86) -> DONOR_POOL
        7. Fallback seguro (sem envio invisível para nuvem) -> NIGHTLY_14B
        """
        privacy = task["privacy_level"]
        lines = task["estimated_lines"]
        task_type = task["task_type"]
        attempts = task["attempt_count"]
        max_attempts = task.get("max_attempts", 3) if isinstance(task, dict) else task["max_attempts"]

        # Ramo 1: Limite de falhas atingido exige revisão humana
        if attempts >= max_attempts:
            return "BLOCKED_HUMAN_REVIEW"

        # Ramo 2: Dados estritamente locais NUNCA saem do Master PC (nem nuvem, nem doadores)
        if privacy == "STRICT_LOCAL":
            if lines <= 35 and attempts == 0:
                return "LOCAL_3B"
            return "NIGHTLY_14B"

        # Ramo 3: Refatoração pesada ou mais de 120 linhas -> Fila Noturna Colibrì (3B não faz refactor)
        if task_type == "REFACTOR" or lines > 120:
            return "NIGHTLY_14B"

        # Ramo 4: Microtarefa leve (<= 30 linhas) com histórico satisfatório (>= 86%) -> Local 3B
        if lines <= 30 and attempts == 0:
            if self._is_model_qualified("MASTER_NODE", "qwen2.5-coder:3b", task_type, min_rate=0.86):
                return "LOCAL_3B"

        # Ramo 5: Modo Online ativo + tarefa pública/higienizada -> APIs Gratuitas
        if online_mode and privacy in ("PUBLIC", "SANITIZED_DERIVATIVE"):
            for provider in ("groq", "cerebras", "openrouter"):
                quota = api_quotas.get(provider, {})
                remaining = quota.get("remaining_requests", 0)
                if remaining > 5:
                    return f"CLOUD_API:{provider}"

        # Ramo 6: APIs esgotadas ou Modo Offline -> Pool de Doadores qualificados via Tailscale
        if self._has_eligible_donor(task_type, min_rate=0.86):
            return "DONOR_POOL"

        # Ramo 7: Fallback Seguro (sem vazamento de dados) -> Fila Noturna Colibrì
        return "NIGHTLY_14B"

    def route_all_pending_tasks(
        self,
        api_quotas: Optional[Dict[str, Any]] = None,
        online_mode: bool = False,
    ) -> int:
        """
        Varre todas as tarefas com status 'PENDING' que ainda não possuem 'assigned_route'
        (ou com rota desatualizada) e aplica a árvore de decisão determinística.
        Salva a rota atribuída no banco de dados e retorna o total de tarefas roteadas.
        """
        if api_quotas is None:
            api_quotas = {}
            config_path = self.repo_root / "config" / "routing_policy.json"
            if config_path.is_file():
                try:
                    import json
                    policy = json.loads(config_path.read_text(encoding="utf-8"))
                    api_quotas = policy.get("cloud_api_quotas", {})
                except Exception:
                    pass

        routed_count = 0
        with self._get_conn() as conn:
            tasks = conn.execute(
                """
                SELECT * FROM tasks_queue
                WHERE status = 'PENDING' AND (assigned_route IS NULL OR assigned_route = '')
                ORDER BY created_at ASC
                """
            ).fetchall()

            for task_row in tasks:
                task_dict = dict(task_row)
                chosen_route = self.route_task(task_dict, api_quotas=api_quotas, online_mode=online_mode)
                conn.execute(
                    "UPDATE tasks_queue SET assigned_route = ? WHERE task_id = ?",
                    (chosen_route, task_dict["task_id"]),
                )
                routed_count += 1
            conn.commit()

        return routed_count

    def _is_model_qualified(self, node_id: str, model: str, task_type: str, min_rate: float = 0.86) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT success_rate, verification_state FROM capability_matrix
                WHERE node_id = ? AND model_name = ? AND task_type = ?
                """,
                (node_id, model, task_type),
            ).fetchone()
            if not row:
                return True  # Calibração inicial permite execução
            return row["success_rate"] >= min_rate

    def _has_eligible_donor(self, task_type: str, min_rate: float = 0.86) -> bool:
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

    # -------------------------------------------------------------------------
    # 3. Pull com Lease para Doadores
    # -------------------------------------------------------------------------
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

    # -------------------------------------------------------------------------
    # 4. Sandbox e Validação de Submissões (Git Worktree)
    # -------------------------------------------------------------------------
    def verify_and_integrate_submission(
        self, task_id: str, node_id: str, candidate_code: str, compute_sec: float
    ) -> bool:
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
        worktree_dir = (self.repo_root / "data" / "sandboxes" / f"sandbox_{task_id}").resolve()
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        cmd_add = ["git", "worktree", "add", "--detach", str(worktree_dir), "HEAD"]
        res_add = subprocess.run(cmd_add, cwd=str(self.repo_root), capture_output=True, text=True)

        if res_add.returncode != 0:
            return self._run_in_tempdir_fallback(target_file_path, candidate_code, test_code)

        try:
            target_path = worktree_dir / target_file_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(candidate_code, encoding="utf-8")

            test_file = worktree_dir / f"test_{target_path.name}"
            test_file.write_text(test_code, encoding="utf-8")

            res = subprocess.run(
                [get_python_runtime(), "-m", "pytest", "-q", "--tb=short", "-o", "pythonpath=.", "--disable-warnings", str(test_file)],
                cwd=str(worktree_dir),
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=get_subprocess_creation_flags(),
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
            target_file = tmp_path / target_file_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(candidate_code, encoding="utf-8")

            # Também espelha na raiz caso o teste faça import direto sem prefixo de pasta
            target_root = tmp_path / Path(target_file_path).name
            if target_root != target_file:
                target_root.write_text(candidate_code, encoding="utf-8")

            test_file = tmp_path / f"test_{Path(target_file_path).name}"
            test_file.write_text(test_code, encoding="utf-8")

            try:
                res = subprocess.run(
                    [get_python_runtime(), "-m", "pytest", "-q", "--tb=short", "-o", "pythonpath=.", "--disable-warnings", str(test_file)],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    creationflags=get_subprocess_creation_flags(),
                )
                return (res.returncode == 0), res.stdout, res.stderr
            except subprocess.TimeoutExpired:
                return False, "", "Timeout de 30s excedido em sandbox temporário."


# Alias de compatibilidade retroativa
PCIRouterAndBroker = PCIRouter
