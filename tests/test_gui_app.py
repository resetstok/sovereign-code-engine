"""
Testes Unitários para a Interface Gráfica do Operador (src/pci_engine/gui_app.py - Etapa 7).
Verifica a inicialização da janela principal (1150x720), estados reativos,
barra lateral fixa de hardware, preenchimento das tabelas SQLite e execução assíncrona.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.gui_app import PCIMasterGUI


@pytest.fixture
def mock_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "data" / "jobs.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(db_path=db_file)

    # Registra nó mestre e doador para testes
    db.register_node(
        node_id="MASTER_NODE",
        node_name="PC_MASTER",
        role="MASTER",
        ram_total_mb=8192,
        vram_total_mb=4096,
        ssd_free_gb=120.0,
        supported_models=["qwen2.5-coder:3b"],
        status="ONLINE",
    )
    db.register_node(
        node_id="DONOR_TEST_01",
        node_name="PC_DOADOR_ALPHA",
        role="DONOR",
        ram_total_mb=16384,
        vram_total_mb=8192,
        ssd_free_gb=200.0,
        supported_models=["qwen2.5-coder:7b"],
        status="ONLINE",
    )

    # Insere tarefa de teste
    db.insert_task(
        task_id="TASK_GUI_001",
        parent_masterbook="MB_GUI",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=30,
        spec_markdown="spec",
        test_code="def test_gui(): pass",
        target_file_path="src/gui_sample.py",
        status="PENDING",
    )

    # Insere capability de teste
    db.record_capability(
        node_id="MASTER_NODE",
        model_name="qwen2.5-coder:3b",
        task_type="EXTRACTION",
        sample_size=4,
        success_rate=0.92,
        avg_tokens_per_sec=35.5,
        verification_state="VERIFIED",
        benchmark_version="v2.1",
    )

    return db


@pytest.fixture
def gui_instance(mock_db: DatabaseManager, tmp_path: Path):
    """Instancia a GUI de forma controlada para testes."""
    try:
        import customtkinter as ctk
        root = ctk.CTk()
    except Exception:
        root = tk.Tk()

    root.withdraw()  # Esconde a janela durante os testes automatizados
    app = PCIMasterGUI(root=root, repo_root=tmp_path)
    yield app
    app._monitoring_active = False
    try:
        root.destroy()
    except Exception:
        pass


def test_gui_initial_state_and_components(gui_instance: PCIMasterGUI):
    """Verifica se os estados reativos e componentes foram inicializados corretamente."""
    assert gui_instance.is_hybrid_mode.get() is False
    assert gui_instance.is_broker_running.get() is False
    # O interruptor explícito obrigatório de remoção de PII deve estar ativado por padrão
    assert gui_instance.remove_pii_var.get() is True
    assert gui_instance.encrypt_spool_var.get() is True

    # Verifica se os widgets principais existem
    assert gui_instance.sidebar is not None
    assert gui_instance.tabview is not None
    assert gui_instance.txt_masterbook is not None
    assert gui_instance.tree_tasks is not None
    assert gui_instance.tree_donors is not None
    assert gui_instance.tree_matrix is not None


def test_gui_probe_ollama_status():
    """Verifica a rotina não bloqueante de verificação do Ollama."""
    online = PCIMasterGUI._probe_ollama_status()
    assert isinstance(online, bool)


def test_gui_refresh_tables(gui_instance: PCIMasterGUI):
    """Verifica se as tabelas da interface carregam dados das 5 tabelas do jobs.db."""
    # 1. Tabela de Tarefas
    gui_instance.refresh_tasks_table()
    task_items = gui_instance.tree_tasks.get_children()
    assert len(task_items) >= 1
    first_task = gui_instance.tree_tasks.item(task_items[0])["values"]
    assert "TASK_GUI_001" in first_task

    # 2. Tabela de Doadores
    gui_instance.refresh_donors_table()
    donor_items = gui_instance.tree_donors.get_children()
    assert len(donor_items) >= 2  # MASTER_NODE e DONOR_TEST_01

    # 3. Tabela da Matriz de Capacidades
    gui_instance.refresh_matrix_table()
    matrix_items = gui_instance.tree_matrix.get_children()
    assert len(matrix_items) >= 1
    first_cap = gui_instance.tree_matrix.item(matrix_items[0])["values"]
    assert "EXTRACTION" in first_cap
    assert "92.0%" in first_cap or "0.92" in str(first_cap)


def test_gui_logging_append(gui_instance: PCIMasterGUI):
    """Testa a escrita de logs em tempo real nos consoles das abas."""
    gui_instance.log("Mensagem de teste no console principal")
    gui_instance.log_nightly("Mensagem de teste no console noturno")

    # Processa fila thread-safe e atualiza UI
    gui_instance._process_ui_queue()
    gui_instance.root.update()

    main_log = gui_instance.txt_logs.get("1.0", "end")
    nightly_log = gui_instance.txt_nightly_log.get("1.0", "end")

    assert "Mensagem de teste no console principal" in main_log
    assert "Mensagem de teste no console noturno" in nightly_log


def test_gui_hybrid_switch_toggle(gui_instance: PCIMasterGUI):
    """Verifica o alternador de Modo 100% Offline vs Modo Híbrido."""
    gui_instance.is_hybrid_mode.set(True)
    gui_instance._on_hybrid_switch_toggled()
    assert gui_instance.is_hybrid_mode.get() is True

    gui_instance.is_hybrid_mode.set(False)
    gui_instance._on_hybrid_switch_toggled()
    assert gui_instance.is_hybrid_mode.get() is False


def test_gui_forensic_sanitizer_flow(gui_instance: PCIMasterGUI, tmp_path: Path):
    """Testa a geração de cópia derivada sanitizada acionada a partir da interface."""
    audit_file = tmp_path / "documento_investigacao.txt"
    audit_file.write_text("Investigado: Fulano de Tal, CPF: 123.456.789-00, PIX: fulano@gmail.com", encoding="utf-8")

    gui_instance.file_path_var.set(str(audit_file))
    gui_instance._on_generate_sanitized_derivative()

    # Aguarda a thread de processamento concluir
    import time
    for _ in range(30):
        time.sleep(0.1)
        gui_instance._process_ui_queue()
        gui_instance.root.update()
        output = gui_instance.sanitizer_output_box.get("1.0", "end")
        if "RELATÓRIO DE CUSTÓDIA FORENSE" in output:
            break

    output = gui_instance.sanitizer_output_box.get("1.0", "end")
    assert "RELATÓRIO DE CUSTÓDIA FORENSE" in output
    assert "SHA-256 Original:" in output
    assert "SHA-256 Derivada:" in output
