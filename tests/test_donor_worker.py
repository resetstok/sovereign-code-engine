"""
Testes Unitários para o Cliente Doador Autônomo (donor_client/worker_agent.py - Etapa 6).
Verifica a detecção de hardware, formato de mensagens, extração de código e ciclos de execução.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from donor_client.worker_agent import DonorWorkerAgent


@pytest.fixture
def agent(tmp_path: Path) -> DonorWorkerAgent:
    workspace = tmp_path / "test_workspace"
    return DonorWorkerAgent(
        master_url="http://127.0.0.1:8000",
        workspace_dir=workspace,
        ollama_url="http://127.0.0.1:11434",
        preferred_model="qwen2.5-coder:3b",
    )


# =============================================================================
# 1. DETECÇÃO DE HARDWARE E EXTRAÇÃO DE CÓDIGO
# =============================================================================

def test_hardware_detection(agent: DonorWorkerAgent):
    """Valida identificação determinística do nó doador e métricas de hardware."""
    hw = agent.hw_info
    assert hw["node_id"].startswith("donor_")
    assert hw["ram_total_mb"] > 0
    assert hw["ssd_free_gb"] >= 0
    assert "qwen2.5-coder:3b" in hw["supported_models"]
    assert agent.node_id == hw["node_id"]


def test_extract_code():
    """Valida parser robusto de extração de blocos de código python."""
    code_md = "```python\ndef subtrair(a, b):\n    return a - b\n```"
    assert DonorWorkerAgent._extract_code(code_md) == "def subtrair(a, b):\n    return a - b"

    code_generic = "```\ndef dividir(a, b):\n    return a / b\n```"
    assert DonorWorkerAgent._extract_code(code_generic) == "def dividir(a, b):\n    return a / b"

    code_raw = "def puro(): pass"
    assert DonorWorkerAgent._extract_code(code_raw) == code_raw


# =============================================================================
# 2. PROTOCOLOS DE REDE (REGISTER, HEARTBEAT, LEASE, SUBMIT)
# =============================================================================

def test_register_flow(agent: DonorWorkerAgent, monkeypatch):
    """Testa cadastro do nó doador."""
    monkeypatch.setattr(agent, "_http_request", lambda endpoint, method="GET", data=None, timeout=15: (200, {"status": "REGISTERED"}))
    assert agent.register() is True

    # Falha na conexão
    monkeypatch.setattr(agent, "_http_request", lambda endpoint, method="GET", data=None, timeout=15: (500, "Erro"))
    assert agent.register() is False


def test_heartbeat_flow(agent: DonorWorkerAgent, monkeypatch):
    """Testa envio de pulso cardíaco."""
    monkeypatch.setattr(agent, "_http_request", lambda endpoint, method="GET", data=None, timeout=5: (200, {"status": "HEARTBEAT_ACK"}))
    assert agent.send_heartbeat() is True


def test_pull_lease_flow(agent: DonorWorkerAgent, monkeypatch):
    """Testa pull de tarefa com lease."""
    mock_task = {
        "task_id": "TASK_DONOR_99",
        "spec_markdown": "Extrair info",
        "test_code": "def test_ok(): pass",
        "target_filename": "info.py",
        "target_rel_path": "src/info.py",
        "lease_expires_at": "2026-11-02T10:00:00Z",
    }
    monkeypatch.setattr(agent, "_http_request", lambda endpoint, method="GET", data=None, timeout=15: (200, mock_task))
    pulled = agent.pull_lease()
    assert pulled is not None
    assert pulled["task_id"] == "TASK_DONOR_99"

    # Fila vazia (204)
    monkeypatch.setattr(agent, "_http_request", lambda endpoint, method="GET", data=None, timeout=15: (204, None))
    assert agent.pull_lease() is None


def test_submit_solution_flow(agent: DonorWorkerAgent, monkeypatch):
    """Testa envio de solução e recebimento de Kudos."""
    monkeypatch.setattr(
        agent,
        "_http_request",
        lambda endpoint, method="GET", data=None, timeout=45: (200, {"status": "VERIFIED_AND_INTEGRATED", "awarded": True}),
    )
    submitted, awarded = agent.submit_solution("TASK_DONOR_99", "def info(): pass", 12.5)
    assert submitted is True
    assert awarded is True


# =============================================================================
# 3. CICLO RUN_ONCE
# =============================================================================

def test_run_once_no_tasks(agent: DonorWorkerAgent, monkeypatch):
    """Testa execução de ciclo quando a fila do Master está vazia."""
    monkeypatch.setattr(agent, "send_heartbeat", lambda: True)
    monkeypatch.setattr(agent, "pull_lease", lambda: None)

    res = agent.run_once()
    assert res["status"] == "NO_TASKS"


def test_run_once_with_task(agent: DonorWorkerAgent, monkeypatch):
    """Testa ciclo completo quando há tarefa elegível."""
    mock_task = {
        "task_id": "TASK_CYCLE_01",
        "spec_markdown": "Cria funcao multiplicacao",
        "test_code": "def test_mult(): assert mult(2, 3) == 6",
        "target_filename": "mult.py",
        "target_rel_path": "src/mult.py",
    }
    monkeypatch.setattr(agent, "send_heartbeat", lambda: True)
    monkeypatch.setattr(agent, "pull_lease", lambda: mock_task)
    monkeypatch.setattr(agent, "solve_task", lambda spec, test: "def mult(a, b): return a * b")
    monkeypatch.setattr(agent, "submit_solution", lambda tid, code, sec: (True, True))

    res = agent.run_once()
    assert res["status"] == "COMPLETED"
    assert res["task_id"] == "TASK_CYCLE_01"
    assert res["submitted"] is True
    assert res["awarded"] is True
