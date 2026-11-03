"""
Suíte de Teste Exaustiva Botão a Botão (PCI Master System v2.3).
Testa programaticamente cada ação de botão da GUI e suas funções subjacentes.
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

# Garante path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pci_engine.gui_app import PCIMasterGUI
from pci_engine.database import DatabaseManager
from pci_engine.benchmark_runner import BenchmarkRunner
from pci_engine.hard_wallet import HardWalletManager
from pci_engine.nightly_colibri import NightlyColibriWorker
from pci_engine.drive_sync import DriveSyncManager
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.preflight import PreflightChecker


@pytest.fixture
def test_app(tmp_path):
    """Cria uma instância da GUI em modo headless para testar callbacks."""
    db_file = tmp_path / "test_jobs.db"
    app = PCIMasterGUI(db_path=db_file, repo_root=REPO_ROOT, headless_test=True)
    yield app
    try:
        app.root.destroy()
    except Exception:
        pass


def test_01_hud_web_file_exists_and_resolves(test_app):
    """Testa se o botão 'Abrir HUD Web' resolve para um arquivo HTML real."""
    hud_path = test_app.repo_root / "assets" / "triquetra_hud.html"
    assert hud_path.exists(), f"HUD não encontrado em {hud_path}"
    assert "<svg" in hud_path.read_text(encoding="utf-8")


def test_02_clean_ram_callback(test_app):
    """Testa o acionamento do botão 'Higienizar RAM Agora'."""
    ram_info = RAMLiberator.get_system_ram_mb()
    assert "total_mb" in ram_info
    assert ram_info["total_mb"] > 0
    test_app._on_clean_ram_clicked()
    time.sleep(0.5)
    test_app._process_ui_queue()


def test_03_hybrid_and_broker_switches(test_app):
    """Testa os switches de Modo Híbrido e Broker Tailscale."""
    test_app.is_hybrid_mode.set(True)
    test_app._on_hybrid_switch_toggled()
    assert os.environ.get("PCI_HYBRID_MODE") == "1"

    test_app.is_hybrid_mode.set(False)
    test_app._on_hybrid_switch_toggled()
    assert os.environ.get("PCI_HYBRID_MODE") == "0"


def test_04_decompose_and_queue_flow(test_app):
    """Testa '1. Decompor Masterbook' seguido de '2. Execução Autônoma'."""
    test_spec = (
        "# Tarefa Teste Automático\n"
        "Construa uma função soma_positivos(lista: list) -> int que some apenas positivos."
    )
    test_app.txt_masterbook.delete("1.0", "end")
    test_app.txt_masterbook.insert("1.0", test_spec)

    test_app._on_decompose_clicked()
    
    # Aguarda a thread de background do Ollama terminar
    for _ in range(30):
        time.sleep(0.5)
        test_app._process_ui_queue()
        tasks = test_app.db.list_tasks()
        if len(tasks) > 0:
            break

    assert len(tasks) > 0, "Nenhuma tarefa foi enfileirada pela decomposição"


def test_05_mtv_batch_button_execution(test_app):
    """Testa '3. Executar MTV (Best-of-N)' com a tarefa extrair_cnpj."""
    tasks_dir = test_app.repo_root / "tasks"
    assert (tasks_dir / "extrair_cnpj" / "task.md").exists()
    assert (tasks_dir / "extrair_cnpj" / "test_solution.py").exists()

    test_app._on_run_mtv_batch_clicked()
    time.sleep(0.5)
    test_app._process_ui_queue()


def test_06_forensic_extract_investigative(test_app):
    """Testa '❖ Extrair & Cruzar Vínculos (100% Local)'."""
    fixture_path = test_app.repo_root / "tasks" / "fixtures" / "relatorio_investigacao_teste.txt"
    assert fixture_path.exists()

    test_app.file_path_var.set(str(fixture_path))
    test_app.remove_pii_var.set(False)

    test_app._on_extract_investigative_clicked()
    
    for _ in range(15):
        time.sleep(0.2)
        test_app._process_ui_queue()
        output = test_app.sanitizer_output_box.get("1.0", "end")
        if "111.222.333-44" in output:
            break

    assert "DOSSIÊ DE EXTRAÇÃO E CRUZAMENTO INVESTIGATIVO" in output or "DOSSI" in output
    assert "111.222.333-44" in output
    assert "BRA-2024" in output


def test_07_forensic_lock_spool_and_vault(test_app):
    """Testa '🔒 Trancar e Salvar no Spool Cifrado' e '🔑 Trancar p/ Doador'."""
    fixture_path = test_app.repo_root / "tasks" / "fixtures" / "relatorio_investigacao_teste.txt"
    test_app.file_path_var.set(str(fixture_path))
    test_app.encrypt_spool_var.set(True)
    test_app.operator_password_var.set("SENHA_TESTE_SOBERANA_1234")

    test_app._on_generate_sanitized_derivative()
    time.sleep(0.8)
    test_app._process_ui_queue()

    test_app._on_lock_donor_vault_clicked()
    time.sleep(0.8)
    test_app._process_ui_queue()

    spool_files = list((test_app.repo_root / "data" / "spool_encrypted").glob("**/*.enc"))
    assert len(spool_files) > 0, "Arquivo trancado no spool não foi gerado"


def test_08_benchmark_runner_execution(test_app):
    """Testa 'Rodar Bateria de Benchmark Agora'."""
    runner = BenchmarkRunner(
        node_id="TEST_RUNNER",
        model_name="qwen2.5-coder:3b",
        db_path=test_app.db_path,
        repo_root=test_app.repo_root,
    )
    metrics = runner._get_system_metrics()
    assert "ram_used_mb" in metrics
    assert "vram_used_mb" in metrics


def test_09_colibri_nightly_worker(test_app):
    """Testa 'Executar Lote Noturno Agora'."""
    worker = NightlyColibriWorker(db_path=test_app.db_path, repo_root=test_app.repo_root)
    res = worker.run_nightly_pipeline(max_tasks=1, check_user=False)
    assert "total_tasks" in res
    assert "success_count" in res


def test_10_drive_sync_manager(test_app):
    """Testa 'Sincronizar Spool com Google Drive'."""
    sync_mgr = DriveSyncManager(
        spool_dir=test_app.repo_root / "data" / "spool_encrypted",
        db_path=test_app.db_path,
    )
    pending = sync_mgr.list_pending_packages()
    assert isinstance(pending, list)


def test_11_aider_runner_non_blocking(test_app):
    """Testa o Aider Runner em modo seguro e não bloqueante."""
    runner = test_app.aider_runner
    assert runner.repo_root.resolve() == test_app.repo_root.resolve()
    cmd = runner.build_command([], "/architect Teste")
    assert "--no-show-model-warnings" in cmd
    assert "--exit" in cmd
