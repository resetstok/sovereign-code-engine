"""
Módulo: pci_engine/broker_api.py
Servidor FastAPI local (Tailscale VPN) para Pull & Lease de Nós Doadores da arquitetura PCI Master v2.0/v2.1.
Permite pull assíncrono de tarefas, validação em sandbox Git Worktree e livro-razão de Kudos.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from pci_engine.database import DatabaseManager
from pci_engine.preflight import PreflightChecker
from pci_engine.router import PCIRouter

app = FastAPI(
    title="Sovereign Code Engine - Broker API v2.0",
    description="Interface de Pull/Lease para nós doadores via rede privada Tailscale.",
    version="2.0.0",
)

DB_PATH = Path("data/jobs.db")
REPO_ROOT = Path(".").resolve()


def get_db() -> DatabaseManager:
    return DatabaseManager(db_path=DB_PATH)


def get_router() -> PCIRouter:
    return PCIRouter(db_path=DB_PATH, repo_root=REPO_ROOT)


class RegisterNodeRequest(BaseModel):
    node_id: str
    node_name: str
    ram_total_mb: int
    vram_total_mb: int
    ssd_free_gb: float
    supported_models: List[str]


class HeartbeatRequest(BaseModel):
    node_id: str
    status: str = "ONLINE"
    ram_free_mb: Optional[float] = None
    vram_free_mb: Optional[float] = None


class SubmissionRequest(BaseModel):
    node_id: str
    candidate_code: str
    compute_seconds: float = Field(ge=0.0)


@app.get("/health")
def get_health(response: Response):
    """Audita a integridade física do nó mestre. Aborta se SSD estiver abaixo de 40GB."""
    health = PreflightChecker.check_health(REPO_ROOT)
    if health.get("ssd_critical"):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "BLOCKED_EXTERNAL",
            "detail": "SSD do Master abaixo de 40GB. Operações pausadas para proteção física.",
            "master_health": health,
        }
    return {"status": "HEALTHY", "master_health": health}


@app.post("/nodes/register")
def register_node(req: RegisterNodeRequest):
    """Cadastra ou atualiza o nó doador no livro-razão da rede."""
    db = get_db()
    db.register_node(
        node_id=req.node_id,
        node_name=req.node_name,
        role="DONOR",
        ram_total_mb=req.ram_total_mb,
        vram_total_mb=req.vram_total_mb,
        ssd_free_gb=req.ssd_free_gb,
        supported_models=req.supported_models,
        status="ONLINE",
    )
    return {"status": "REGISTERED", "node_id": req.node_id}


@app.post("/nodes/heartbeat")
def node_heartbeat(req: HeartbeatRequest):
    """Atualiza o batimento cardíaco e status de conectividade do nó."""
    db = get_db()
    node = db.get_node(req.node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nó não encontrado. Registre-se primeiro via /nodes/register.",
        )

    now = datetime.now(timezone.utc).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            """
            UPDATE nodes
            SET status = ?, last_heartbeat = ?
            WHERE node_id = ?
            """,
            (req.status, now, req.node_id),
        )
        conn.commit()

    return {"status": "HEARTBEAT_ACK", "timestamp": now}


@app.get("/tasks/lease")
def lease_task(node_id: str = Query(..., description="ID do nó doador cadastrado")):
    """
    Entrega a próxima tarefa elegível (PUBLIC ou SANITIZED_DERIVATIVE) para o doador,
    travando um lease de 45 minutos no jobs.db.
    """
    router = get_router()
    task = router.lease_task_for_donor(donor_node_id=node_id, lease_minutes=45)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_204_NO_CONTENT,
            detail="Nenhuma tarefa elegível para doadores no momento.",
        )
    return task


@app.post("/tasks/{task_id}/submit")
def submit_task(task_id: str, req: SubmissionRequest):
    """
    Recebe a submissão de código gerado pelo nó doador e valida em sandbox isolado
    via Git Worktree (nanocoder pattern). Se passar no Pytest, bonifica Kudos.
    """
    router = get_router()
    success = router.verify_and_integrate_submission(
        task_id=task_id,
        node_id=req.node_id,
        candidate_code=req.candidate_code,
        compute_sec=req.compute_seconds,
    )
    if success:
        return {
            "status": "VERIFIED_AND_INTEGRATED",
            "task_id": task_id,
            "awarded": True,
        }
    else:
        return {
            "status": "TEST_FAILED_RETRYING",
            "task_id": task_id,
            "awarded": False,
        }


@app.get("/kudos/{node_id}")
def get_kudos(node_id: str):
    """Retorna o saldo total de Kudos e o histórico de transações de computação do nó doador."""
    db = get_db()
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nó doador não cadastrado.",
        )

    ledger = db.get_kudos_ledger_for_node(node_id)
    return {
        "node_id": node_id,
        "node_name": node["node_name"],
        "kudos_balance": node["kudos_balance"],
        "transactions_count": len(ledger),
        "history": ledger[:20],
    }
