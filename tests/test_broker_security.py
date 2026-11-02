"""
Testes de Seguranca Estrita e Autenticacao Negativa do Broker API.
Garante que rotas sem header, com token invalido ou com token revogado recebam HTTP 401.
"""

from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from pci_engine.broker_api import app, revoke_token, unrevoke_token, DEFAULT_DONOR_TOKEN
from pci_engine.database import DatabaseManager


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "security_test.db"
    db = DatabaseManager(db_path=db_file)
    db.init_db()
    return db


@pytest.fixture
def client(test_db: DatabaseManager, monkeypatch):
    import pci_engine.broker_api as broker_mod
    monkeypatch.setattr(broker_mod, "DB_PATH", test_db.db_path)
    return TestClient(app)


def test_missing_token_returns_401(client: TestClient):
    """Qualquer requisicao sem header X-PCI-Donor-Token deve falhar com 401."""
    # Rota v1
    resp = client.post("/api/v1/nodes/register", json={"node_id": "TEST", "node_name": "T"})
    assert resp.status_code == 401
    assert "ausente" in resp.json().get("detail", "").lower()

    # Rota legada
    resp_leg = client.post("/nodes/register", json={"node_id": "TEST", "node_name": "T"})
    assert resp_leg.status_code == 401


def test_invalid_token_returns_401(client: TestClient):
    """Token forjado ou incorreto deve receber 401."""
    headers = {"X-PCI-Donor-Token": "token-falso-hacker-123"}
    resp = client.get("/api/v1/tasks/lease?node_id=NODE_1", headers=headers)
    assert resp.status_code == 401
    assert "inválido" in resp.json().get("detail", "").lower() or "não reconhecido" in resp.json().get("detail", "").lower()


def test_revoked_token_returns_401(client: TestClient):
    """Token que foi explicitamente revogado pelo administrador deve receber 401."""
    revoked = "pci-donor-malicious-node-999"
    revoke_token(revoked)
    try:
        headers = {"X-PCI-Donor-Token": revoked}
        resp = client.get(f"/api/v1/kudos/NODE_1", headers=headers)
        assert resp.status_code == 401
        assert "revogado" in resp.json().get("detail", "").lower()
    finally:
        unrevoke_token(revoked)


def test_valid_token_allows_access(client: TestClient):
    """Token oficial gerado dinamicamente deve passar com sucesso."""
    headers = {"X-PCI-Donor-Token": DEFAULT_DONOR_TOKEN}
    resp = client.post(
        "/api/v1/nodes/register",
        headers=headers,
        json={
            "node_id": "VALID_NODE_01",
            "node_name": "PC_DOADOR_VALIDO",
            "ram_total_mb": 16384,
            "ssd_free_gb": 120.0,
            "supported_models": ["qwen2.5-coder:3b"],
        },
    )
    assert resp.status_code == 200
    assert resp.json().get("status") == "REGISTERED"
