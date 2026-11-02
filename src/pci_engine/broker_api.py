"""
Módulo: pci_engine/broker_api.py
Servidor FastAPI local (Tailscale VPN) para Pull & Lease de Nós Doadores da arquitetura PCI Master v2.0/v2.1.
Implementa autenticação via Token Revogável (X-PCI-Donor-Token), Pull com Lease (45 min),
validação cega e isolada em sandbox Git Worktree pelo Master, e Livro-Razão de Kudos.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from pci_engine.context_guard import ContextGuard
from pci_engine.database import DatabaseManager
from pci_engine.orchestrator import TaskExecutionOrchestrator
from pci_engine.preflight import PreflightChecker
from pci_engine.router import PCIRouter

logger = logging.getLogger("pci_engine.broker_api")

app = FastAPI(
    title="Sovereign Code Engine - Broker API v2.1",
    description="Interface de Pull/Lease para nós doadores via rede privada Tailscale (estilo AI Horde).",
    version="2.1.0",
)

DB_PATH = Path("data/jobs.db")
REPO_ROOT = Path(".").resolve()

# =============================================================================
# Autenticação via Token Revogável (Header: X-PCI-Donor-Token)
# =============================================================================
def _load_or_create_broker_token() -> str:
    env_token = os.getenv("PCI_DONOR_TOKEN")
    if env_token:
        return env_token
    token_path = Path("config/broker_token.secret")
    if token_path.exists():
        t = token_path.read_text(encoding="utf-8").strip()
        if t:
            return t
    import secrets
    t = secrets.token_urlsafe(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(t, encoding="utf-8")
    return t


DEFAULT_DONOR_TOKEN = _load_or_create_broker_token()
REVOKED_DONOR_TOKENS: Set[str] = set()


def revoke_token(token: str) -> None:
    """Revoga um token de doador administrativamente."""
    REVOKED_DONOR_TOKENS.add(token)


def unrevoke_token(token: str) -> None:
    """Restaura um token de doador."""
    REVOKED_DONOR_TOKENS.discard(token)


def verify_donor_token(
    x_pci_donor_token: Optional[str] = Header(None, alias="X-PCI-Donor-Token"),
) -> str:
    """
    Valida a presença e integridade do token revogável no header HTTP.
    Permite comunicação apenas de nós autorizados na VPN Tailscale.
    """
    if not x_pci_donor_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header de autenticação 'X-PCI-Donor-Token' ausente.",
        )
    if x_pci_donor_token in REVOKED_DONOR_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de doador revogado pelo administrador do Master.",
        )
    if x_pci_donor_token != DEFAULT_DONOR_TOKEN and not x_pci_donor_token.startswith("pci-donor-"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de doador inválido ou não reconhecido.",
        )
    return x_pci_donor_token


def get_db() -> DatabaseManager:
    return DatabaseManager(db_path=DB_PATH)


def get_router() -> PCIRouter:
    return PCIRouter(db_path=DB_PATH, repo_root=REPO_ROOT)


def get_orchestrator() -> TaskExecutionOrchestrator:
    return TaskExecutionOrchestrator(repo_root=REPO_ROOT, db_path=DB_PATH)


# =============================================================================
# Modelos de Dados Pydantic
# =============================================================================
class RegisterNodeRequest(BaseModel):
    node_id: str
    node_name: str
    ram_total_mb: int
    vram_total_mb: int = 0
    ssd_free_gb: float
    supported_models: List[str]


class HeartbeatRequest(BaseModel):
    node_id: str
    status: str = "ONLINE"
    ram_free_mb: Optional[float] = None
    vram_free_mb: Optional[float] = None


class LeaseTaskRequest(BaseModel):
    node_id: str


class SubmitTaskRequest(BaseModel):
    task_id: str
    node_id: str
    candidate_code: str
    compute_seconds: float = Field(ge=0.0)
    extracted_sensitive_data: Optional[str] = None
    encryption_password: Optional[str] = None


class InvestigativeSubmissionRequest(BaseModel):
    node_id: str
    task_id: Optional[str] = None
    extracted_content: str
    password: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    compute_seconds: float = Field(default=1.0, ge=0.0)


class SubmissionRequest(BaseModel):
    node_id: str
    candidate_code: str
    compute_seconds: float = Field(ge=0.0)


# =============================================================================
# ENDPOINTS PÚBLICOS DE MONITORAMENTO (HEALTH CHECK)
# =============================================================================
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


# =============================================================================
# ROTAS V1 AUTENTICADAS VIA TOKEN (X-PCI-Donor-Token)
# =============================================================================

@app.post("/api/v1/nodes/register")
def register_node_v1(req: RegisterNodeRequest, token: str = Depends(verify_donor_token)):
    """Registra o PC doador e suas capacidades físicas no jobs.db."""
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


@app.post("/api/v1/nodes/heartbeat")
def node_heartbeat_v1(req: HeartbeatRequest, token: str = Depends(verify_donor_token)):
    """Atualiza o batimento cardíaco e status ONLINE do doador."""
    db = get_db()
    node = db.get_node(req.node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nó não encontrado. Registre-se primeiro via /api/v1/nodes/register.",
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


@app.post("/api/v1/tasks/lease")
@app.get("/api/v1/tasks/lease")
def lease_task_v1(
    body: Optional[LeaseTaskRequest] = None,
    node_id: Optional[str] = Query(None),
    token: str = Depends(verify_donor_token),
):
    """
    Aplica o mecanismo Pull with Lease. Entrega apenas tarefas públicas/sanitizadas,
    trava a tarefa por 45 minutos (status='LEASED') e envia SOMENTE dados essenciais:
    task_id, spec_markdown, test_code e target_filename (nunca dados sensíveis nem caminhos internos).
    """
    donor_id = body.node_id if body else node_id
    if not donor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="node_id obrigatório no body ou query param.",
        )

    router = get_router()
    task = router.lease_task_for_donor(donor_node_id=donor_id, lease_minutes=45)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_204_NO_CONTENT,
            detail="Nenhuma tarefa elegível para doadores no momento.",
        )

    # Retorna estritamente o necessário (confinamento de privacidade)
    return {
        "task_id": task["task_id"],
        "spec_markdown": task["spec_markdown"],
        "test_code": task["test_code"],
        "target_filename": task.get("target_filename") or Path(task.get("target_rel_path", "")).name,
        "lease_expires_at": task["lease_expires_at"],
    }


@app.post("/api/v1/tasks/submit")
def submit_task_v1(req: SubmitTaskRequest, token: str = Depends(verify_donor_token)):
    """
    Recebe o código candidato gerado pelo doador.
    O MASTER NUNCA CONFIA NO DOADOR: executa o teste isolado em sandbox no Master.
    Só se o Pytest passar no Master a tarefa vira VERIFIED e os Kudos são creditados.
    """
    router = get_router()
    db = get_db()
    task = db.get_task(req.task_id)

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tarefa '{req.task_id}' não encontrada.",
        )

    # Validação segura em sandbox Git Worktree / tempdir via PCIRouter / Orchestrator
    success = router.verify_and_integrate_submission(
        task_id=req.task_id,
        node_id=req.node_id,
        candidate_code=req.candidate_code,
        compute_sec=req.compute_seconds,
    )

    if success:
        kudos = round((task["estimated_lines"] * 0.5) + (req.compute_seconds * 0.05), 2)
        resp_data: Dict[str, Any] = {
            "status": "VERIFIED_AND_INTEGRATED",
            "task_id": req.task_id,
            "awarded": True,
            "kudos": kudos,
        }

        # Se houver dados sensíveis extraídos pelo nó doador, tranca com a senha do operador
        if req.extracted_sensitive_data:
            node = db.get_node(req.node_id)
            node_name = node["node_name"] if node else req.node_id
            vault_info = ContextGuard.pack_sensitive_donor_extraction(
                node_name=node_name,
                raw_data=req.extracted_sensitive_data,
                password=req.encryption_password,
                repo_root=REPO_ROOT,
                db_manager=db,
                task_id=req.task_id,
            )
            resp_data["sensitive_vault"] = vault_info

        return resp_data
    else:
        return {
            "status": "TEST_FAILED_RETRYING",
            "task_id": req.task_id,
            "awarded": False,
            "detail": "Código reprovado no Pytest isolado do Master PC.",
        }


@app.post("/api/v1/investigative/submit")
def submit_investigative_extraction_v1(
    req: InvestigativeSubmissionRequest,
    token: str = Depends(verify_donor_token),
):
    """
    Recebe diretamente dados sensíveis extraídos por nó doador (ou worker Tailscale).
    Os dados (CPFs, e-mails, contratos) NÃO sofrem redação: são 100% preservados,
    mas são imediatamente trancados em data/spool_encrypted/<nome_doador>/
    com a senha criptográfica definida pelo operador antes da sincronização com o Drive.
    """
    db = get_db()
    node = db.get_node(req.node_id)
    node_name = node["node_name"] if node else req.node_id

    vault_info = ContextGuard.pack_sensitive_donor_extraction(
        node_name=node_name,
        raw_data=req.extracted_content,
        password=req.password,
        repo_root=REPO_ROOT,
        db_manager=db,
        task_id=req.task_id,
    )

    # Recompensa de kudos ao nó doador pelo processamento investigativo
    kudos = round(max(1.0, (len(req.extracted_content) / 200.0) + (req.compute_seconds * 0.1)), 2)
    if node:
        kudos_task_id = req.task_id if (req.task_id and db.get_task(req.task_id)) else None
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE nodes SET kudos_balance = kudos_balance + ? WHERE node_id = ?",
                (kudos, req.node_id),
            )
            conn.execute(
                """
                INSERT INTO kudos_ledger (node_id, task_id, kudos_awarded, compute_seconds)
                VALUES (?, ?, ?, ?)
                """,
                (req.node_id, kudos_task_id, kudos, req.compute_seconds),
            )
            conn.commit()

    return {
        "status": "INVESTIGATIVE_EXTRACTION_LOCKED",
        "node_id": req.node_id,
        "node_name": node_name,
        "evidence_id": vault_info["evidence_id"],
        "spool_file": vault_info["spool_file"],
        "relative_spool_path": vault_info["relative_spool_path"],
        "kudos_awarded": kudos,
        "message": f"Dados trancados com sucesso na pasta do doador '{node_name}' para o Drive.",
    }


@app.get("/api/v1/kudos/{node_id}")
def get_kudos_v1(node_id: str, token: str = Depends(verify_donor_token)):
    """Retorna o saldo de Kudos acumulados e o histórico de transações de computação."""
    db = get_db()
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nó doador '{node_id}' não cadastrado.",
        )

    ledger = db.get_kudos_ledger_for_node(node_id)
    return {
        "node_id": node_id,
        "node_name": node["node_name"],
        "kudos_balance": node["kudos_balance"],
        "transactions_count": len(ledger),
        "history": ledger[:20],
    }


# =============================================================================
# ENDPOINTS LEGADOS / COMPATIBILIDADE (Exigem autenticacao de token obrigatoria)
# =============================================================================
@app.post("/nodes/register")
def register_node_legacy(req: RegisterNodeRequest, token: str = Depends(verify_donor_token)):
    return register_node_v1(req, token=token)


@app.post("/nodes/heartbeat")
def node_heartbeat_legacy(req: HeartbeatRequest, token: str = Depends(verify_donor_token)):
    return node_heartbeat_v1(req, token=token)


@app.get("/tasks/lease")
def lease_task_legacy(node_id: str = Query(...), token: str = Depends(verify_donor_token)):
    return lease_task_v1(node_id=node_id, token=token)


@app.post("/tasks/{task_id}/submit")
def submit_task_legacy(task_id: str, req: SubmissionRequest, token: str = Depends(verify_donor_token)):
    submit_req = SubmitTaskRequest(
        task_id=task_id,
        node_id=req.node_id,
        candidate_code=req.candidate_code,
        compute_seconds=req.compute_seconds,
    )
    return submit_task_v1(submit_req, token=token)


@app.get("/kudos/{node_id}")
def get_kudos_legacy(node_id: str, token: str = Depends(verify_donor_token)):
    return get_kudos_v1(node_id, token=token)
