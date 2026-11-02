"""
Módulo: pci_engine/gui_app.py
Aplicativo Desktop Completo do Operador do PCI Sovereign Code Engine v2.1.
Interface gráfica moderna (CustomTkinter) em modo Dark/Cyber, projetada para consumir
menos de 30 MB de RAM e fornecer controle total sem necessidade de comandos de terminal.

Janela Principal (1150x720):
- Barra Lateral Fixa: Monitor de Hardware em tempo real (RAM, VRAM, SSD, Ollama),
  botão de higiene de RAM, alternador de Modo Híbrido/Offline e ativação do Broker de Doadores.
- Aba 1: Masterbook & Fila de Execução (Decomposição, tabela de tarefas e console de logs ao vivo).
- Aba 2: Custódia Forense & Sanitizador OSINT (Seletor, chave de remoção de PII, hashes SHA-256).
- Aba 3: Rede de Doadores & Matriz de Capacidades (86%-96%, Kudos e geração de instalador EXE).
- Aba 4: Colibrì Noturno & Sincronização (Fila noturna 14B, execução manual e sync rclone para Google Drive).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Garante que a raiz do repositório e src/ estejam no sys.path mesmo se iniciado via duplo-clique
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

import psutil

try:
    import customtkinter as ctk

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
except ImportError:
    import tkinter as ctk

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pci_engine.database import DatabaseManager
from pci_engine.decomposer import MasterbookDecomposer
from pci_engine.drive_sync import DriveSyncManager
from pci_engine.micro_harness import MicroHarness
from pci_engine.nightly_colibri import NightlyColibriWorker
from pci_engine.preflight import PreflightChecker
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.router import PCIRouter
from pci_engine.sanitizer import ForensicSanitizer
from pci_engine.benchmark_runner import BenchmarkRunner

logger = logging.getLogger("pci_engine.gui_app")


class PCIMasterGUI:
    """Controlador principal da interface gráfica CustomTkinter do PCI Engine."""

    def __init__(self, root: Optional[ctk.CTk] = None, repo_root: Path | str = "."):
        self.repo_root = Path(repo_root).resolve()
        self.db_path = self.repo_root / "data" / "jobs.db"
        self.db = DatabaseManager(db_path=self.db_path)

        self.root = root or ctk.CTk()
        self.root.title("PCI Sovereign Code Engine v2.1 — Master Control Console")
        self.root.geometry("1150x720")
        self.root.minsize(1050, 680)

        # Estados reativos
        self.is_hybrid_mode = tk.BooleanVar(value=False)
        self.is_broker_running = tk.BooleanVar(value=False)
        self.remove_pii_var = tk.BooleanVar(value=True)  # Interruptor explícito obrigatório
        self.encrypt_spool_var = tk.BooleanVar(value=True)

        self.ram_status_var = tk.StringVar(value="RAM: Calculando...")
        self.vram_status_var = tk.StringVar(value="VRAM: Calculando...")
        self.ssd_status_var = tk.StringVar(value="SSD Livre: Calculando...")
        self.ollama_status_var = tk.StringVar(value="Ollama: Checando...")
        self.broker_status_var = tk.StringVar(value="Broker: Inativo")
        self.api_key_var = tk.StringVar(value="")

        self._broker_process: Optional[subprocess.Popen] = None
        self._monitoring_active = True
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        self._setup_ui()
        self._process_ui_queue()
        self._start_metrics_poller()

    def dispatch_to_ui(self, fn: Callable[[], None]) -> None:
        """Enfileira com segurança de thread uma chamada para a thread de interface."""
        self._ui_queue.put(fn)

    def _process_ui_queue(self) -> None:
        """Consome a fila de callbacks na thread principal do Tkinter."""
        try:
            while not self._ui_queue.empty():
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception as exc:
                    logger.debug(f"Erro em callback de UI: {exc}")
        except Exception:
            pass
        finally:
            if self._monitoring_active and self.root:
                try:
                    self.root.after(40, self._process_ui_queue)
                except Exception:
                    pass

    # =========================================================================
    # CONSTRUÇÃO DO LAYOUT PRINCIPAL
    # =========================================================================
    def _setup_ui(self) -> None:
        """Monta a barra lateral fixa e o sistema de abas principais."""
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # ---------------------------------------------------------------------
        # 1. BARRA LATERAL FIXA (Monitor de Hardware e Controles Rápidos)
        # ---------------------------------------------------------------------
        self.sidebar = ctk.CTkFrame(self.root, width=280, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=0)
        self.sidebar.grid_propagate(False)

        # Cabeçalho da Sidebar
        title_lbl = ctk.CTkLabel(
            self.sidebar,
            text="⚡ PCI ENGINE v2.1",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#00f5d4",
        )
        title_lbl.pack(pady=(15, 2), padx=15, anchor="w")

        sub_lbl = ctk.CTkLabel(
            self.sidebar,
            text="Sovereign Master System\n8GB RAM / GTX 1650 4GB",
            font=ctk.CTkFont(size=11),
            text_color="#a89984",
            justify="left",
        )
        sub_lbl.pack(pady=(0, 15), padx=15, anchor="w")

        # Cartão de Monitoramento de Recursos Físicos
        hw_card = ctk.CTkFrame(self.sidebar, corner_radius=8, fg_color="#1a1e24")
        hw_card.pack(fill="x", padx=12, pady=5)

        ctk.CTkLabel(
            hw_card,
            text="MONITOR FÍSICO (AO VIVO)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#3a86ff",
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self.lbl_ram = ctk.CTkLabel(hw_card, textvariable=self.ram_status_var, font=ctk.CTkFont(size=12))
        self.lbl_ram.pack(anchor="w", padx=10, pady=2)
        self.prog_ram = ctk.CTkProgressBar(hw_card, height=8)
        self.prog_ram.pack(fill="x", padx=10, pady=(0, 6))

        self.lbl_vram = ctk.CTkLabel(hw_card, textvariable=self.vram_status_var, font=ctk.CTkFont(size=12))
        self.lbl_vram.pack(anchor="w", padx=10, pady=2)

        self.lbl_ssd = ctk.CTkLabel(hw_card, textvariable=self.ssd_status_var, font=ctk.CTkFont(size=12))
        self.lbl_ssd.pack(anchor="w", padx=10, pady=2)

        self.lbl_ollama = ctk.CTkLabel(hw_card, textvariable=self.ollama_status_var, font=ctk.CTkFont(size=12))
        self.lbl_ollama.pack(anchor="w", padx=10, pady=(2, 8))

        # Botão Higienizar RAM
        btn_clean_ram = ctk.CTkButton(
            self.sidebar,
            text="🧹 Higienizar RAM Agora",
            command=self._on_clean_ram_clicked,
            fg_color="#3a86ff",
            hover_color="#2667d4",
            height=32,
        )
        btn_clean_ram.pack(fill="x", padx=12, pady=(12, 6))

        # Separador
        sep = ctk.CTkFrame(self.sidebar, height=2, fg_color="#2b303c")
        sep.pack(fill="x", padx=12, pady=10)

        # Chave Seletora de Modo (Offline vs Híbrido)
        self.switch_hybrid = ctk.CTkSwitch(
            self.sidebar,
            text="Modo Híbrido (+ APIs)",
            variable=self.is_hybrid_mode,
            command=self._on_hybrid_switch_toggled,
            onvalue=True,
            offvalue=False,
        )
        self.switch_hybrid.pack(anchor="w", padx=15, pady=4)

        # Campo da Chave de API externa
        self.api_key_entry = ctk.CTkEntry(
            self.sidebar,
            placeholder_text="API Key (Groq / OpenRouter)",
            textvariable=self.api_key_var,
            show="•",
            height=28,
            font=ctk.CTkFont(size=11),
        )
        self.api_key_entry.pack(fill="x", padx=12, pady=(2, 10))

        # Chave Seletora do Broker de Doadores (FastAPI Tailscale)
        self.switch_broker = ctk.CTkSwitch(
            self.sidebar,
            text="Broker Doadores (Tailscale)",
            variable=self.is_broker_running,
            command=self._on_broker_switch_toggled,
            onvalue=True,
            offvalue=False,
        )
        self.switch_broker.pack(anchor="w", padx=15, pady=4)

        self.lbl_broker_status = ctk.CTkLabel(
            self.sidebar,
            textvariable=self.broker_status_var,
            font=ctk.CTkFont(size=11),
            text_color="#8ec07c",
        )
        self.lbl_broker_status.pack(anchor="w", padx=15, pady=(0, 10))

        # Rodapé da Barra Lateral
        lbl_version = ctk.CTkLabel(
            self.sidebar,
            text="Tailscale Port: 8000\nOllama Port: 11434",
            font=ctk.CTkFont(size=10),
            text_color="#504945",
            justify="left",
        )
        lbl_version.pack(side="bottom", anchor="w", padx=15, pady=10)

        # ---------------------------------------------------------------------
        # 2. ÁREA CENTRAL — SISTEMA DE ABAS PRINCIPAIS
        # ---------------------------------------------------------------------
        self.tabview = ctk.CTkTabview(self.root, corner_radius=8)
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=10, pady=5)

        tab_mb = self.tabview.add("Masterbook & Fila")
        tab_sanitizer = self.tabview.add("Custódia Forense & OSINT")
        tab_donors = self.tabview.add("Rede de Doadores & Matriz")
        tab_nightly = self.tabview.add("Colibrì Noturno & Sync")

        self._build_tab_masterbook(tab_mb)
        self._build_tab_sanitizer(tab_sanitizer)
        self._build_tab_donors(tab_donors)
        self._build_tab_nightly(tab_nightly)

    # =========================================================================
    # ABA 1: MASTERBOOK & FILA DE EXECUÇÃO
    # =========================================================================
    def _build_tab_masterbook(self, parent: ctk.CTkFrame) -> None:
        """Monta o painel de entrada de Masterbook, fila de tarefas e logs."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=2)
        parent.grid_rowconfigure(3, weight=3)
        parent.grid_rowconfigure(5, weight=2)

        # Cabeçalho da Aba
        top_frame = ctk.CTkFrame(parent, fg_color="transparent")
        top_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 2))

        ctk.CTkLabel(
            top_frame,
            text="Masterbook (Linguagem Natural do Operador):",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

        btn_load_mb = ctk.CTkButton(
            top_frame,
            text="📂 Carregar .MD",
            width=110,
            height=26,
            command=self._on_load_masterbook_file,
        )
        btn_load_mb.pack(side="right", padx=5)

        # Caixa de Texto do Masterbook
        self.txt_masterbook = ctk.CTkTextbox(parent, height=130, wrap="word", font=ctk.CTkFont(size=12))
        self.txt_masterbook.grid(row=1, column=0, sticky="nsew", padx=5, pady=2)
        self.txt_masterbook.insert(
            "1.0",
            "# Especificação de Sistema\n"
            "Construa um módulo em src/analytics.py com uma função calcular_metricas(dados: list) -> dict\n"
            "que extraia o total, a média e descarte valores negativos com validação estrita.\n",
        )

        # Barra de Ações da Fila
        actions_bar = ctk.CTkFrame(parent, fg_color="transparent")
        actions_bar.grid(row=2, column=0, sticky="ew", padx=5, pady=6)

        btn_decompose = ctk.CTkButton(
            actions_bar,
            text="1. 🧠 Decompor em Microtarefas (JSON)",
            command=self._on_decompose_clicked,
            fg_color="#00f5d4",
            text_color="#121417",
            hover_color="#00bbf9",
            height=32,
            font=ctk.CTkFont(weight="bold"),
        )
        btn_decompose.pack(side="left", padx=(0, 8))

        btn_run_queue = ctk.CTkButton(
            actions_bar,
            text="2. 🚀 Iniciar Execução Autônoma",
            command=self._on_run_autonomous_queue_clicked,
            fg_color="#ff007f",
            hover_color="#d6006b",
            height=32,
            font=ctk.CTkFont(weight="bold"),
        )
        btn_run_queue.pack(side="left", padx=8)

        btn_refresh_tasks = ctk.CTkButton(
            actions_bar,
            text="🔄 Atualizar Fila",
            width=100,
            height=32,
            command=self.refresh_tasks_table,
        )
        btn_refresh_tasks.pack(side="right")

        # Tabela Visual de Tarefas (Jobs DB)
        table_container = ctk.CTkFrame(parent, corner_radius=6)
        table_container.grid(row=3, column=0, sticky="nsew", padx=5, pady=2)
        table_container.grid_columnconfigure(0, weight=1)
        table_container.grid_rowconfigure(0, weight=1)

        columns = ("task_id", "task_type", "assigned_route", "privacy_level", "status", "attempts")
        self.tree_tasks = ttk.Treeview(table_container, columns=columns, show="headings", height=6)
        self.tree_tasks.heading("task_id", text="ID da Tarefa")
        self.tree_tasks.heading("task_type", text="Tipo")
        self.tree_tasks.heading("assigned_route", text="Rota")
        self.tree_tasks.heading("privacy_level", text="Privacidade")
        self.tree_tasks.heading("status", text="Status")
        self.tree_tasks.heading("attempts", text="Tentativas")

        self.tree_tasks.column("task_id", width=180, anchor="w")
        self.tree_tasks.column("task_type", width=90, anchor="center")
        self.tree_tasks.column("assigned_route", width=100, anchor="center")
        self.tree_tasks.column("privacy_level", width=110, anchor="center")
        self.tree_tasks.column("status", width=120, anchor="center")
        self.tree_tasks.column("attempts", width=70, anchor="center")

        scrollbar_tasks = ttk.Scrollbar(table_container, orient="vertical", command=self.tree_tasks.yview)
        self.tree_tasks.configure(yscrollcommand=scrollbar_tasks.set)

        self.tree_tasks.grid(row=0, column=0, sticky="nsew")
        scrollbar_tasks.grid(row=0, column=1, sticky="ns")

        # Painel Inferior de Logs de Execução em Tempo Real
        lbl_logs = ctk.CTkLabel(
            parent,
            text="Diário de Execução & Saída do Pytest (Tempo Real):",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        lbl_logs.grid(row=4, column=0, sticky="w", padx=5, pady=(8, 2))

        self.txt_logs = ctk.CTkTextbox(parent, height=120, wrap="word", font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_logs.grid(row=5, column=0, sticky="nsew", padx=5, pady=(0, 5))

    # =========================================================================
    # ABA 2: CUSTÓDIA FORENSE & SANITIZADOR OSINT
    # =========================================================================
    def _build_tab_sanitizer(self, parent: ctk.CTkFrame) -> None:
        """Painel com interruptor explícito de remoção de PII e cálculo de hashes."""
        parent.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            parent,
            text="Cadeia de Custódia Forense & Redação de PII (OSINT)",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#00f5d4",
        )
        title.pack(anchor="w", padx=15, pady=(15, 5))

        desc = ctk.CTkLabel(
            parent,
            text="Gera cópias derivadas higienizadas para envio seguro à nuvem ou doadores,\n"
            "preservando o arquivo original intocado com registro de integridade SHA-256.",
            font=ctk.CTkFont(size=12),
            text_color="gray",
            justify="left",
        )
        desc.pack(anchor="w", padx=15, pady=(0, 15))

        # Seleção de Arquivo Bruto
        file_frame = ctk.CTkFrame(parent)
        file_frame.pack(fill="x", padx=15, pady=5)

        self.file_path_var = tk.StringVar(value="")
        lbl_file = ctk.CTkLabel(file_frame, text="Arquivo Bruto:")
        lbl_file.pack(side="left", padx=10, pady=10)

        self.entry_file_path = ctk.CTkEntry(
            file_frame,
            textvariable=self.file_path_var,
            placeholder_text="Caminho do arquivo de auditoria...",
            width=500,
        )
        self.entry_file_path.pack(side="left", fill="x", expand=True, padx=5, pady=10)

        btn_browse = ctk.CTkButton(
            file_frame,
            text="📁 Procurar",
            width=90,
            command=self._on_browse_forensic_file,
        )
        btn_browse.pack(side="left", padx=10, pady=10)

        # Interruptor Explícito Obrigatório de PII
        opts_frame = ctk.CTkFrame(parent, fg_color="transparent")
        opts_frame.pack(fill="x", padx=15, pady=10)

        self.switch_pii = ctk.CTkSwitch(
            opts_frame,
            text="[x] Remover metadados e PII (CPF / CNPJ / Telefones / E-mails / Chaves PIX) da cópia derivada",
            variable=self.remove_pii_var,
            onvalue=True,
            offvalue=False,
            font=ctk.CTkFont(size=13, weight="bold"),
            progress_color="#00f5d4",
        )
        self.switch_pii.pack(anchor="w", pady=4)

        self.switch_spool = ctk.CTkSwitch(
            opts_frame,
            text="Salvar cópia cifrada em data/spool_encrypted/ (Criptografia Fernet AES)",
            variable=self.encrypt_spool_var,
            onvalue=True,
            offvalue=False,
        )
        self.switch_spool.pack(anchor="w", pady=4)

        btn_generate_derived = ctk.CTkButton(
            parent,
            text="🛡️ Gerar Derivada Auditável com Hash Imutável",
            command=self._on_generate_sanitized_derivative,
            fg_color="#00f5d4",
            text_color="#121417",
            hover_color="#00bbf9",
            height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        btn_generate_derived.pack(anchor="w", padx=15, pady=15)

        # Painel de Saída de Hashes e Dados Sanitizados
        self.sanitizer_output_box = ctk.CTkTextbox(parent, height=280, font=ctk.CTkFont(family="Consolas", size=11))
        self.sanitizer_output_box.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.sanitizer_output_box.insert(
            "1.0",
            "Aguardando processamento de arquivo...\n"
            "Selecione um documento e clique em 'Gerar Derivada Auditável'.\n",
        )

    # =========================================================================
    # ABA 3: REDE DE DOADORES & MATRIZ DE CAPACIDADES
    # =========================================================================
    def _build_tab_donors(self, parent: ctk.CTkFrame) -> None:
        """Exibe nós voluntários da VPN Tailscale e a matriz medida (86%-96%)."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_rowconfigure(3, weight=1)

        # Seção A: Doadores
        header_donors = ctk.CTkFrame(parent, fg_color="transparent")
        header_donors.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        ctk.CTkLabel(
            header_donors,
            text="Nós Doadores Conectados via Tailscale:",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        btn_build_donor_exe = ctk.CTkButton(
            header_donors,
            text="📦 Gerar Instalador .EXE para Novo Doador",
            command=self._on_build_donor_exe_clicked,
            fg_color="#f77f00",
            hover_color="#d66800",
            height=28,
        )
        btn_build_donor_exe.pack(side="right", padx=5)

        btn_refresh_donors = ctk.CTkButton(
            header_donors,
            text="🔄 Atualizar Doadores",
            width=120,
            height=28,
            command=self.refresh_donors_table,
        )
        btn_refresh_donors.pack(side="right", padx=5)

        frame_tree_donors = ctk.CTkFrame(parent)
        frame_tree_donors.grid(row=1, column=0, sticky="nsew", padx=10, pady=2)
        frame_tree_donors.grid_columnconfigure(0, weight=1)
        frame_tree_donors.grid_rowconfigure(0, weight=1)

        cols_donors = ("node_id", "node_name", "ram_vram", "status", "kudos_balance", "last_heartbeat")
        self.tree_donors = ttk.Treeview(frame_tree_donors, columns=cols_donors, show="headings", height=4)
        self.tree_donors.heading("node_id", text="ID do Nó")
        self.tree_donors.heading("node_name", text="Nome da Máquina")
        self.tree_donors.heading("ram_vram", text="RAM / VRAM")
        self.tree_donors.heading("status", text="Status")
        self.tree_donors.heading("kudos_balance", text="Kudos Acumulados")
        self.tree_donors.heading("last_heartbeat", text="Último Pulso")

        self.tree_donors.column("node_id", width=180, anchor="w")
        self.tree_donors.column("node_name", width=140, anchor="w")
        self.tree_donors.column("ram_vram", width=130, anchor="center")
        self.tree_donors.column("status", width=90, anchor="center")
        self.tree_donors.column("kudos_balance", width=120, anchor="center")
        self.tree_donors.column("last_heartbeat", width=160, anchor="center")

        self.tree_donors.grid(row=0, column=0, sticky="nsew")

        # Seção B: Matriz de Capacidades
        header_matrix = ctk.CTkFrame(parent, fg_color="transparent")
        header_matrix.grid(row=2, column=0, sticky="ew", padx=10, pady=(15, 4))

        ctk.CTkLabel(
            header_matrix,
            text="Matriz de Desempenho Aferida (Meta 86% a 96%):",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#00f5d4",
        ).pack(side="left")

        btn_run_bench = ctk.CTkButton(
            header_matrix,
            text="🚀 Rodar Bateria de Benchmark Agora",
            command=self._on_run_benchmark_clicked,
            fg_color="#3a86ff",
            hover_color="#2667d4",
            height=28,
            font=ctk.CTkFont(weight="bold"),
        )
        btn_run_bench.pack(side="right", padx=5)

        frame_tree_matrix = ctk.CTkFrame(parent)
        frame_tree_matrix.grid(row=3, column=0, sticky="nsew", padx=10, pady=2)
        frame_tree_matrix.grid_columnconfigure(0, weight=1)
        frame_tree_matrix.grid_rowconfigure(0, weight=1)

        cols_matrix = ("node_id", "model_name", "task_type", "sample_size", "success_rate", "tokens_sec", "state")
        self.tree_matrix = ttk.Treeview(frame_tree_matrix, columns=cols_matrix, show="headings", height=5)
        self.tree_matrix.heading("node_id", text="Nó Aferido")
        self.tree_matrix.heading("model_name", text="Modelo")
        self.tree_matrix.heading("task_type", text="Categoria")
        self.tree_matrix.heading("sample_size", text="Amostras")
        self.tree_matrix.heading("success_rate", text="Taxa de Acerto")
        self.tree_matrix.heading("tokens_sec", text="Tokens/s")
        self.tree_matrix.heading("state", text="Estado de Qualificação")

        self.tree_matrix.column("node_id", width=160, anchor="w")
        self.tree_matrix.column("model_name", width=130, anchor="w")
        self.tree_matrix.column("task_type", width=120, anchor="center")
        self.tree_matrix.column("sample_size", width=80, anchor="center")
        self.tree_matrix.column("success_rate", width=110, anchor="center")
        self.tree_matrix.column("tokens_sec", width=90, anchor="center")
        self.tree_matrix.column("state", width=130, anchor="center")

        self.tree_matrix.grid(row=0, column=0, sticky="nsew")

    # =========================================================================
    # ABA 4: COLIBRÌ NOTURNO & CONFIGURAÇÕES / DRIVE SYNC
    # =========================================================================
    def _build_tab_nightly(self, parent: ctk.CTkFrame) -> None:
        """Painel de controle do processamento noturno Colibrì e sincronização Google Drive."""
        parent.grid_columnconfigure(0, weight=1)

        # Seção Colibrì
        colibri_frame = ctk.CTkFrame(parent)
        colibri_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(
            colibri_frame,
            text="Processamento Noturno Colibrì (Modelos 14B via --mmap 1)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#00f5d4",
        ).pack(anchor="w", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            colibri_frame,
            text="Executa refatorações pesadas na madrugada lendo do SSD NVMe sem congelar o sistema.\n"
            "Varre automaticamente anotações '# REFACTOR_NIGHTLY' no código-fonte do projeto.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 10))

        btn_row_colibri = ctk.CTkFrame(colibri_frame, fg_color="transparent")
        btn_row_colibri.pack(fill="x", padx=12, pady=(0, 10))

        btn_run_nightly_now = ctk.CTkButton(
            btn_row_colibri,
            text="🌙 Executar Lote Noturno Agora",
            command=self._on_run_nightly_now_clicked,
            fg_color="#3a86ff",
            hover_color="#2667d4",
            height=30,
        )
        btn_run_nightly_now.pack(side="left", padx=(0, 10))

        btn_register_sched = ctk.CTkButton(
            btn_row_colibri,
            text="⏰ Agendar no Windows (03:00 AM)",
            command=self._on_register_windows_task_clicked,
            fg_color="#458588",
            hover_color="#076678",
            height=30,
        )
        btn_register_sched.pack(side="left")

        # Seção Google Drive Sync via Rclone
        sync_frame = ctk.CTkFrame(parent)
        sync_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(
            sync_frame,
            text="Sincronização de Spool Criptografado (Google Drive via Rclone)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#f77f00",
        ).pack(anchor="w", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            sync_frame,
            text="Envia pacotes criptografados da pasta data/spool_encrypted/ para o Google Drive.\n"
            "Conferência criptográfica obrigatória: o arquivo local SÓ é apagado se o hash remoto for idêntico.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 10))

        btn_sync_drive = ctk.CTkButton(
            sync_frame,
            text="☁️ Sincronizar Spool com Google Drive",
            command=self._on_sync_drive_clicked,
            fg_color="#f77f00",
            hover_color="#d66800",
            height=32,
            font=ctk.CTkFont(weight="bold"),
        )
        btn_sync_drive.pack(anchor="w", padx=12, pady=(0, 10))

        # Log do Painel Noturno e Sync
        ctk.CTkLabel(
            parent,
            text="Log de Operações Noturnas & Sincronização:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(10, 2))

        self.txt_nightly_log = ctk.CTkTextbox(parent, height=180, font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_nightly_log.pack(fill="both", expand=True, padx=15, pady=(0, 15))

    # =========================================================================
    # MONITORAMENTO EM TEMPO REAL (MÉTRICAS DE HARDWARE A CADA 3S)
    # =========================================================================
    def _start_metrics_poller(self) -> None:
        """Dispara a thread secundária de auditoria contínua de recursos."""
        def poller_loop():
            while self._monitoring_active:
                try:
                    # 1. RAM
                    vm = psutil.virtual_memory()
                    ram_used_gb = (vm.total - vm.available) / (1024**3)
                    ram_total_gb = vm.total / (1024**3)
                    ram_pct = vm.percent

                    # 2. VRAM da GPU NVIDIA
                    gpu = PreflightChecker.get_gpu_info()
                    if gpu.get("available"):
                        vram_used = gpu.get("vram_used_mb", 0)
                        vram_total = gpu.get("vram_total_mb", 4096)
                        vram_text = f"VRAM GTX 1650: {vram_used} MB / {vram_total} MB"
                    else:
                        vram_text = "VRAM: N/A (GPU Indisponível)"

                    # 3. SSD Livre
                    disk = psutil.disk_usage(str(self.repo_root))
                    free_gb = disk.free / (1024**3)
                    if free_gb < 40.0:
                        ssd_text = f"SSD Livre: {free_gb:.1f} GB ⚠️ CRÍTICO (<40GB)"
                    else:
                        ssd_text = f"SSD Livre: {free_gb:.1f} GB (Seguro)"

                    # 4. Ollama Local
                    ollama_online = self._probe_ollama_status()
                    ollama_text = "Ollama 3B: ONLINE (11434)" if ollama_online else "Ollama 3B: OFFLINE"

                    # Atualiza variáveis Tkinter na thread principal
                    def update_vars(r_u=ram_used_gb, r_t=ram_total_gb, r_p=ram_pct, v_t=vram_text, s_t=ssd_text, o_t=ollama_text):
                        self.ram_status_var.set(f"RAM: {r_u:.1f} / {r_t:.1f} GB ({r_p:.0f}%)")
                        self.prog_ram.set(r_p / 100.0)
                        self.vram_status_var.set(v_t)
                        self.ssd_status_var.set(s_t)
                        self.ollama_status_var.set(o_t)

                    self.dispatch_to_ui(update_vars)
                except Exception as exc:
                    logger.debug(f"Erro no monitor de métricas: {exc}")

                time.sleep(3.0)

        threading.Thread(target=poller_loop, daemon=True).start()

    @staticmethod
    def _probe_ollama_status() -> bool:
        """Verifica de forma rápida e não bloqueante se o Ollama está respondendo."""
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=1.2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def log(self, message: str) -> None:
        """Adiciona log ao console principal de execução."""
        def _append():
            timestamp = time.strftime("%H:%M:%S")
            self.txt_logs.insert("end", f"[{timestamp}] {message}\n")
            self.txt_logs.see("end")

        self.dispatch_to_ui(_append)

    def log_nightly(self, message: str) -> None:
        """Adiciona log ao console da aba Colibrì Noturno."""
        def _append():
            timestamp = time.strftime("%H:%M:%S")
            self.txt_nightly_log.insert("end", f"[{timestamp}] {message}\n")
            self.txt_nightly_log.see("end")

        self.dispatch_to_ui(_append)

    # =========================================================================
    # CONTROLADORES DE EVENTOS (ASYNC THREADING PARA ZERO FREEZE)
    # =========================================================================

    def _on_clean_ram_clicked(self) -> None:
        """Executa limpeza de RAM e caches em background."""
        def _run():
            self.log("Iniciando rotina de higiene de memória RAM e limpeza de caches temporários...")
            hygiene = RAMLiberator.execute_hygiene_pipeline(auto_terminate=False, repo_root=self.repo_root)
            self.log(
                f"Higiene concluída com sucesso! Total liberado: {hygiene['total_freed_mb']} MB "
                f"(RAM livre agora: {hygiene['ram_after_mb']} MB)."
            )

        threading.Thread(target=_run, daemon=True).start()

    def _on_hybrid_switch_toggled(self) -> None:
        """Alterna entre modo 100% offline e modo híbrido com APIs gratuitas."""
        if self.is_hybrid_mode.get():
            self.log("Modo Híbrido ATIVADO: Tarefas complexas poderão ser roteadas para APIs autorizadas.")
            self.api_key_entry.configure(state="normal")
        else:
            self.log("Modo 100% Offline ATIVADO: Todos os dados ficam estritamente confinados no PC local.")

    def _on_broker_switch_toggled(self) -> None:
        """Inicia ou desliga o servidor FastAPI do Broker para doadores da Tailscale."""
        if self.is_broker_running.get():
            self.log("Iniciando Broker FastAPI na porta 8000 para a rede Tailscale...")

            def _start_server():
                try:
                    cmd = [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "pci_engine.broker_api:app",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        "8000",
                    ]
                    self._broker_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.dispatch_to_ui(lambda: self.broker_status_var.set("Broker: ONLINE (Porta 8000)"))
                    self.log("Broker FastAPI ATIVO! Pronto para receber solicitações de Pull & Lease de doadores.")
                except Exception as exc:
                    self.log(f"Falha ao iniciar servidor broker: {exc}")
                    self.dispatch_to_ui(lambda: self.is_broker_running.set(False))

            threading.Thread(target=_start_server, daemon=True).start()
        else:
            self.log("Encerrando Broker FastAPI...")
            if self._broker_process:
                self._broker_process.terminate()
                self._broker_process = None
            self.broker_status_var.set("Broker: Inativo")
            self.log("Broker desativado.")

    def _on_load_masterbook_file(self) -> None:
        """Carrega o texto de um arquivo .md selecionado pelo usuário."""
        chosen = filedialog.askopenfilename(
            title="Selecionar Masterbook Markdown",
            filetypes=[("Arquivos Markdown", "*.md"), ("Texto", "*.txt"), ("Todos", "*.*")],
            initialdir=str(self.repo_root / "tasks"),
        )
        if chosen:
            content = Path(chosen).read_text(encoding="utf-8", errors="ignore")
            self.txt_masterbook.delete("1.0", "end")
            self.txt_masterbook.insert("1.0", content)
            self.log(f"Arquivo Masterbook carregado: {Path(chosen).name}")

    def _on_decompose_clicked(self) -> None:
        """Decompõe o Masterbook em microtarefas estruturadas (DAG)."""
        content = self.txt_masterbook.get("1.0", "end").strip()
        if not content:
            messagebox.showwarning("Aviso", "O texto do Masterbook está vazio.")
            return

        def _run():
            self.log("Iniciando decomposição do Masterbook em microtarefas (Pydantic Schema)...")
            decomposer = MasterbookDecomposer(db_path=self.db_path)
            temp_mb = self.repo_root / "tasks" / "active" / f"mb_{int(time.time())}.md"
            temp_mb.parent.mkdir(parents=True, exist_ok=True)
            temp_mb.write_text(content, encoding="utf-8")

            try:
                tasks = decomposer.decompose_file(temp_mb)
                self.log(f"Sucesso! {len(tasks)} microtarefas geradas e salvas no banco de dados.")
                self.dispatch_to_ui(self.refresh_tasks_table)
            except Exception as exc:
                self.log(f"Erro na decomposição: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_run_autonomous_queue_clicked(self) -> None:
        """Dispara a execução de microtarefas da fila de forma autônoma."""
        def _run():
            self.log("Roteando e executando tarefas pendentes da fila...")
            router = PCIRouter(db_path=self.db_path, repo_root=self.repo_root)
            harness = MicroHarness(db_path=self.db_path, repo_root=self.repo_root)

            # Roteia tarefas pendentes
            routed_count = router.route_all_pending_tasks()
            self.log(f"Roteador determinístico despachou {routed_count} tarefas.")

            # Executa próximo lote local
            success, task_id, out = harness.run_next_local_task()
            if task_id:
                status_txt = "APROVADA no Pytest" if success else "REPROVADA"
                self.log(f"Execução de {task_id} finalizada -> {status_txt}")
                self.log(f"Detalhes do Sandbox:\n{out[:400]}")
            else:
                self.log("Nenhuma tarefa elegível para execução local no momento.")

            self.dispatch_to_ui(self.refresh_tasks_table)

        threading.Thread(target=_run, daemon=True).start()

    def refresh_tasks_table(self) -> None:
        """Recarrega a tabela de tarefas a partir do jobs.db."""
        for item in self.tree_tasks.get_children():
            self.tree_tasks.delete(item)

        try:
            tasks = self.db.list_tasks()
            for t in tasks:
                self.tree_tasks.insert(
                    "",
                    "end",
                    values=(
                        t["task_id"],
                        t["task_type"],
                        t.get("assigned_route") or "PENDENTE",
                        t["privacy_level"],
                        t["status"],
                        t.get("attempts", 0),
                    ),
                )
        except Exception as exc:
            self.log(f"Erro ao carregar tarefas do banco: {exc}")

    # =========================================================================
    # CONTROLADORES DA ABA 2: CUSTÓDIA FORENSE & SANITIZADOR OSINT
    # =========================================================================
    def _on_browse_forensic_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Selecionar Arquivo Bruto Forense",
            initialdir=str(self.repo_root),
        )
        if chosen:
            self.file_path_var.set(chosen)

    def _on_generate_sanitized_derivative(self) -> None:
        """Gera cópia derivada higienizada calculando hashes SHA-256 e redação."""
        raw_path = self.file_path_var.get().strip()
        if not raw_path or not Path(raw_path).exists():
            messagebox.showwarning("Aviso", "Selecione um arquivo existente para sanitizar.")
            return

        remove_pii = self.remove_pii_var.get()
        encrypt_spool = self.encrypt_spool_var.get()

        def _run():
            self.log(f"Processando custódia forense para: {Path(raw_path).name}...")
            sanitizer = ForensicSanitizer()
            output_dir = self.repo_root / "data" / "sanitized_derivatives"
            spool_dir = self.repo_root / "data" / "spool_encrypted"

            try:
                result = sanitizer.create_sanitized_derivative(
                    original_path=raw_path,
                    output_dir=output_dir,
                    strip_metadata_switch=remove_pii,
                    db_manager=self.db,
                )

                spool_file = "Não gerado"
                if encrypt_spool:
                    spool_res = sanitizer.encrypt_file_to_spool(
                        file_path=result["derivative_path"],
                        spool_dir=spool_dir,
                    )
                    spool_file = spool_res.get("encrypted_file", "OK")

                sample_text = ""
                deriv_path = Path(result["derivative_path"])
                if deriv_path.exists():
                    sample_text = deriv_path.read_text(encoding="utf-8", errors="ignore")[:800]

                output_text = (
                    "=========================================================================\n"
                    "         RELATÓRIO DE CUSTÓDIA FORENSE E HIGIENIZAÇÃO DE DADOS           \n"
                    "=========================================================================\n"
                    f"Arquivo Original:    {Path(raw_path).name}\n"
                    f"SHA-256 Original:    {result['original_sha256']}\n"
                    f"SHA-256 Derivada:    {result['derivative_sha256']}\n"
                    f"Remoção de PII:      {'SIM' if remove_pii else 'NÃO'}\n"
                    f"Categorias Redigidas: {result.get('stripped_categories', 'NONE')}\n"
                    f"Cópia Spool Cifrada: {spool_file}\n"
                    "-------------------------------------------------------------------------\n"
                    "AMOSTRA DO CONTEÚDO DERIVADO HIGIENIZADO:\n"
                    "-------------------------------------------------------------------------\n"
                    f"{sample_text}\n"
                )

                def update_box():
                    self.sanitizer_output_box.delete("1.0", "end")
                    self.sanitizer_output_box.insert("1.0", output_text)

                self.dispatch_to_ui(update_box)
                self.log(f"Derivada gerada com sucesso! SHA-256 Derivada: {result['derivative_sha256'][:16]}...")
            except Exception as exc:
                self.log(f"Falha na sanitização forense: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    # =========================================================================
    # CONTROLADORES DA ABA 3: DOADORES & BENCHMARK
    # =========================================================================
    def refresh_donors_table(self) -> None:
        """Atualiza a lista de nós doadores a partir do banco de dados."""
        for item in self.tree_donors.get_children():
            self.tree_donors.delete(item)

        try:
            nodes = self.db.list_nodes()
            for n in nodes:
                ram_vram = f"{n['ram_total_mb']} MB / {n.get('vram_total_mb', 0)} MB"
                self.tree_donors.insert(
                    "",
                    "end",
                    values=(
                        n["node_id"],
                        n["node_name"],
                        ram_vram,
                        n["status"],
                        f"{n.get('kudos_balance', 0.0):.2f} pts",
                        n.get("last_heartbeat", "Nunca"),
                    ),
                )
        except Exception as exc:
            self.log(f"Erro ao atualizar lista de doadores: {exc}")

    def refresh_matrix_table(self) -> None:
        """Atualiza a tabela da matriz de capacidades aferida."""
        for item in self.tree_matrix.get_children():
            self.tree_matrix.delete(item)

        try:
            with self.db.get_connection() as conn:
                rows = conn.execute("SELECT * FROM capability_matrix ORDER BY task_type ASC").fetchall()
                for r in rows:
                    rate_pct = f"{r['success_rate']*100:.1f}%"
                    self.tree_matrix.insert(
                        "",
                        "end",
                        values=(
                            r["node_id"],
                            r["model_name"],
                            r["task_type"],
                            r["sample_size"],
                            rate_pct,
                            f"{r['avg_tokens_per_sec']:.1f}",
                            r["verification_state"],
                        ),
                    )
        except Exception as exc:
            self.log(f"Erro ao carregar capability_matrix: {exc}")

    def _on_build_donor_exe_clicked(self) -> None:
        """Executa a compilação do executável do cliente doador via batch."""
        def _run():
            self.log("Iniciando compilação do executável PCI_Donor_Agent.exe via PyInstaller...")
            bat_path = self.repo_root / "donor_client" / "build_donor_exe.bat"
            if not bat_path.exists():
                self.log("ERRO: Script build_donor_exe.bat não encontrado.")
                return

            try:
                proc = subprocess.run(
                    [str(bat_path)],
                    cwd=str(self.repo_root / "donor_client"),
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if proc.returncode == 0:
                    self.log("SUCESSO: PCI_Donor_Agent.exe gerado na pasta donor_client/dist/!")
                    messagebox.showinfo(
                        "Compilação Concluída",
                        f"Instalador gerado com sucesso em:\n{self.repo_root / 'donor_client' / 'dist'}",
                    )
                else:
                    self.log(f"Erro no build: {proc.stderr[:400]}")
            except Exception as exc:
                self.log(f"Exceção durante build do instalador: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_run_benchmark_clicked(self) -> None:
        """Executa a bateria de benchmark contra as 4 categorias fundamentais."""
        def _run():
            self.log("Iniciando Bateria de Benchmark (EXTRACTION, CRUD, REFACTOR, PARSER_OSINT)...")
            runner = BenchmarkRunner(
                node_id="MASTER_NODE",
                model_name="qwen2.5-coder:3b",
                db_path=self.db_path,
                repo_root=self.repo_root,
            )

            try:
                report = runner.run_full_suite()
                rate_pct = report["overall_success_rate"] * 100
                self.log(
                    f"Bateria Concluída! Total: {report['total_passed']}/{report['total_tasks']} aprovadas. "
                    f"Taxa Global: {rate_pct:.1f}% em {report['total_time_sec']}s."
                )
                self.dispatch_to_ui(self.refresh_matrix_table)
            except Exception as exc:
                self.log(f"Erro durante benchmark: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    # =========================================================================
    # CONTROLADORES DA ABA 4: COLIBRÌ NOTURNO & DRIVE SYNC
    # =========================================================================
    def _on_run_nightly_now_clicked(self) -> None:
        """Executa o processamento em lote Colibrì imediatamente."""
        def _run():
            self.log_nightly("Disparando execução manual do pipeline noturno Colibrì...")
            worker = NightlyColibriWorker(db_path=self.db_path, repo_root=self.repo_root)

            try:
                res = worker.run_nightly_pipeline(max_tasks=5, check_user=False)
                self.log_nightly(
                    f"Lote noturno concluído: {res['success_count']}/{res['total_tasks']} tarefas aprovadas. "
                    f"Comentários escaneados: {res.get('scanned_comments_count', 0)}."
                )
                self.dispatch_to_ui(self.refresh_tasks_table)
            except Exception as exc:
                self.log_nightly(f"Erro no processamento noturno: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_register_windows_task_clicked(self) -> None:
        """Executa o script de agendamento no Windows Task Scheduler."""
        bat_path = self.repo_root / "config" / "register_nightly_task.bat"
        if not bat_path.exists():
            messagebox.showerror("Erro", "Script config/register_nightly_task.bat não encontrado.")
            return

        def _run():
            try:
                proc = subprocess.run([str(bat_path)], capture_output=True, text=True, timeout=20)
                self.log_nightly(proc.stdout)
                if proc.returncode == 0:
                    messagebox.showinfo("Agendamento", "Tarefa 'PCI_Sovereign_Nightly_Colibri' agendada para 03:00 AM!")
                else:
                    self.log_nightly(f"Aviso no agendamento: {proc.stderr}")
            except Exception as exc:
                self.log_nightly(f"Erro ao agendar no Windows: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_sync_drive_clicked(self) -> None:
        """Executa a sincronização do spool de dados com o Google Drive via Rclone."""
        def _run():
            self.log_nightly("Iniciando sincronização de pacotes criptografados com o Google Drive...")
            sync_mgr = DriveSyncManager(
                spool_dir=self.repo_root / "data" / "spool_encrypted",
                db_path=self.db_path,
            )

            try:
                res = sync_mgr.sync_spool(purge_local_on_success=True)
                if res["status"] == "COMPLETED":
                    self.log_nightly(
                        f"Sincronização bem-sucedida! {res['synced_count']} pacotes transferidos e validados "
                        f"({res.get('freed_mb', 0)} MB liberados no spool local)."
                    )
                else:
                    self.log_nightly(f"Resultado do Sync: {res.get('message', res['status'])}")
            except Exception as exc:
                self.log_nightly(f"Erro durante sincronização Rclone: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def run(self) -> None:
        """Inicia o loop principal de eventos da interface gráfica."""
        self.refresh_tasks_table()
        self.refresh_donors_table()
        self.refresh_matrix_table()
        self.root.mainloop()


# Alias de conveniência
PCIMasterApp = PCIMasterGUI


def main():
    app = PCIMasterGUI()
    app.run()


if __name__ == "__main__":
    main()
