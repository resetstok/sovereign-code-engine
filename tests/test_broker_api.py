"""
Testes de Integração para a API Broker FastAPI (Etapa 5).
Verifica a comunicação de rede Tailscale, Pull com Lease, Validação em Sandbox e Livro-Razão de Kudos.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from pci_engine.broker_api import app, DB_PATH
import pci_engine.broker_api as broker_module
from pci_engine.database import DatabaseManager


@pytest.fixture
def test_db(tmp_path: Path, monkeypatch) -> DatabaseManager:
    db_file = tmp_path / "test_broker_jobs.db"
    db = DatabaseManager(db_path=db_file)
    db.register_node(
        node_id="MASTER_NODE",
        node_name="PC_MASTER",
        role="MASTER",
        ram_total_mb=8192,
        vram_total_mb=4096,
        ssd_free_gb=100.0,
        supported_models=["qwen2.5-coder:3b"],
        status="ONLINE",
    )
    monkeypatch.setattr(broker_module, "DB_PATH", db_file)
    monkeypatch.setattr(broker_module, "REPO_ROOT", tmp_path)
    return db


@pytest.fixture
def client(test_db: DatabaseManager) -> TestClient:
    return TestClient(app)


# =============================================================================
# TESTES DOS ENDPOINTS DO BROKER FASTAPI
# =============================================================================

def test_health_check_ok(client: TestClient):
    """Verifica saúde do Master com disco e hardware normais."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "HEALTHY"
    assert "master_health" in data
    assert "ssd_free_gb" in data["master_health"]


def test_health_check_critical_ssd_blocks(client: TestClient, monkeypatch):
    """Verifica que quando o SSD cai abaixo de 40GB, a API retorna 503 BLOCKED_EXTERNAL."""
    monkeypatch.setattr(
        "pci_engine.preflight.PreflightChecker.check_health",
        lambda repo_root: {
            "ssd_free_gb": 35.0,
            "ssd_critical": True,
            "ram_free_mb": 2000.0,
            "ram_total_mb": 8000.0,
            "can_run_local_3b": True,
            "gpu": {"available": True},
        },
    )
    resp = client.get("/health")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "BLOCKED_EXTERNAL"
    assert "abaixo de 40GB" in data["detail"]


def test_register_node_and_heartbeat(client: TestClient, test_db: DatabaseManager):
    """Testa cadastro de nó doador e batimento cardíaco."""
    payload = {
        "node_id": "DONOR_TEST_01",
        "node_name": "PC_DOADOR_ALPHA",
        "ram_total_mb": 16384,
        "vram_total_mb": 8192,
        "ssd_free_gb": 250.0,
        "supported_models": ["qwen2.5-coder:7b", "llama3.1:8b"],
    }
    resp = client.post("/nodes/register", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "REGISTERED"

    node = test_db.get_node("DONOR_TEST_01")
    assert node is not None
    assert node["role"] == "DONOR"
    assert node["status"] == "ONLINE"

    # Envia Heartbeat
    hb_resp = client.post("/nodes/heartbeat", json={"node_id": "DONOR_TEST_01", "status": "ONLINE"})
    assert hb_resp.status_code == 200
    assert hb_resp.json()["status"] == "HEARTBEAT_ACK"

    # Heartbeat de nó inexistente deve retornar 404
    hb_unknown = client.post("/nodes/heartbeat", json={"node_id": "UNKNOWN_NODE"})
    assert hb_unknown.status_code == 404


def test_lease_task_for_donor(client: TestClient, test_db: DatabaseManager):
    """Testa o mecanismo de Pull com Lease de tarefas públicas."""
    # Registra doador
    client.post(
        "/nodes/register",
        json={
            "node_id": "DONOR_PULL_01",
            "node_name": "DOADOR_PULL",
            "ram_total_mb": 16384,
            "vram_total_mb": 8192,
            "ssd_free_gb": 100.0,
            "supported_models": ["qwen2.5-coder:7b"],
        },
    )

    # Sem tarefas -> 204 No Content
    resp_empty = client.get("/tasks/lease?node_id=DONOR_PULL_01")
    assert resp_empty.status_code == 204

    # Insere tarefa elegível (PUBLIC)
    test_db.insert_task(
        task_id="TASK_DONOR_001",
        parent_masterbook="MB_REMOTE",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=30,
        spec_markdown="Extraia dados de log",
        test_code="def test_log(): assert True",
        target_file_path="src/log_extractor.py",
        status="PENDING",
    )

    # Agora faz o lease
    resp_lease = client.get("/tasks/lease?node_id=DONOR_PULL_01")
    assert resp_lease.status_code == 200
    task_data = resp_lease.json()
    assert task_data["task_id"] == "TASK_DONOR_001"
    assert "lease_expires_at" in task_data

    # Verifica status atualizado para LEASED no banco
    t = test_db.get_task("TASK_DONOR_001")
    assert t["status"] == "LEASED"
    assert t["leased_by_node"] == "DONOR_PULL_01"


def test_submit_task_and_kudos_awarded(client: TestClient, test_db: DatabaseManager, monkeypatch):
    """Testa submissão com sucesso que valida em sandbox e premia Kudos."""
    # Registra doador
    client.post(
        "/nodes/register",
        json={
            "node_id": "DONOR_SUBMIT_01",
            "node_name": "DOADOR_SUBMIT",
            "ram_total_mb": 16384,
            "vram_total_mb": 8192,
            "ssd_free_gb": 100.0,
            "supported_models": ["qwen2.5-coder:7b"],
        },
    )
    test_db.insert_task(
        task_id="TASK_TO_SUBMIT",
        parent_masterbook="MB_SUBMIT",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=40,
        spec_markdown="spec",
        test_code="def test_ok(): assert True",
        target_file_path="src/ok.py",
        status="LEASED",
    )

    # Mock da validação sandbox do router para retornar sucesso
    monkeypatch.setattr(
        "pci_engine.router.PCIRouter._run_in_worktree_or_fallback",
        lambda self, task_id, target_file_path, candidate_code, test_code: (True, "1 passed", ""),
    )

    submit_payload = {
        "node_id": "DONOR_SUBMIT_01",
        "candidate_code": "def ok(): return True\n",
        "compute_seconds": 15.0,
    }
    resp = client.post("/tasks/TASK_TO_SUBMIT/submit", json=submit_payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "VERIFIED_AND_INTEGRATED"
    assert resp.json()["awarded"] is True

    # Verifica se os Kudos foram concedidos
    kudos_resp = client.get("/kudos/DONOR_SUBMIT_01")
    assert kudos_resp.status_code == 200
    kudos_data = kudos_resp.json()
    assert kudos_data["kudos_balance"] > 0
    assert kudos_data["transactions_count"] >= 1
