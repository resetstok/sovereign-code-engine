"""
Testes de Integração para a API Broker FastAPI e Agente Doador (Etapa 5).
Verifica a comunicação de rede Tailscale, autenticação por token revogável (X-PCI-Donor-Token),
Pull com Lease (45 min), validação cega em sandbox no Master, Livro-Razão de Kudos e cliente doador.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from pci_engine.broker_api import (
    app,
    DEFAULT_DONOR_TOKEN,
    REVOKED_DONOR_TOKENS,
    revoke_token,
    unrevoke_token,
)
import pci_engine.broker_api as broker_module
from pci_engine.database import DatabaseManager
from donor_client.worker_agent import DonorWorkerAgent


AUTH_HEADER = {"X-PCI-Donor-Token": DEFAULT_DONOR_TOKEN}


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
# TESTES DE MONITORAMENTO E PREFLIGHT
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


# =============================================================================
# TESTES DE AUTENTICAÇÃO POR TOKEN REVOGÁVEL (X-PCI-Donor-Token)
# =============================================================================

def test_token_auth_missing_header_rejected(client: TestClient):
    """Requisições aos endpoints v1 sem token devem ser rejeitadas com 401."""
    resp = client.post(
        "/api/v1/nodes/register",
        json={
            "node_id": "DONOR_NO_AUTH",
            "node_name": "DONOR",
            "ram_total_mb": 8000,
            "ssd_free_gb": 50.0,
            "supported_models": ["qwen2.5-coder:3b"],
        },
    )
    assert resp.status_code == 401
    assert "X-PCI-Donor-Token" in resp.json()["detail"]


def test_token_auth_invalid_token_rejected(client: TestClient):
    """Requisições com token não autorizado devem ser rejeitadas com 401."""
    resp = client.post(
        "/api/v1/nodes/register",
        headers={"X-PCI-Donor-Token": "invalid-hacker-token"},
        json={
            "node_id": "DONOR_INVALID_AUTH",
            "node_name": "DONOR",
            "ram_total_mb": 8000,
            "ssd_free_gb": 50.0,
            "supported_models": ["qwen2.5-coder:3b"],
        },
    )
    assert resp.status_code == 401
    assert "Token de doador inválido" in resp.json()["detail"]


def test_token_revocation_lifecycle(client: TestClient):
    """Testa o ciclo de revogação e restauração dinâmica de tokens de doadores."""
    test_token = "pci-donor-special-friend-token"
    reg_payload = {
        "node_id": "DONOR_REVOKE_TEST",
        "node_name": "DONOR_FRIEND",
        "ram_total_mb": 16000,
        "ssd_free_gb": 120.0,
        "supported_models": ["qwen2.5-coder:7b"],
    }

    # Inicialmente aceita
    resp = client.post("/api/v1/nodes/register", headers={"X-PCI-Donor-Token": test_token}, json=reg_payload)
    assert resp.status_code == 200

    # Revoga o token
    revoke_token(test_token)
    try:
        resp_revoked = client.post(
            "/api/v1/nodes/register",
            headers={"X-PCI-Donor-Token": test_token},
            json=reg_payload,
        )
        assert resp_revoked.status_code == 401
        assert "revogado" in resp_revoked.json()["detail"]
    finally:
        # Restaura o token
        unrevoke_token(test_token)

    # Após restauração, volta a funcionar
    resp_restored = client.post(
        "/api/v1/nodes/register",
        headers={"X-PCI-Donor-Token": test_token},
        json=reg_payload,
    )
    assert resp_restored.status_code == 200


# =============================================================================
# TESTES DOS ENDPOINTS V1 DO BROKER FASTAPI
# =============================================================================

def test_register_and_heartbeat_v1(client: TestClient, test_db: DatabaseManager):
    """Testa cadastro de nó doador e batimento cardíaco na API v1."""
    payload = {
        "node_id": "DONOR_V1_01",
        "node_name": "PC_DOADOR_V1",
        "ram_total_mb": 16384,
        "vram_total_mb": 8192,
        "ssd_free_gb": 250.0,
        "supported_models": ["qwen2.5-coder:7b", "llama3.1:8b"],
    }
    resp = client.post("/api/v1/nodes/register", headers=AUTH_HEADER, json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "REGISTERED"

    node = test_db.get_node("DONOR_V1_01")
    assert node is not None
    assert node["role"] == "DONOR"
    assert node["status"] == "ONLINE"

    # Envia Heartbeat v1
    hb_resp = client.post(
        "/api/v1/nodes/heartbeat",
        headers=AUTH_HEADER,
        json={"node_id": "DONOR_V1_01", "status": "ONLINE", "ram_free_mb": 12000},
    )
    assert hb_resp.status_code == 200
    assert hb_resp.json()["status"] == "HEARTBEAT_ACK"

    # Heartbeat de nó inexistente deve retornar 404
    hb_unknown = client.post(
        "/api/v1/nodes/heartbeat",
        headers=AUTH_HEADER,
        json={"node_id": "UNKNOWN_NODE_ID"},
    )
    assert hb_unknown.status_code == 404


def test_lease_task_for_donor_v1(client: TestClient, test_db: DatabaseManager):
    """Testa o mecanismo de Pull com Lease via API v1 garantindo confinamento de dados."""
    # Registra doador
    client.post(
        "/api/v1/nodes/register",
        headers=AUTH_HEADER,
        json={
            "node_id": "DONOR_PULL_V1",
            "node_name": "DOADOR_PULL_V1",
            "ram_total_mb": 16384,
            "vram_total_mb": 8192,
            "ssd_free_gb": 100.0,
            "supported_models": ["qwen2.5-coder:7b"],
        },
    )

    # Sem tarefas -> 204 No Content
    resp_empty = client.post("/api/v1/tasks/lease", headers=AUTH_HEADER, json={"node_id": "DONOR_PULL_V1"})
    assert resp_empty.status_code == 204

    # Insere tarefa elegível (PUBLIC)
    test_db.insert_task(
        task_id="TASK_DONOR_PUBLIC_01",
        parent_masterbook="MB_REMOTE",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=30,
        spec_markdown="Extraia dados de log de conexao",
        test_code="def test_log(): assert True",
        target_file_path="src/log_extractor.py",
        status="PENDING",
    )

    # Faz o lease via POST v1
    resp_lease = client.post("/api/v1/tasks/lease", headers=AUTH_HEADER, json={"node_id": "DONOR_PULL_V1"})
    assert resp_lease.status_code == 200
    task_data = resp_lease.json()
    assert task_data["task_id"] == "TASK_DONOR_PUBLIC_01"
    assert "spec_markdown" in task_data
    assert "test_code" in task_data
    assert "target_filename" in task_data
    assert "lease_expires_at" in task_data

    # Garante que o caminho absoluto interno do repositório NÃO é exposto ao doador
    assert "target_file_path" not in task_data
    assert "repo_root" not in task_data

    # Verifica status atualizado para LEASED no banco
    t = test_db.get_task("TASK_DONOR_PUBLIC_01")
    assert t["status"] == "LEASED"
    assert t["leased_by_node"] == "DONOR_PULL_V1"


def test_strict_local_privacy_never_leased(client: TestClient, test_db: DatabaseManager):
    """Garante que tarefas com privacy_level='STRICT_LOCAL' NUNCA sejam entregues a doadores."""
    client.post(
        "/api/v1/nodes/register",
        headers=AUTH_HEADER,
        json={
            "node_id": "DONOR_UNPRIVILEGED",
            "node_name": "DOADOR_EXTERNO",
            "ram_total_mb": 16384,
            "ssd_free_gb": 100.0,
            "supported_models": ["qwen2.5-coder:7b"],
        },
    )

    # Insere apenas tarefa STRICT_LOCAL
    test_db.insert_task(
        task_id="TASK_SECRET_LOCAL_01",
        parent_masterbook="MB_FORENSIC",
        task_type="FORENSIC",
        privacy_level="STRICT_LOCAL",
        estimated_lines=50,
        spec_markdown="Processamento de dados bancarios forenses confidenciais",
        test_code="def test_forensic(): assert True",
        target_file_path="src/forensic_bank.py",
        status="PENDING",
    )

    # Tentativa de pull deve retornar 204 (nenhuma tarefa entregue)
    resp = client.post("/api/v1/tasks/lease", headers=AUTH_HEADER, json={"node_id": "DONOR_UNPRIVILEGED"})
    assert resp.status_code == 204

    # Tarefa permanece PENDING no Master
    t = test_db.get_task("TASK_SECRET_LOCAL_01")
    assert t["status"] == "PENDING"
    assert t["leased_by_node"] is None


def test_submit_task_and_kudos_awarded_v1(client: TestClient, test_db: DatabaseManager, monkeypatch):
    """Testa submissão via API v1 com validação em sandbox do Master e crédito de Kudos."""
    client.post(
        "/api/v1/nodes/register",
        headers=AUTH_HEADER,
        json={
            "node_id": "DONOR_SUBMIT_V1",
            "node_name": "DOADOR_SUBMIT_V1",
            "ram_total_mb": 16384,
            "vram_total_mb": 8192,
            "ssd_free_gb": 100.0,
            "supported_models": ["qwen2.5-coder:7b"],
        },
    )
    test_db.insert_task(
        task_id="TASK_TO_SUBMIT_V1",
        parent_masterbook="MB_SUBMIT",
        task_type="PARSER",
        privacy_level="PUBLIC",
        estimated_lines=40,
        spec_markdown="spec",
        test_code="def test_ok(): assert True",
        target_file_path="src/parser.py",
        status="LEASED",
    )

    # Mock da validação sandbox do router para retornar sucesso
    monkeypatch.setattr(
        "pci_engine.router.PCIRouter._run_in_worktree_or_fallback",
        lambda self, task_id, target_file_path, candidate_code, test_code: (True, "1 passed", ""),
    )

    submit_payload = {
        "task_id": "TASK_TO_SUBMIT_V1",
        "node_id": "DONOR_SUBMIT_V1",
        "candidate_code": "def parse(): return True\n",
        "compute_seconds": 12.5,
    }
    resp = client.post("/api/v1/tasks/submit", headers=AUTH_HEADER, json=submit_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "VERIFIED_AND_INTEGRATED"
    assert data["awarded"] is True
    assert data["kudos"] > 0

    # Consulta o saldo e histórico via /api/v1/kudos/{node_id}
    kudos_resp = client.get("/api/v1/kudos/DONOR_SUBMIT_V1", headers=AUTH_HEADER)
    assert kudos_resp.status_code == 200
    kudos_data = kudos_resp.json()
    assert kudos_data["kudos_balance"] > 0
    assert kudos_data["transactions_count"] >= 1
    assert len(kudos_data["history"]) >= 1


def test_submit_task_failed_test_rejects(client: TestClient, test_db: DatabaseManager, monkeypatch):
    """Testa que se o sandbox do Master falhar no teste, a submissão é rejeitada e sem Kudos."""
    client.post(
        "/api/v1/nodes/register",
        headers=AUTH_HEADER,
        json={
            "node_id": "DONOR_FAIL_TEST",
            "node_name": "DOADOR_BUGADO",
            "ram_total_mb": 16384,
            "ssd_free_gb": 100.0,
            "supported_models": ["qwen2.5-coder:7b"],
        },
    )
    test_db.insert_task(
        task_id="TASK_FAILING_01",
        parent_masterbook="MB_FAIL",
        task_type="MATH",
        privacy_level="PUBLIC",
        estimated_lines=20,
        spec_markdown="spec",
        test_code="def test_fail(): assert False",
        target_file_path="src/calc.py",
        status="LEASED",
    )

    # Simula falha no pytest do Master
    monkeypatch.setattr(
        "pci_engine.router.PCIRouter._run_in_worktree_or_fallback",
        lambda self, task_id, target_file_path, candidate_code, test_code: (False, "", "AssertionError: assert False"),
    )

    submit_payload = {
        "task_id": "TASK_FAILING_01",
        "node_id": "DONOR_FAIL_TEST",
        "candidate_code": "def err(): pass\n",
        "compute_seconds": 5.0,
    }
    resp = client.post("/api/v1/tasks/submit", headers=AUTH_HEADER, json=submit_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "TEST_FAILED_RETRYING"
    assert data["awarded"] is False


# =============================================================================
# TESTES DE COMPATIBILIDADE LEGADA (ROTAS NÃO PREFIXADAS)
# =============================================================================

def test_legacy_routes_backward_compatibility(client: TestClient, test_db: DatabaseManager, monkeypatch):
    """Garante que rotas legadas (/nodes/register, /tasks/lease, etc) continuam funcionando."""
    # Cadastro legado
    resp_reg = client.post(
        "/nodes/register",
        json={
            "node_id": "DONOR_LEGACY_01",
            "node_name": "DOADOR_LEGADO",
            "ram_total_mb": 8000,
            "ssd_free_gb": 80.0,
            "supported_models": ["qwen2.5-coder:3b"],
        },
    )
    assert resp_reg.status_code == 200

    # Heartbeat legado
    resp_hb = client.post("/nodes/heartbeat", json={"node_id": "DONOR_LEGACY_01", "status": "ONLINE"})
    assert resp_hb.status_code == 200

    # Consulta de Kudos legada
    resp_kudos = client.get("/kudos/DONOR_LEGACY_01")
    assert resp_kudos.status_code == 200
    assert "kudos_balance" in resp_kudos.json()


# =============================================================================
# TESTES UNITÁRIOS DO CLIENTE DOADOR (DONOR WORKER AGENT)
# =============================================================================

def test_donor_worker_agent_preflight_and_extract(tmp_path: Path):
    """Testa auditoria de hardware e rotinas de extração de código do agente doador."""
    agent = DonorWorkerAgent(master_url="http://127.0.0.1:8000", workspace_dir=tmp_path)
    hw = agent.get_hardware_info()

    assert "node_id" in hw
    assert hw["node_id"].startswith("donor_")
    assert hw["ram_total_mb"] > 0
    assert hw["ssd_free_gb"] > 0
    assert "qwen2.5-coder:3b" in hw["supported_models"]

    # Extração de código markdown
    md_with_fence = "Texto explicativo antes\n```python\ndef foo():\n    return 42\n```\nTexto depois"
    code = agent._extract_code(md_with_fence)
    assert code == "def foo():\n    return 42"

    raw_code = "def bar():\n    return 'clean'"
    assert agent._extract_code(raw_code) == raw_code


def test_donor_worker_agent_local_pretest(tmp_path: Path):
    """Verifica que o pré-teste local executa confinado na pasta temporária com sucesso."""
    agent = DonorWorkerAgent(master_url="http://127.0.0.1:8000", workspace_dir=tmp_path)

    # 1. Código válido que passa no teste
    valid_code = "def soma(a, b):\n    return a + b\n"
    test_code = "from solution import soma\ndef test_soma():\n    assert soma(2, 3) == 5\n"

    passed, out = agent.local_pretest(
        task_id="TASK_PRETEST_PASS",
        candidate_code=valid_code,
        test_code=test_code,
        target_filename="solution.py",
    )
    assert passed is True
    assert "1 passed" in out or "passed" in out

    # 2. Código inválido que falha no teste
    invalid_code = "def soma(a, b):\n    return a - b\n"
    failed, out_fail = agent.local_pretest(
        task_id="TASK_PRETEST_FAIL",
        candidate_code=invalid_code,
        test_code=test_code,
        target_filename="solution.py",
    )
    assert failed is False
    assert "assert -1 == 5" in out_fail or "FAILED" in out_fail or "AssertionError" in out_fail
