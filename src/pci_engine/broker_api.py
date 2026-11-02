"""
Módulo: pci_engine/broker_api.py
Servidor FastAPI local (Tailscale) para Pull & Lease de Nós Doadores.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from pci_engine.db import JobsDatabaseV2
from pci_engine.router import PCIRouterAndBroker

app = FastAPI(
    title="Sovereign Code Engine - Broker API v2.0",
    description="Interface segura de Pull/Lease para nós doadores via Tailscale VPN.",
    version="2.0.0",
)

db_path = Path("data/jobs.db")
broker = PCIRouterAndBroker(db_path=db_path)


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
def get_health():
    health = broker.check_master_health()
    return {"status": "HEALTHY", "master_health": health}


@app.post("/nodes/register")
def register_node(req: RegisterNodeRequest):
    with broker._get_conn() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO nodes (
                node_id, node_name, role, ram_total_mb, vram_total_mb,
                ssd_free_gb, supported_models, status, last_heartbeat
            ) VALUES (?, ?, 'DONOR', ?, ?, ?, ?, 'ONLINE', ?)
            """,
            (
                req.node_id,
                req.node_name,
                req.ram_total_mb,
                req.vram_total_mb,
                req.ssd_free_gb,
                json.dumps(req.supported_models),
                now,
            ),
        )
        conn.commit()
    return {"status": "REGISTERED", "node_id": req.node_id}


@app.post("/nodes/heartbeat")
def node_heartbeat(req: HeartbeatRequest):
    with broker._get_conn() as conn:
        now = datetime.now(timezone.utc).isoformat()
        res = conn.execute(
            """
            UPDATE nodes
            SET status = ?, last_heartbeat = ?
            WHERE node_id = ?
            """,
            (req.status, now, req.node_id),
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Nó não encontrado. Registre-se primeiro.")
    return {"status": "HEARTBEAT_ACK", "timestamp": now}


@app.get("/tasks/lease")
def lease_task(node_id: str = Query(..., description="ID do nó doador autenticado")):
    task = broker.lease_task_for_donor(donor_node_id=node_id, lease_minutes=45)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_204_NO_CONTENT,
            detail="Nenhuma tarefa elegível para doadores no momento.",
        )
    return task


@app.post("/tasks/{task_id}/submit")
def submit_task(task_id: str, req: SubmissionRequest):
    success = broker.verify_and_integrate_submission(
        task_id=task_id,
        node_id=req.node_id,
        candidate_code=req.candidate_code,
        compute_sec=req.compute_seconds,
    )
    if success:
        return {"status": "VERIFIED_AND_INTEGRATED", "task_id": task_id, "awarded": True}
    else:
        return {"status": "TEST_FAILED_RETRYING", "task_id": task_id, "awarded": False}


@app.get("/kudos/{node_id}")
def get_kudos(node_id: str):
    with broker._get_conn() as conn:
        row = conn.execute("SELECT kudos_balance FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Nó não cadastrado.")
        return {"node_id": node_id, "kudos_balance": row["kudos_balance"]}
