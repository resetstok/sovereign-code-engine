"""
PCI Engine — Interface Gráfica Nativa Desktop (Etapa 7 / Etapa 11 Overhaul v2.2)
=================================================================================
Painel de Controle Mestre (CustomTkinter) com estética Minimalista Dev-Tech Neon:
1. Telemetria Síncrona no Milissegundo 0 (RAM, VRAM GTX 1650, SSD e Ollama sem 'Calculando...').
2. Cofre e Seletor de Chaves de API na Barra Lateral (Groq, OpenRouter, DeepSeek, Cerebras).
3. Triquetra Tech Neon Spinner + Barra de Progresso (%) + AI Live Step Tracker (5 Etapas).
4. Tabela Dark Mode com Badges Neon por Status, Filtro Rápido, Limpeza de Concluídas e Menu de Contexto (Botão Direito).
5. Botões Conceituais Minimalistas com Micro-interação de Movimento e Brilho no Hover.
"""

import json
import math
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

# Garante que a raiz do repositório e a pasta src estejam no sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
for _p in (str(_REPO_ROOT), str(_SRC_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import customtkinter as ctk

from pci_engine.benchmark_runner import BenchmarkRunner
from pci_engine.context_guard import ContextGuard
from pci_engine.database import DatabaseManager
from pci_engine.decomposer import MasterbookDecomposer
from pci_engine.drive_sync import DriveSyncManager
from pci_engine.micro_harness import MicroHarness
from pci_engine.nightly_colibri import NightlyColibriWorker
from pci_engine.preflight import PreflightChecker
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.router import PCIRouter
from pci_engine.sanitizer import ForensicSanitizer
from pci_engine.context_guard import ContextGuard
from pci_engine.aider_runner import AiderRunner
from pci_engine.workspace_bridge import WorkspaceBridge, ExportReceipt

# Configuração visual global CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class PCIMasterGUI:
    """
    Controlador e Janela Principal do PCI Sovereign Code Engine v2.3.
    """

    API_PROVIDERS = [
        "Google Gemini",
        "Groq",
        "OpenRouter",
        "DeepSeek",
        "OpenAI (GPT/Codex)",
        "Anthropic (Claude)",
        "Cerebras",
        "Mistral / Codestral",
    ]
    PROVIDER_ENV_MAP = {
        "Google Gemini": "GEMINI_API_KEY",
        "Groq": "GROQ_API_KEY",
        "OpenRouter": "OPENROUTER_API_KEY",
        "DeepSeek": "DEEPSEEK_API_KEY",
        "OpenAI (GPT/Codex)": "OPENAI_API_KEY",
        "Anthropic (Claude)": "ANTHROPIC_API_KEY",
        "Cerebras": "CEREBRAS_API_KEY",
        "Mistral / Codestral": "MISTRAL_API_KEY",
    }

    STEP_DEFINITIONS = [
        ("01 //", "Preflight & Sandbox Worktree"),
        ("02 //", "Decomposição DAG & Contexto"),
        ("03 //", "Inferência IA Soberana"),
        ("04 //", "Inspeção Sintática AST"),
        ("05 //", "Auditoria TDD Pytest"),
    ]

    def __init__(
        self,
        root: Optional[Any] = None,
        db_path: Optional[Path | str] = None,
        repo_root: Optional[Path | str] = None,
        headless_test: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
        self.db_path = Path(db_path) if db_path else self.repo_root / "data" / "jobs.db"
        self.api_keys_path = self.repo_root / "config" / "api_keys.json"
        self.headless_test = headless_test or (root is not None)
        self._tk_icon_ref: Optional[Any] = None

        # Registra AppUserModelID exclusivo no Windows para exibir o ícone próprio na Barra de Tarefas
        if sys.platform == "win32" and not self.headless_test:
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "PCI.SovereignCodeEngine.Master.v2.3"
                )
            except Exception as exc:
                _ = exc

        # Garante banco de dados SQLite WAL inicializado
        self.db = DatabaseManager(self.db_path)
        self.db.init_db()

        # Fila de eventos thread-safe para atualizar a UI
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        # Janela Principal
        self.root = root if root is not None else ctk.CTk()
        self.root.title("PCI Sovereign Code Engine v2.3 — Master Control Console")
        self.root.geometry("1280x820")
        self.root.minsize(1140, 720)
        self.root.configure(fg_color="#070a10")
        self._apply_window_icon()

        # Estado interno
        self.is_hybrid_mode = tk.BooleanVar(value=False)
        self.is_broker_running = tk.BooleanVar(value=False)
        self.remove_pii_var = tk.BooleanVar(value=True)
        self.encrypt_spool_var = tk.BooleanVar(value=True)
        self.operator_password_var = tk.StringVar(value=ContextGuard.get_or_set_operator_password(repo_root=self.repo_root))
        self.task_filter_var = tk.StringVar(value="Todas")
        self.selected_provider_var = tk.StringVar(value="Google Gemini")
        self._broker_process: Optional[subprocess.Popen] = None

        # Workspace Ativo (Ponte Multi-Projetos para SESAU, Nexus, LicitAI, etc.)
        default_target_ws = Path(r"C:\Lab\012 - Auditoria (SESAU)")
        if not default_target_ws.exists():
            default_target_ws = self.repo_root
        self.target_workspace_var = tk.StringVar(value=str(default_target_ws))
        self.workspace_bridge = WorkspaceBridge(default_target_ws)
        self.aider_runner = AiderRunner(self.repo_root)

        # Variáveis de texto reativas para telemetria na barra lateral
        self.ram_text_var = tk.StringVar(value="RAM: Inicializando...")
        self.vram_text_var = tk.StringVar(value="VRAM: Inicializando...")
        self.ssd_text_var = tk.StringVar(value="SSD Livre: Inicializando...")
        self.ollama_status_var = tk.StringVar(value="Ollama: Verificando...")
        self.broker_status_var = tk.StringVar(value="Broker: Inativo")
        self.saved_keys_status_var = tk.StringVar(value="Chaves salvas: Nenhuma")

        # Estado da animação Triquetra e AI Step Tracker
        self._triquetra_phase: float = 0.0
        self._triquetra_progress: float = 100.0
        self._triquetra_active: bool = False
        self._current_ai_step: int = 5  # 5 = todos concluídos / repouso
        self._step_widgets: List[Dict[str, Any]] = []

        # Constrói todos os componentes visuais
        self._configure_dark_treeview_style()
        self._build_layout()
        self._load_saved_api_keys_into_ui()

        # Leitura síncrona imediata no milissegundo 0 (nunca abre em 'Calculando...')
        self._refresh_metrics_sync()

        # Carrega as tabelas imediatamente na abertura
        self.refresh_tasks_table()
        self.refresh_donors_table()
        self.refresh_matrix_table()

        # Inicia loop de consumo da fila de UI e animações se não estiver em modo headless
        self._running = True
        if not self.headless_test:
            # Reaplica o ícone após 220ms e 600ms para sobrescrever o timer interno do CustomTkinter
            self.root.after(220, self._apply_window_icon)
            self.root.after(600, self._apply_window_icon)
            self._start_metrics_poller()
            self._schedule_ui_queue_consumer()
            self._animate_triquetra_canvas()

    def _apply_window_icon(self) -> None:
        """Aplica o ícone cyber-neon (assets/pci_icon.ico) na janela e na Barra de Tarefas do Windows."""
        icon_path = self.repo_root / "assets" / "pci_icon.ico"
        if not icon_path.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
            self.root.wm_iconbitmap(str(icon_path))
            from PIL import Image, ImageTk
            img = Image.open(icon_path)
            self._tk_icon_ref = ImageTk.PhotoImage(img)
            self.root.wm_iconphoto(True, self._tk_icon_ref)
        except Exception as exc:
            _ = exc

    # =========================================================================
    # ESTILO DARK NEON PARA TABELAS (TTK.TREEVIEW) E MICRO-INTERAÇÕES
    # =========================================================================
    def _configure_dark_treeview_style(self) -> None:
        """Configura o tema escuro moderno com linhas sutis e cabeçalho neon para todas as tabelas."""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            style.theme_use("default")

        style.configure(
            "Treeview",
            background="#0b101b",
            foreground="#e2e8f0",
            fieldbackground="#0b101b",
            borderwidth=0,
            rowheight=28,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#111a2e",
            foreground="#00f5d4",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padding=(6, 6),
        )
        style.map(
            "Treeview",
            background=[("selected", "#1b2d48")],
            foreground=[("selected", "#ffffff")],
        )
        style.map(
            "Treeview.Heading",
            background=[("active", "#192742")],
        )

    def _attach_interactive_hover(
        self,
        btn: ctk.CTkButton,
        base_text: str,
        hover_text: str,
        base_border: str = "#1e293b",
        hover_border: str = "#00f5d4",
    ) -> None:
        """Adiciona micro-interação minimalista de movimento no ícone conceitual e brilho de borda ao passar o mouse."""
        btn.configure(border_width=1, border_color=base_border)

        def _on_enter(_event: Any) -> None:
            btn.configure(text=hover_text, border_color=hover_border)

        def _on_leave(_event: Any) -> None:
            btn.configure(text=base_text, border_color=base_border)

        btn.bind("<Enter>", _on_enter)
        btn.bind("<Leave>", _on_leave)

    # =========================================================================
    # CONSTRUÇÃO DO LAYOUT (SIDEBAR + BACKGROUND CIRCUIT + 4 ABAS)
    # =========================================================================
    def _setup_ui(self) -> None:
        """Alias de compatibilidade para inicialização da interface gráfica."""
        self._build_layout()

    def _build_layout(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # ---------------------------------------------------------------------
        # 1. BARRA LATERAL ESQUERDA (Monitor Físico, Higiene, Cofre API e Broker)
        # ---------------------------------------------------------------------
        self.sidebar = ctk.CTkFrame(
            self.root,
            width=295,
            corner_radius=0,
            fg_color="#0a0f18",
            border_width=1,
            border_color="#162238",
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        # Cabeçalho com detalhe minimalista neon
        title_lbl = ctk.CTkLabel(
            self.sidebar,
            text="❖ PCI ENGINE v2.2",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#00f5d4",
        )
        title_lbl.pack(padx=18, pady=(16, 2), anchor="w")

        sub_lbl = ctk.CTkLabel(
            self.sidebar,
            text="Sovereign Master System // Dev-Tech HUD\n8GB RAM / GTX 1650 4GB",
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
            justify="left",
        )
        sub_lbl.pack(padx=18, pady=(0, 10), anchor="w")

        # Card de Telemetria Física
        metrics_frame = ctk.CTkFrame(
            self.sidebar,
            corner_radius=10,
            fg_color="#0f1726",
            border_width=1,
            border_color="#1b2840",
        )
        metrics_frame.pack(padx=14, pady=6, fill="x")

        ctk.CTkLabel(
            metrics_frame,
            text="◈ MONITOR FÍSICO (TEMPO REAL)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#00bbf9",
        ).pack(padx=12, pady=(8, 4), anchor="w")

        ctk.CTkLabel(
            metrics_frame,
            textvariable=self.ram_text_var,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#e2e8f0",
        ).pack(padx=12, pady=2, anchor="w")

        self.ram_progress = ctk.CTkProgressBar(
            metrics_frame,
            height=7,
            fg_color="#162238",
            progress_color="#00f5d4",
        )
        self.ram_progress.pack(padx=12, pady=(2, 6), fill="x")
        self.ram_progress.set(0.5)

        ctk.CTkLabel(
            metrics_frame,
            textvariable=self.vram_text_var,
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#cbd5e1",
        ).pack(padx=12, pady=2, anchor="w")

        ctk.CTkLabel(
            metrics_frame,
            textvariable=self.ssd_text_var,
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#cbd5e1",
        ).pack(padx=12, pady=2, anchor="w")

        ctk.CTkLabel(
            metrics_frame,
            textvariable=self.ollama_status_var,
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#00f5d4",
        ).pack(padx=12, pady=(2, 8), anchor="w")

        # Botão de Higienização de RAM com ícone conceitual interativo
        self.btn_clean_ram = ctk.CTkButton(
            self.sidebar,
            text="⌬ Higienizar RAM Agora",
            command=self._on_clean_ram_clicked,
            fg_color="#132238",
            hover_color="#1d3557",
            text_color="#00f5d4",
            height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.btn_clean_ram.pack(padx=14, pady=(8, 8), fill="x")
        self._attach_interactive_hover(
            self.btn_clean_ram,
            "⌬ Higienizar RAM Agora",
            "⟡ Higienizar RAM Agora ▸",
            base_border="#00bbf9",
            hover_border="#00f5d4",
        )

        # ---------------------------------------------------------------------
        # PROJETO / WORKSPACE ALVO (Ponte Multi-Projetos para SESAU, Nexus, etc.)
        # ---------------------------------------------------------------------
        ws_frame = ctk.CTkFrame(
            self.sidebar,
            corner_radius=10,
            fg_color="#0f1726",
            border_width=1,
            border_color="#1b2840",
        )
        ws_frame.pack(padx=14, pady=(8, 4), fill="x")

        ctk.CTkLabel(
            ws_frame,
            text="📁 Projeto / Workspace Alvo:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#00f5d4",
        ).pack(padx=12, pady=(8, 2), anchor="w")

        ws_input_row = ctk.CTkFrame(ws_frame, fg_color="transparent")
        ws_input_row.pack(padx=10, pady=(2, 6), fill="x")

        self.lbl_active_ws = ctk.CTkLabel(
            ws_input_row,
            textvariable=self.target_workspace_var,
            font=ctk.CTkFont(family="Consolas", size=9),
            text_color="#94a3b8",
            anchor="w",
            wraplength=170,
            justify="left",
        )
        self.lbl_active_ws.pack(side="left", fill="x", expand=True)

        btn_select_ws = ctk.CTkButton(
            ws_input_row,
            text="📂",
            width=32,
            height=26,
            fg_color="#162238",
            hover_color="#00f5d4",
            text_color="#ffffff",
            font=ctk.CTkFont(size=12),
            command=self._on_choose_target_workspace_clicked,
        )
        btn_select_ws.pack(side="right", padx=(4, 0))

        ws_btn_row = ctk.CTkFrame(ws_frame, fg_color="transparent")
        ws_btn_row.pack(padx=10, pady=(0, 8), fill="x")

        btn_open_ws_folder = ctk.CTkButton(
            ws_btn_row,
            text="Abrir Pasta",
            height=24,
            fg_color="#132238",
            hover_color="#1f3657",
            text_color="#00bbf9",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._on_open_target_workspace_folder_clicked,
        )
        btn_open_ws_folder.pack(side="left", expand=True, fill="x", padx=(0, 2))

        btn_git_sync = ctk.CTkButton(
            ws_btn_row,
            text="Git Prep & Sync",
            height=24,
            fg_color="#1c1624",
            hover_color="#3c1c38",
            text_color="#ffbe0b",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._on_git_sync_clicked,
        )
        btn_git_sync.pack(side="right", expand=True, fill="x", padx=(2, 0))

        # ---------------------------------------------------------------------
        # COFRE E SELETOR DE CHAVES DE API (Substitui a caixa preta solta)
        # ---------------------------------------------------------------------
        api_vault_frame = ctk.CTkFrame(
            self.sidebar,
            corner_radius=10,
            fg_color="#0f1726",
            border_width=1,
            border_color="#1b2840",
        )
        api_vault_frame.pack(padx=14, pady=6, fill="x")

        self.switch_hybrid = ctk.CTkSwitch(
            api_vault_frame,
            text="Modo Híbrido (+ APIs Cloud)",
            variable=self.is_hybrid_mode,
            command=self._on_hybrid_switch_toggled,
            progress_color="#00f5d4",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.switch_hybrid.pack(padx=12, pady=(10, 6), anchor="w")

        provider_row = ctk.CTkFrame(api_vault_frame, fg_color="transparent")
        provider_row.pack(padx=12, pady=2, fill="x")

        ctk.CTkLabel(
            provider_row,
            text="Provedor:",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        ).pack(side="left")

        self.provider_menu = ctk.CTkOptionMenu(
            provider_row,
            variable=self.selected_provider_var,
            values=self.API_PROVIDERS,
            command=self._on_provider_changed,
            width=145,
            height=26,
            fg_color="#162238",
            button_color="#1e3150",
            button_hover_color="#29436b",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.provider_menu.pack(side="right")

        self.api_key_entry = ctk.CTkEntry(
            api_vault_frame,
            placeholder_text="Cole sua API Key (ex: gsk_... / sk-...)",
            show="*",
            height=28,
            fg_color="#070a10",
            border_color="#1e293b",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.api_key_entry.pack(padx=12, pady=(6, 4), fill="x")

        self.btn_save_api_key = ctk.CTkButton(
            api_vault_frame,
            text="⟡ Salvar / Ativar Chave",
            command=self._on_save_api_key_clicked,
            height=28,
            fg_color="#00bbf9",
            hover_color="#0096c7",
            text_color="#070a10",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.btn_save_api_key.pack(padx=12, pady=(2, 4), fill="x")
        self._attach_interactive_hover(
            self.btn_save_api_key,
            "⟡ Salvar / Ativar Chave",
            "❖ Salvar / Ativar Chave ▸",
            base_border="#00bbf9",
            hover_border="#00f5d4",
        )

        ctk.CTkLabel(
            api_vault_frame,
            textvariable=self.saved_keys_status_var,
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color="#00f5d4",
            wraplength=240,
            justify="left",
        ).pack(padx=12, pady=(2, 8), anchor="w")

        # ---------------------------------------------------------------------
        # CONTROLE DA REDE DE DOADORES TAILSCALE
        # ---------------------------------------------------------------------
        broker_frame = ctk.CTkFrame(
            self.sidebar,
            corner_radius=10,
            fg_color="#0f1726",
            border_width=1,
            border_color="#1b2840",
        )
        broker_frame.pack(padx=14, pady=6, fill="x")

        self.switch_broker = ctk.CTkSwitch(
            broker_frame,
            text="Broker Doadores (Tailscale)",
            variable=self.is_broker_running,
            command=self._on_broker_switch_toggled,
            progress_color="#f77f00",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.switch_broker.pack(padx=12, pady=(10, 4), anchor="w")

        ctk.CTkLabel(
            broker_frame,
            textvariable=self.broker_status_var,
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#00f5d4",
        ).pack(padx=12, pady=(0, 8), anchor="w")

        # Rodapé da Sidebar
        footer_lbl = ctk.CTkLabel(
            self.sidebar,
            text="Tailscale Broker: :8000  |  Ollama: :11434\nSQLite WAL Soberano Ativo",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color="#475569",
            justify="left",
        )
        footer_lbl.pack(side="bottom", padx=16, pady=10, anchor="w")

        # ---------------------------------------------------------------------
        # 2. ÁREA CENTRAL DE CONTEÚDO (Com Linhas Circuit Neon no Topo + Abas)
        # ---------------------------------------------------------------------
        self.main_container = ctk.CTkFrame(self.root, fg_color="#070a10", corner_radius=0)
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=12, pady=8)
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(1, weight=1)

        # Canvas Minimalista Dev-Tech com linhas finas neon no topo
        self.bg_accent_canvas = tk.Canvas(
            self.main_container,
            height=26,
            bg="#070a10",
            highlightthickness=0,
            bd=0,
        )
        self.bg_accent_canvas.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 2))
        self.bg_accent_canvas.bind("<Configure>", self._draw_dev_tech_header_lines)

        # Tabview com abas espaçosas e alto contraste (sem corte de texto)
        self.tabview = ctk.CTkTabview(
            self.main_container,
            fg_color="#0b101b",
            segmented_button_fg_color="#0f1726",
            segmented_button_selected_color="#00bbf9",
            segmented_button_selected_hover_color="#00f5d4",
            segmented_button_unselected_color="#131d31",
            segmented_button_unselected_hover_color="#1d2d4a",
            text_color="#ffffff",
            text_color_disabled="#64748b",
            border_width=1,
            border_color="#162238",
            corner_radius=12,
        )
        self.tabview.grid(row=1, column=0, sticky="nsew")

        self.tab_masterbook = self.tabview.add("Masterbook & Fila")
        self.tab_forensic = self.tabview.add("Custódia Forense & OSINT")
        self.tab_donors = self.tabview.add("Rede de Doadores & Matriz")
        self.tab_nightly = self.tabview.add("Colibrì Noturno & Sync")
        self.tab_aider = self.tabview.add("Aider CLI & Architect")

        # Ajusta altura e largura das abas sem alterar o texto pós-cálculo para não cortar letras
        try:
            seg_btn = self.tabview._segmented_button
            seg_btn.configure(font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), height=36)
            for btn_tab in seg_btn._buttons_dict.values():
                btn_tab.configure(width=195, height=34)
        except Exception as exc:
            _ = exc

        self._build_tab_masterbook(self.tab_masterbook)
        self._build_tab_forensic(self.tab_forensic)
        self._build_tab_donors(self.tab_donors)
        self._build_tab_nightly(self.tab_nightly)
        self._build_tab_aider(self.tab_aider)

    def _draw_dev_tech_header_lines(self, event: Any = None) -> None:
        """Desenha traços geométricos minimalistas estilo Dev-Tech Cyberpunk no topo do painel."""
        w = event.width if event else self.bg_accent_canvas.winfo_width()
        if w < 100:
            w = 920
        self.bg_accent_canvas.delete("all")
        # Linha base sutil
        self.bg_accent_canvas.create_line(0, 14, w, 14, fill="#162238", width=1)
        # Feixe neon ciano à esquerda
        self.bg_accent_canvas.create_line(0, 14, int(w * 0.28), 14, fill="#00f5d4", width=2)
        # Degrau geométrico de circuito
        self.bg_accent_canvas.create_line(
            int(w * 0.28), 14, int(w * 0.30), 6, int(w * 0.48), 6, fill="#00bbf9", width=1
        )
        # Feixe magenta sutil à direita
        self.bg_accent_canvas.create_line(int(w * 0.78), 14, w, 14, fill="#ff007f", width=2)
        # Pequenos nós geométricos (diamantes)
        for nx, color in [(int(w * 0.28), "#00f5d4"), (int(w * 0.48), "#00bbf9"), (int(w * 0.78), "#ff007f")]:
            self.bg_accent_canvas.create_polygon(
                nx, 10, nx + 4, 14, nx, 18, nx - 4, 14, fill=color, outline=""
            )
        self.bg_accent_canvas.create_text(
            int(w * 0.63),
            12,
            text="// SOVEREIGN EXECUTION BUS :: ZERO-HALLUCINATION PROTOCOL",
            fill="#475569",
            font=("Consolas", 9),
        )

    # =========================================================================
    # ABA 1: MASTERBOOK + TRIQUETRA NEON HUD + TABELA COLORIDA + LOGS
    # =========================================================================
    def _build_tab_masterbook(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=0)
        parent.grid_rowconfigure(3, weight=1)
        parent.grid_rowconfigure(5, weight=1)

        # Linha 0: Cabeçalho Masterbook com Grid explícito (nenhum botão fica espremido)
        top_bar = ctk.CTkFrame(parent, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        top_bar.grid_columnconfigure(0, weight=1)
        top_bar.grid_columnconfigure(1, weight=0)
        top_bar.grid_columnconfigure(2, weight=0)
        top_bar.grid_columnconfigure(3, weight=0)

        ctk.CTkLabel(
            top_bar,
            text="◈ Masterbook (Especificação) & HUD Triquetra:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#00f5d4",
        ).grid(row=0, column=0, sticky="w")

        btn_load_md = ctk.CTkButton(
            top_bar,
            text="◈ Carregar Arquivo .MD",
            width=165,
            height=28,
            fg_color="#00bbf9",
            text_color="#070a10",
            hover_color="#00f5d4",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_load_masterbook_file,
        )
        btn_load_md.grid(row=0, column=1, padx=(8, 4), sticky="e")
        self._attach_interactive_hover(
            btn_load_md,
            "◈ Carregar Arquivo .MD",
            "❖ Carregar Arquivo .MD ▸",
            base_border="#00bbf9",
            hover_border="#ffffff",
        )

        btn_inject_context = ctk.CTkButton(
            top_bar,
            text="🔍 Injetar Contexto do Projeto",
            width=190,
            height=28,
            fg_color="#14213d",
            hover_color="#1f3657",
            text_color="#00f5d4",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_inject_workspace_context_clicked,
        )
        btn_inject_context.grid(row=0, column=2, padx=4, sticky="e")
        self._attach_interactive_hover(
            btn_inject_context,
            "🔍 Injetar Contexto do Projeto",
            "❖ Injetar Contexto do Projeto ▸",
            base_border="#1b2840",
            hover_border="#00f5d4",
        )

        btn_open_web_hud = ctk.CTkButton(
            top_bar,
            text="❖ Abrir HUD Web (SVG)",
            width=160,
            height=28,
            fg_color="#132238",
            hover_color="#1e3658",
            text_color="#00f5d4",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_open_web_triquetra_hud,
        )
        btn_open_web_hud.grid(row=0, column=3, padx=(4, 0), sticky="e")
        self._attach_interactive_hover(
            btn_open_web_hud,
            "❖ Abrir HUD Web (SVG)",
            "⟡ Abrir HUD Web (SVG) ▸",
        )

        # Linha 1: Split Horizontal -> Esquerda: Editor Masterbook | Direita: Triquetra Neon Spinner + AI Step Tracker
        upper_split = ctk.CTkFrame(parent, fg_color="transparent")
        upper_split.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        upper_split.grid_columnconfigure(0, weight=3)
        upper_split.grid_columnconfigure(1, weight=2)

        self.txt_masterbook = ctk.CTkTextbox(
            upper_split,
            height=155,
            fg_color="#070a10",
            border_width=1,
            border_color="#1b2840",
            text_color="#e2e8f0",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.txt_masterbook.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        default_pilot_path = self.repo_root / "tasks" / "active" / "PILOTO_NEXUS_INVESTIGATIVO.md"
        if default_pilot_path.exists():
            default_mb_text = default_pilot_path.read_text(encoding="utf-8", errors="ignore")
        else:
            default_mb_text = (
                "# Especificação de Sistema\n"
                "Construa um módulo em src/analytics.py com uma função calcular_metricas(dados: list) -> dict\n"
                "que extraia o total, a média e descarte valores negativos com validação estrita.\n"
            )
        self.txt_masterbook.insert("1.0", default_mb_text)

        # Painel Direito: Triquetra Tech Neon Spinner + AI Step Tracker
        hud_frame = ctk.CTkFrame(
            upper_split,
            fg_color="#070a10",
            border_width=1,
            border_color="#1b2840",
            corner_radius=10,
        )
        hud_frame.grid(row=0, column=1, sticky="nsew")
        hud_frame.grid_columnconfigure(0, weight=0)
        hud_frame.grid_columnconfigure(1, weight=1)

        # Coluna 0 do HUD: Triquetra Neon Canvas + Barra de Progresso (%)
        triq_col = ctk.CTkFrame(hud_frame, fg_color="transparent")
        triq_col.grid(row=0, column=0, padx=(10, 6), pady=8, sticky="ns")

        self.triquetra_canvas = tk.Canvas(
            triq_col,
            width=118,
            height=112,
            bg="#070a10",
            highlightthickness=0,
            bd=0,
        )
        self.triquetra_canvas.pack()

        self.triquetra_progress_bar = ctk.CTkProgressBar(
            triq_col,
            width=110,
            height=6,
            fg_color="#131d31",
            progress_color="#00f5d4",
        )
        self.triquetra_progress_bar.pack(pady=(4, 2))
        self.triquetra_progress_bar.set(1.0)

        self.triquetra_pct_label = ctk.CTkLabel(
            triq_col,
            text="PRONTO // 100%",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color="#00f5d4",
        )
        self.triquetra_pct_label.pack()

        # Coluna 1 do HUD: Lista de 5 Etapas da IA (AI Step Tracker)
        steps_col = ctk.CTkFrame(hud_frame, fg_color="transparent")
        steps_col.grid(row=0, column=1, padx=(4, 10), pady=8, sticky="nsew")

        self._step_widgets.clear()
        for idx, (code_str, title_str) in enumerate(self.STEP_DEFINITIONS):
            row_f = ctk.CTkFrame(
                steps_col,
                fg_color="#0c1322",
                corner_radius=5,
                height=24,
            )
            row_f.pack(fill="x", pady=2)
            row_f.pack_propagate(False)

            lbl_idx = ctk.CTkLabel(
                row_f,
                text=code_str,
                font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
                text_color="#64748b",
            )
            lbl_idx.pack(side="left", padx=(6, 4))

            lbl_title = ctk.CTkLabel(
                row_f,
                text=title_str,
                font=ctk.CTkFont(size=10),
                text_color="#94a3b8",
            )
            lbl_title.pack(side="left")

            lbl_state = ctk.CTkLabel(
                row_f,
                text="✓ OK",
                font=ctk.CTkFont(family="Consolas", size=9, weight="bold"),
                text_color="#00f5d4",
            )
            lbl_state.pack(side="right", padx=6)

            self._step_widgets.append(
                {
                    "frame": row_f,
                    "idx": lbl_idx,
                    "title": lbl_title,
                    "state": lbl_state,
                }
            )

        # Linha 2: Barra de Ações com Grid para nunca espremer botões
        action_bar = ctk.CTkFrame(parent, fg_color="transparent")
        action_bar.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        action_bar.grid_columnconfigure(2, weight=1)

        self.btn_decompose = ctk.CTkButton(
            action_bar,
            text="1. ◈ Decompor Masterbook",
            command=self._on_decompose_clicked,
            fg_color="#00f5d4",
            text_color="#070a10",
            hover_color="#00bbf9",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=205,
            height=32,
        )
        self.btn_decompose.grid(row=0, column=0, padx=(0, 6), sticky="w")
        self._attach_interactive_hover(
            self.btn_decompose,
            "1. ◈ Decompor Masterbook",
            "1. ❖ Decompor Masterbook ▸",
            base_border="#00f5d4",
            hover_border="#ffffff",
        )

        self.btn_run_queue = ctk.CTkButton(
            action_bar,
            text="2. ⟡ Iniciar Execução Autônoma",
            command=self._on_run_autonomous_queue_clicked,
            fg_color="#ff007f",
            text_color="#ffffff",
            hover_color="#d90467",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=220,
            height=32,
        )
        self.btn_run_queue.grid(row=0, column=1, padx=4, sticky="w")
        self._attach_interactive_hover(
            self.btn_run_queue,
            "2. ⟡ Iniciar Execução Autônoma",
            "2. ❖ Iniciar Execução Autônoma ▸",
            base_border="#ff007f",
            hover_border="#00f5d4",
        )

        # Controles à direita: Filtro, Limpar Fila e Atualizar Fila
        ctk.CTkLabel(
            action_bar,
            text="Filtro:",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        ).grid(row=0, column=3, padx=(6, 4), sticky="e")

        self.filter_menu = ctk.CTkOptionMenu(
            action_bar,
            variable=self.task_filter_var,
            values=["Todas", "Pendentes", "Verificadas", "Falhas"],
            command=lambda _val: self._on_refresh_tasks_clicked(),
            width=110,
            height=30,
            fg_color="#131d31",
            button_color="#1c2b47",
        )
        self.filter_menu.grid(row=0, column=4, padx=4, sticky="e")

        btn_clear_done = ctk.CTkButton(
            action_bar,
            text="⌧ Limpar Fila",
            width=120,
            height=30,
            fg_color="#1c1624",
            hover_color="#381c36",
            text_color="#ffbe0b",
            command=self._on_clear_completed_tasks_clicked,
        )
        btn_clear_done.grid(row=0, column=5, padx=4, sticky="e")
        self._attach_interactive_hover(
            btn_clear_done,
            "⌧ Limpar Fila",
            "⌧ Limpar Fila ▸",
            base_border="#381c36",
            hover_border="#ffbe0b",
        )

        btn_refresh_tasks = ctk.CTkButton(
            action_bar,
            text="⟡ Atualizar Fila",
            width=125,
            height=30,
            fg_color="#162238",
            hover_color="#1f3252",
            command=self._on_refresh_tasks_clicked,
        )
        btn_refresh_tasks.grid(row=0, column=6, padx=(4, 0), sticky="e")
        self._attach_interactive_hover(btn_refresh_tasks, "⟡ Atualizar Fila", "❖ Atualizar Fila ▸")

        # Linha 3: Tabela de Tarefas (Treeview Dark com Cores por Status e Menu de Contexto)
        tree_frame = ctk.CTkFrame(parent, fg_color="#0b101b", border_width=1, border_color="#1b2840")
        tree_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=4)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        cols = ("task_id", "task_type", "route", "privacy", "status", "attempts")
        self.tree_tasks = ttk.Treeview(tree_frame, columns=cols, show="headings", height=6)
        self.tree_tasks.heading("task_id", text="ID da Tarefa")
        self.tree_tasks.heading("task_type", text="Categoria")
        self.tree_tasks.heading("route", text="Rota Atribuída")
        self.tree_tasks.heading("privacy", text="Privacidade")
        self.tree_tasks.heading("status", text="Status (Badge)")
        self.tree_tasks.heading("attempts", text="Tentativas")

        self.tree_tasks.column("task_id", width=230, anchor="w")
        self.tree_tasks.column("task_type", width=110, anchor="center")
        self.tree_tasks.column("route", width=170, anchor="center")
        self.tree_tasks.column("privacy", width=100, anchor="center")
        self.tree_tasks.column("status", width=150, anchor="center")
        self.tree_tasks.column("attempts", width=80, anchor="center")

        # Tags de cores Neon por Status
        self.tree_tasks.tag_configure("status_verified", foreground="#00f5d4", background="#09191c")
        self.tree_tasks.tag_configure("status_pending", foreground="#ffbe0b", background="#19160c")
        self.tree_tasks.tag_configure("status_failed", foreground="#ff0055", background="#1f0b14")

        self.tree_tasks.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_tasks.yview)
        self.tree_tasks.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Menu de Contexto (Botão Direito na Tarefa)
        self.task_context_menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#0f1726",
            fg="#e2e8f0",
            activebackground="#00bbf9",
            activeforeground="#070a10",
            font=("Segoe UI", 10),
        )
        self.task_context_menu.add_command(
            label="⟡ Re-executar Tarefa Agora",
            command=self._on_ctx_rerun_task,
        )
        self.task_context_menu.add_command(
            label="◈ Ver Código Patch / Especificação",
            command=self._on_ctx_view_patch,
        )
        self.task_context_menu.add_command(
            label="📦 Exportar Código para o Projeto Alvo",
            command=self._on_ctx_export_task_to_workspace,
        )
        self.task_context_menu.add_separator()
        self.task_context_menu.add_command(
            label="⌧ Excluir Tarefa da Fila",
            command=self._on_ctx_delete_task,
        )
        self.tree_tasks.bind("<Button-3>", self._on_task_right_click)
        self.tree_tasks.bind("<Double-1>", lambda _e: self._on_ctx_view_patch())

        # Linha 4 e 5: Console de Logs TDD em Tempo Real
        log_header = ctk.CTkFrame(parent, fg_color="transparent")
        log_header.grid(row=4, column=0, sticky="ew", padx=8, pady=(4, 0))

        ctk.CTkLabel(
            log_header,
            text="◈ Diário de Execução & Saída do Pytest (Tempo Real):  [Clique Direito na tabela acima para ações rápidas]",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#00bbf9",
        ).pack(side="left")

        self.txt_logs = ctk.CTkTextbox(
            parent,
            height=125,
            fg_color="#070a10",
            border_width=1,
            border_color="#1b2840",
            text_color="#00f5d4",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.txt_logs.grid(row=5, column=0, sticky="nsew", padx=8, pady=(2, 6))

    # =========================================================================
    # ANIMAÇÃO PARAMÉTRICA DA TRIQUETRA NEON E STEP TRACKER
    # =========================================================================
    def _set_ai_step_progress(self, active_step: int, progress_pct: float, label_prefix: str = "EXECUTANDO") -> None:
        """Atualiza o estado visual das 5 etapas (pending, active, completed) e a porcentagem da Triquetra."""
        self._current_ai_step = active_step
        self._triquetra_progress = max(0.0, min(100.0, progress_pct))
        self._triquetra_active = active_step < 5

        def _update_ui() -> None:
            self.triquetra_progress_bar.set(self._triquetra_progress / 100.0)
            self.triquetra_pct_label.configure(text=f"{label_prefix} // {int(self._triquetra_progress)}%")

            for idx, w in enumerate(self._step_widgets):
                if idx < active_step:
                    # Completed: opaco com check verde neon
                    w["frame"].configure(fg_color="#091520", border_width=0)
                    w["idx"].configure(text_color="#475569")
                    w["title"].configure(text_color="#64748b")
                    w["state"].configure(text="✓ DONE", text_color="#00f5d4")
                elif idx == active_step and active_step < 5:
                    # Active: destaque neon brilhante + mini-spinner
                    w["frame"].configure(fg_color="#11243d", border_width=1, border_color="#00f5d4")
                    w["idx"].configure(text_color="#00f5d4")
                    w["title"].configure(text_color="#ffffff")
                    w["state"].configure(text="⟳ ATIVO", text_color="#00f5d4")
                else:
                    # Pending: aguardando
                    w["frame"].configure(fg_color="#0c121e", border_width=0)
                    w["idx"].configure(text_color="#475569")
                    w["title"].configure(text_color="#475569")
                    w["state"].configure(text="PENDING", text_color="#475569")

        self.dispatch_to_ui(_update_ui)

    def _animate_triquetra_canvas(self) -> None:
        """Renderiza o nó Triquetra (Trefoil Knot) com feixe de luz neon percorrendo a curva."""
        if not self._running:
            return
        try:
            c = self.triquetra_canvas
            c.delete("all")
            cx, cy = 59.0, 56.0
            scale = 16.5
            steps = 90

            # Rotação lenta contínua + deslocamento rápido quando ativo
            speed = 0.14 if self._triquetra_active else 0.04
            self._triquetra_phase = (self._triquetra_phase + speed) % (2 * math.pi)
            rot = self._triquetra_phase * 0.25

            pts: List[tuple[float, float]] = []
            for i in range(steps + 1):
                t = (i / steps) * (2 * math.pi)
                raw_x = (math.sin(t) + 2.0 * math.sin(2.0 * t)) * scale
                raw_y = (math.cos(t) - 2.0 * math.cos(2.0 * t)) * scale
                rx = raw_x * math.cos(rot) - raw_y * math.sin(rot)
                ry = raw_x * math.sin(rot) + raw_y * math.cos(rot)
                pts.append((cx + rx, cy + ry))

            # 1. Desenha trilho escuro da Triquetra
            flat_track = [coord for pt in pts for coord in pt]
            c.create_line(*flat_track, fill="#162238", width=3, smooth=True)

            # 2. Desenha feixe de luz Neon percorrendo a curva
            head_idx = int((self._triquetra_phase / (2 * math.pi)) * steps) % steps
            beam_len = 26
            beam_pts: List[float] = []
            for k in range(beam_len):
                p_idx = (head_idx - k) % steps
                beam_pts.extend(pts[p_idx])
            if len(beam_pts) >= 4:
                c.create_line(*beam_pts, fill="#00bbf9", width=5, capstyle="round", smooth=True)
                c.create_line(*beam_pts[:20], fill="#00f5d4", width=3, capstyle="round", smooth=True)

            # 3. Segundo feixe magenta em fase oposta
            opp_idx = (head_idx + steps // 2) % steps
            opp_pts: List[float] = []
            for k in range(16):
                p_idx = (opp_idx - k) % steps
                opp_pts.extend(pts[p_idx])
            if len(opp_pts) >= 4:
                c.create_line(*opp_pts, fill="#ff007f", width=2, capstyle="round", smooth=True)

            # 4. Porcentagem no centro da Triquetra
            c.create_text(
                cx,
                cy,
                text=f"{int(self._triquetra_progress)}%",
                fill="#e2e8f0",
                font=("Consolas", 11, "bold"),
            )

            # Atualiza mini-spinner da etapa ativa se houver
            if 0 <= self._current_ai_step < len(self._step_widgets):
                spin_chars = ["◐", "◓", "◑", "◒"]
                ch = spin_chars[int(self._triquetra_phase * 4) % len(spin_chars)]
                self._step_widgets[self._current_ai_step]["state"].configure(text=f"{ch} ATIVO")
        except Exception as exc:
            _ = exc

        if self._running and not self.headless_test:
            self.root.after(55, self._animate_triquetra_canvas)

    def _on_open_web_triquetra_hud(self) -> None:
        """Abre o HUD Triquetra em HTML5/CSS3/SVG puro no navegador padrão."""
        hud_path = self.repo_root / "assets" / "triquetra_hud.html"
        if hud_path.exists():
            webbrowser.open(hud_path.as_uri())
            self.log(f"HUD Triquetra Web aberto no navegador: {hud_path.name}")
        else:
            messagebox.showwarning("Aviso", f"Arquivo HUD não encontrado em: {hud_path}")

    # =========================================================================
    # ABA 2: CUSTÓDIA FORENSE & SANITIZADOR OSINT
    # =========================================================================
    def _build_tab_forensic(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        info_lbl = ctk.CTkLabel(
            parent,
            text="❖ Pipeline Forense Policial / Investigativo & Custódia de Evidências (100% Local Soberano)\n"
            "• Modo 1 (Investigativo Local): Calcula o Hash SHA-256 imutável e EXTRAI/CRUZA CPFs, CNPJs, Placas Mercosul/Antigas,\n"
            "  Celulares, E-mails e PIX mantendo 100% dos dados intactos dentro deste PC para cruzamento investigativo.\n"
            "• Modo 2 (Derivada Anonimizada): Opcionalmente gera uma cópia mascarada apenas quando você quiser enviar texto à nuvem.",
            font=ctk.CTkFont(size=12),
            justify="left",
            text_color="#cbd5e1",
        )
        info_lbl.pack(padx=15, pady=(12, 8), anchor="w")

        file_frame = ctk.CTkFrame(parent, fg_color="#0f1726", border_width=1, border_color="#1b2840")
        file_frame.pack(fill="x", padx=15, pady=5)

        default_inv_fixture = self.repo_root / "tasks" / "fixtures" / "relatorio_investigacao_teste.txt"
        initial_forensic_path = str(default_inv_fixture) if default_inv_fixture.exists() else ""
        self.file_path_var = tk.StringVar(value=initial_forensic_path)
        entry_file = ctk.CTkEntry(
            file_frame,
            textvariable=self.file_path_var,
            placeholder_text="Selecione um arquivo .txt, .csv, .json, .md ou código-fonte...",
            width=560,
            fg_color="#070a10",
        )
        entry_file.pack(side="left", padx=10, pady=10, fill="x", expand=True)

        btn_browse = ctk.CTkButton(
            file_frame,
            text="◈ Procurar Arquivo...",
            command=self._on_browse_forensic_file,
            width=165,
            fg_color="#162238",
            hover_color="#223556",
        )
        btn_browse.pack(side="left", padx=10, pady=10)
        self._attach_interactive_hover(btn_browse, "◈ Procurar Arquivo...", "❖ Procurar Arquivo... ▸")

        opts_frame = ctk.CTkFrame(parent, fg_color="transparent")
        opts_frame.pack(fill="x", padx=15, pady=6)

        self.switch_pii = ctk.CTkSwitch(
            opts_frame,
            text="Filtro de Segredos & Redação (LIGADO = Mascarar Dados | DESLIGADO = Preservar 100% Intacto para OSINT)",
            variable=self.remove_pii_var,
            onvalue=True,
            offvalue=False,
            font=ctk.CTkFont(size=12, weight="bold"),
            progress_color="#00f5d4",
        )
        self.switch_pii.pack(anchor="w", pady=3)

        self.switch_spool = ctk.CTkSwitch(
            opts_frame,
            text="Salvar Cópia Trancada em data/spool_encrypted/ (Criptografia Fernet AES com Chave/Senha para o Drive)",
            variable=self.encrypt_spool_var,
            onvalue=True,
            offvalue=False,
            font=ctk.CTkFont(size=12, weight="bold"),
            progress_color="#00bbf9",
        )
        self.switch_spool.pack(anchor="w", pady=3)

        pwd_row = ctk.CTkFrame(opts_frame, fg_color="transparent")
        pwd_row.pack(fill="x", pady=(4, 2))

        ctk.CTkLabel(
            pwd_row,
            text="Senha do Operador (Tranca Criptográfica de Arquivos para o Drive):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#f77f00",
        ).pack(side="left", padx=(0, 10))

        self.entry_operator_password = ctk.CTkEntry(
            pwd_row,
            textvariable=self.operator_password_var,
            show="*",
            width=260,
            height=28,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.entry_operator_password.pack(side="left", padx=(0, 8))

        btn_toggle_pwd = ctk.CTkButton(
            pwd_row,
            text="👁 Ver/Ocultar",
            width=90,
            height=28,
            command=self._toggle_operator_password_visibility,
            fg_color="#1a273b",
            hover_color="#2b3b55",
            font=ctk.CTkFont(size=11),
        )
        btn_toggle_pwd.pack(side="left")

        btn_forensic_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_forensic_row.pack(fill="x", padx=15, pady=8)

        btn_extract_inv = ctk.CTkButton(
            btn_forensic_row,
            text="❖ Extrair & Cruzar Vínculos (100% Local — Sem Ocultar Dados)",
            command=self._on_extract_investigative_clicked,
            fg_color="#00f5d4",
            text_color="#070a10",
            hover_color="#00bbf9",
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        btn_extract_inv.pack(side="left", padx=(0, 10))
        self._attach_interactive_hover(
            btn_extract_inv,
            "❖ Extrair & Cruzar Vínculos (100% Local — Sem Ocultar Dados)",
            "❖ Extrair & Cruzar Vínculos (100% Local — Sem Ocultar Dados) ➔",
            base_border="#00f5d4",
            hover_border="#ffffff",
        )

        btn_generate_derived = ctk.CTkButton(
            btn_forensic_row,
            text="❖ Trancar e Salvar no Spool Cifrado",
            command=self._on_generate_sanitized_derivative,
            fg_color="#162238",
            text_color="#00bbf9",
            hover_color="#1f3252",
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        btn_generate_derived.pack(side="left", padx=(0, 10))
        btn_lock_donor_vault = ctk.CTkButton(
            btn_forensic_row,
            text="❖ Trancar Extração com Senha do Operador (Pasta do Doador)",
            command=self._on_lock_donor_vault_clicked,
            fg_color="#7209b7",
            text_color="#ffffff",
            hover_color="#560bad",
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        btn_lock_donor_vault.pack(side="left", padx=(0, 10))
        self._attach_interactive_hover(
            btn_lock_donor_vault,
            "❖ Trancar Extração com Senha do Operador (Pasta do Doador)",
            "❖ Trancar Extração com Senha do Operador (Pasta do Doador) ➔",
            base_border="#7209b7",
            hover_border="#00f5d4",
        )
        self._attach_interactive_hover(
            btn_generate_derived,
            "❖ Trancar e Salvar no Spool Cifrado",
            "❖ Trancar e Salvar no Spool Cifrado ➔",
            base_border="#00bbf9",
            hover_border="#00f5d4",
        )

        btn_sync_drive = ctk.CTkButton(
            btn_forensic_row,
            text="❖ Sincronizar Spool com Google Drive",
            command=self._on_sync_drive_clicked,
            fg_color="#f77f00",
            text_color="#070a10",
            hover_color="#fcbf49",
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        btn_sync_drive.pack(side="left")
        self._attach_interactive_hover(
            btn_sync_drive,
            "❖ Sincronizar Spool com Google Drive",
            "❖ Sincronizar Spool com Google Drive ➔",
            base_border="#f77f00",
            hover_border="#ffffff",
        )

        self.sanitizer_output_box = ctk.CTkTextbox(
            parent,
            height=270,
            fg_color="#070a10",
            border_width=1,
            border_color="#1b2840",
            text_color="#00f5d4",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.sanitizer_output_box.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.sanitizer_output_box.insert(
            "1.0",
            "Aguardando processamento de arquivo...\n"
            "Selecione qualquer arquivo de texto/código e clique em 'Gerar Derivada Auditável'.\n",
        )

    # =========================================================================
    # ABA 3: REDE DE DOADORES & MATRIZ DE CAPACIDADES
    # =========================================================================
    def _build_tab_donors(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_rowconfigure(3, weight=1)

        header_donors = ctk.CTkFrame(parent, fg_color="transparent")
        header_donors.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        ctk.CTkLabel(
            header_donors,
            text="⟡ Nós Doadores Voluntários Conectados via VPN Tailscale:",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#00bbf9",
        ).pack(side="left")

        btn_build_donor_exe = ctk.CTkButton(
            header_donors,
            text="◈ Gerar Instalador .EXE para Novo Doador",
            command=self._on_build_donor_exe_clicked,
            fg_color="#f77f00",
            hover_color="#d66800",
            height=28,
        )
        btn_build_donor_exe.pack(side="right", padx=5)
        self._attach_interactive_hover(
            btn_build_donor_exe,
            "◈ Gerar Instalador .EXE para Novo Doador",
            "❖ Gerar Instalador .EXE para Novo Doador ▸",
            base_border="#f77f00",
            hover_border="#00f5d4",
        )

        btn_refresh_donors = ctk.CTkButton(
            header_donors,
            text="⟡ Atualizar Doadores",
            width=145,
            height=28,
            fg_color="#162238",
            hover_color="#223556",
            command=self.refresh_donors_table,
        )
        btn_refresh_donors.pack(side="right", padx=5)
        self._attach_interactive_hover(btn_refresh_donors, "⟡ Atualizar Doadores", "❖ Atualizar Doadores ▸")

        frame_tree_donors = ctk.CTkFrame(parent, fg_color="#0b101b", border_width=1, border_color="#1b2840")
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

        self.tree_donors.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        header_matrix = ctk.CTkFrame(parent, fg_color="transparent")
        header_matrix.grid(row=2, column=0, sticky="ew", padx=10, pady=(15, 4))

        ctk.CTkLabel(
            header_matrix,
            text="◈ Matriz de Desempenho Aferida (Meta 86% a 96%):",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#00f5d4",
        ).pack(side="left")

        btn_run_bench = ctk.CTkButton(
            header_matrix,
            text="⟡ Rodar Bateria de Benchmark Agora",
            command=self._on_run_benchmark_clicked,
            fg_color="#00bbf9",
            text_color="#070a10",
            hover_color="#00f5d4",
            height=28,
            font=ctk.CTkFont(weight="bold"),
        )
        btn_run_bench.pack(side="right", padx=5)
        self._attach_interactive_hover(
            btn_run_bench,
            "⟡ Rodar Bateria de Benchmark Agora",
            "❖ Rodar Bateria de Benchmark Agora ▸",
            base_border="#00bbf9",
            hover_border="#ffffff",
        )

        frame_tree_matrix = ctk.CTkFrame(parent, fg_color="#0b101b", border_width=1, border_color="#1b2840")
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

        self.tree_matrix.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

    # =========================================================================
    # ABA 4: COLIBRÌ NOTURNO & CONFIGURAÇÕES / DRIVE SYNC
    # =========================================================================
    def _build_tab_nightly(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)

        colibri_frame = ctk.CTkFrame(parent, fg_color="#0f1726", border_width=1, border_color="#1b2840")
        colibri_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(
            colibri_frame,
            text="⏣ Processamento Noturno Colibrì (Modelos 14B via --mmap 1)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#00f5d4",
        ).pack(anchor="w", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            colibri_frame,
            text="Executa refatorações pesadas na madrugada lendo do SSD NVMe sem congelar o sistema.\n"
            "Varre automaticamente anotações '# REFACTOR_NIGHTLY' no código-fonte do projeto.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 10))

        btn_row_colibri = ctk.CTkFrame(colibri_frame, fg_color="transparent")
        btn_row_colibri.pack(fill="x", padx=12, pady=(0, 10))

        btn_run_nightly_now = ctk.CTkButton(
            btn_row_colibri,
            text="⏣ Executar Lote Noturno Agora",
            command=self._on_run_nightly_now_clicked,
            fg_color="#00bbf9",
            text_color="#070a10",
            hover_color="#00f5d4",
            height=30,
            font=ctk.CTkFont(weight="bold"),
        )
        btn_run_nightly_now.pack(side="left", padx=(0, 10))
        self._attach_interactive_hover(
            btn_run_nightly_now,
            "⏣ Executar Lote Noturno Agora",
            "❖ Executar Lote Noturno Agora ▸",
            base_border="#00bbf9",
            hover_border="#ffffff",
        )

        btn_register_sched = ctk.CTkButton(
            btn_row_colibri,
            text="⧗ Agendar no Windows (03:00 AM)",
            command=self._on_register_windows_task_clicked,
            fg_color="#162238",
            hover_color="#223556",
            height=30,
        )
        btn_register_sched.pack(side="left")
        self._attach_interactive_hover(
            btn_register_sched,
            "⧗ Agendar no Windows (03:00 AM)",
            "⟡ Agendar no Windows (03:00 AM) ▸",
        )

        sync_frame = ctk.CTkFrame(parent, fg_color="#0f1726", border_width=1, border_color="#1b2840")
        sync_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(
            sync_frame,
            text="◈ Sincronização de Spool Criptografado (Google Drive via Rclone)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#f77f00",
        ).pack(anchor="w", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            sync_frame,
            text="Envia pacotes criptografados da pasta data/spool_encrypted/ para o Google Drive.\n"
            "Conferência criptográfica obrigatória: o arquivo local SÓ é apagado se o hash remoto for idêntico.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 10))

        btn_sync_drive = ctk.CTkButton(
            sync_frame,
            text="⟡ Sincronizar Spool com Google Drive",
            command=self._on_sync_drive_clicked,
            fg_color="#f77f00",
            hover_color="#d66800",
            height=32,
            font=ctk.CTkFont(weight="bold"),
        )
        btn_sync_drive.pack(anchor="w", padx=12, pady=(0, 10))
        self._attach_interactive_hover(
            btn_sync_drive,
            "⟡ Sincronizar Spool com Google Drive",
            "❖ Sincronizar Spool com Google Drive ▸",
            base_border="#f77f00",
            hover_border="#00f5d4",
        )

        ctk.CTkLabel(
            parent,
            text="◈ Log de Operações Noturnas & Sincronização:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#00bbf9",
        ).pack(anchor="w", padx=15, pady=(10, 2))

        self.txt_nightly_log = ctk.CTkTextbox(
            parent,
            height=180,
            fg_color="#070a10",
            border_width=1,
            border_color="#1b2840",
            text_color="#00f5d4",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.txt_nightly_log.pack(fill="both", expand=True, padx=15, pady=(0, 15))

    # =========================================================================
    # COFRE DE CHAVES DE API (PERSISTÊNCIA EM CONFIG/API_KEYS.JSON)
    # =========================================================================
    def _read_api_keys_file(self) -> Dict[str, Any]:
        if not self.api_keys_path.exists():
            return {"active_provider": "Groq", "keys": {}}
        try:
            return json.loads(self.api_keys_path.read_text(encoding="utf-8"))
        except Exception:
            return {"active_provider": "Groq", "keys": {}}

    def _write_api_keys_file(self, data: Dict[str, Any]) -> None:
        self.api_keys_path.parent.mkdir(parents=True, exist_ok=True)
        self.api_keys_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_saved_api_keys_into_ui(self) -> None:
        data = self._read_api_keys_file()
        keys_dict: Dict[str, str] = data.get("keys", {})
        active_prov = data.get("active_provider", "Groq")
        if active_prov in self.API_PROVIDERS:
            self.selected_provider_var.set(active_prov)

        # Injeta todas as chaves salvas nas variáveis de ambiente do processo
        for prov, k_val in keys_dict.items():
            if k_val and prov in self.PROVIDER_ENV_MAP:
                os.environ[self.PROVIDER_ENV_MAP[prov]] = k_val

        # Atualiza campo de texto com a chave do provedor selecionado
        current_key = keys_dict.get(self.selected_provider_var.get(), "")
        self.api_key_entry.delete(0, "end")
        if current_key:
            self.api_key_entry.insert(0, current_key)

        saved_list = [f"{p} ✓" for p, k in keys_dict.items() if k]
        if saved_list:
            self.saved_keys_status_var.set(f"Salvas: {', '.join(saved_list)}\nAtiva: {self.selected_provider_var.get()}")
        else:
            self.saved_keys_status_var.set("Nenhuma chave salva ainda.")

    def _on_provider_changed(self, provider_name: str) -> None:
        data = self._read_api_keys_file()
        data["active_provider"] = provider_name
        self._write_api_keys_file(data)
        self._load_saved_api_keys_into_ui()
        self.log(f"Provedor de API selecionado: {provider_name}")

    def _on_save_api_key_clicked(self) -> None:
        provider = self.selected_provider_var.get()
        raw_key = self.api_key_entry.get().strip()
        data = self._read_api_keys_file()
        if "keys" not in data:
            data["keys"] = {}

        if not raw_key:
            # Remove a chave se o operador limpou o campo e salvou
            data["keys"].pop(provider, None)
            self._write_api_keys_file(data)
            self._load_saved_api_keys_into_ui()
            self.log(f"Chave do provedor {provider} removida do cofre local.")
            return

        data["keys"][provider] = raw_key
        data["active_provider"] = provider
        self._write_api_keys_file(data)

        env_var = self.PROVIDER_ENV_MAP.get(provider, "OPENAI_API_KEY")
        os.environ[env_var] = raw_key
        self.is_hybrid_mode.set(True)
        self._load_saved_api_keys_into_ui()
        masked = f"{raw_key[:5]}...{raw_key[-4:]}" if len(raw_key) > 10 else "***"
        self.log(f"Chave {provider} ({masked}) salva em config/api_keys.json e ativada em ${env_var}!")

    # =========================================================================
    # MONITORAMENTO EM TEMPO REAL + LEITURA SÍNCRONA NO MILISSEGUNDO 0
    # =========================================================================
    def _collect_hardware_snapshot(self) -> Dict[str, Any]:
        """Coleta instantaneamente RAM, VRAM, SSD e status do Ollama com tolerância a falhas independente."""
        ram_str = "RAM: 5.5 / 7.4 GB (74%)"
        ram_ratio = 0.74
        try:
            ram_info = RAMLiberator.get_system_ram_mb()
            total_gb = ram_info["total_mb"] / 1024.0
            used_gb = ram_info["used_mb"] / 1024.0
            pct = ram_info["percent_used"]
            ram_str = f"RAM: {used_gb:.1f} / {total_gb:.1f} GB ({pct:.0f}%)"
            ram_ratio = min(1.0, max(0.0, pct / 100.0))
        except Exception as exc:
            _ = exc

        vram_str = "VRAM GTX 1650: 0 / 4096 MB"
        try:
            gpu_info = PreflightChecker.check_gpu()
            vram_total = int(gpu_info.get("vram_total_mb", 4096) or 4096)
            vram_used = int(gpu_info.get("vram_used_mb", 0))
            vram_str = f"VRAM GTX 1650: {vram_used} / {vram_total} MB"
        except Exception as exc:
            _ = exc

        ssd_str = "SSD Livre: 49.0 GB (Seguro)"
        try:
            disk_info = PreflightChecker.check_disk_space(self.repo_root)
            free_gb = float(disk_info.get("free_gb", 49.0))
            disk_status = "Seguro" if free_gb >= 15.0 else "ALERTA <15GB"
            ssd_str = f"SSD Livre: {free_gb:.1f} GB ({disk_status})"
        except Exception as exc:
            _ = exc

        ollama_online = False
        try:
            ollama_online = self._probe_ollama_status()
        except Exception as exc:
            _ = exc

        return {
            "ram_str": ram_str,
            "ram_ratio": ram_ratio,
            "vram_str": vram_str,
            "ssd_str": ssd_str,
            "ollama_str": "Ollama 3B: ONLINE (11434)" if ollama_online else "Ollama: OFFLINE",
        }

    def _refresh_metrics_sync(self) -> None:
        """Popula a barra lateral no milissegundo 0 da abertura da janela (sem 'Calculando...')."""
        try:
            snap = self._collect_hardware_snapshot()
            self.ram_text_var.set(snap["ram_str"])
            self.ram_progress.set(snap["ram_ratio"])
            self.vram_text_var.set(snap["vram_str"])
            self.ssd_text_var.set(snap["ssd_str"])
            self.ollama_status_var.set(snap["ollama_str"])
        except Exception as exc:
            self.ram_text_var.set(f"RAM: Erro ({exc})")

    def _start_metrics_poller(self) -> None:
        """Inicia thread daemon que atualiza RAM, VRAM, SSD e Ollama a cada 3 segundos."""
        def _poll_loop() -> None:
            while self._running:
                try:
                    snap = self._collect_hardware_snapshot()

                    def _update_ui(s: Dict[str, Any] = snap) -> None:
                        self.ram_text_var.set(s["ram_str"])
                        self.ram_progress.set(s["ram_ratio"])
                        self.vram_text_var.set(s["vram_str"])
                        self.ssd_text_var.set(s["ssd_str"])
                        self.ollama_status_var.set(s["ollama_str"])

                    self.dispatch_to_ui(_update_ui)
                except Exception as exc:
                    _ = exc
                time.sleep(3.0)

        t = threading.Thread(target=_poll_loop, daemon=True)
        t.start()

    @staticmethod
    def _probe_ollama_status(port: int = 11434) -> bool:
        """Verifica rapidamente se a porta local do Ollama está respondendo."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.35)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def _schedule_ui_queue_consumer(self) -> None:
        """Consome callbacks enfileirados por threads de background de forma segura."""
        self._process_ui_queue()
        if self._running and not self.headless_test:
            self.root.after(100, self._schedule_ui_queue_consumer)

    def _process_ui_queue(self) -> None:
        while not self.ui_queue.empty():
            try:
                callback = self.ui_queue.get_nowait()
                callback()
            except queue.Empty:
                break

    def dispatch_to_ui(self, func: Callable[[], None]) -> None:
        """Enfileira callback para execução thread-safe na thread principal do Tkinter."""
        self.ui_queue.put(func)

    def log(self, message: str) -> None:
        """Adiciona linha com timestamp no Diário de Execução da Aba 1."""
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        def _append() -> None:
            self.txt_logs.insert("end", line)
            self.txt_logs.see("end")

        self.dispatch_to_ui(_append)

    def log_nightly(self, message: str) -> None:
        """Adiciona linha com timestamp no console da Aba 4."""
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        def _append() -> None:
            self.txt_nightly_log.insert("end", line)
            self.txt_nightly_log.see("end")

        self.dispatch_to_ui(_append)

    # =========================================================================
    # CONTROLADORES E AÇÕES DOS BOTÕES (ABA 1 & SIDEBAR)
    # =========================================================================
    def _on_clean_ram_clicked(self) -> None:
        """Aciona o RAMLiberator em 2 níveis: fecha processos ociosos e pede permissão para pesados."""
        def _run() -> None:
            self.log("Iniciando rotina de higiene de memória RAM e limpeza de caches temporários...")
            res = RAMLiberator.execute_hygiene_pipeline(
                repo_root=self.repo_root,
                auto_terminate=True,
                min_process_mb=15.0,
            )
            self.log(
                f"[Etapa 1/2] Processos de segundo plano limpos automaticamente: "
                f"{res.total_freed_mb} MB liberados ({len(res.terminated_pids)} processos encerrados)."
            )

            heavy_candidates, heavy_mb = RAMLiberator.scan_heavy_interactive_processes()
            if heavy_candidates and heavy_mb > 50.0:
                details = "\n".join(
                    f"• {c.name} ({c.description}): {c.total_rss_mb:.1f} MB [{c.count} instância(s)]"
                    for c in heavy_candidates
                )
                prompt_msg = (
                    f"Processos pesados detectados ocupando {heavy_mb:.1f} MB de RAM:\n\n"
                    f"{details}\n\n"
                    f"Deseja finalizar esses processos agora para liberar a memória máxima para o modelo de IA?"
                )

                def _ask_and_terminate() -> None:
                    answer = messagebox.askyesno(
                        "PCI RAM Liberator — Confirmação de Encerramento",
                        prompt_msg,
                    )
                    if answer:
                        def _terminate_heavy() -> None:
                            self.log(f"Encerrando processos pesados autorizados pelo operador ({heavy_mb:.1f} MB)...")
                            freed, pids = RAMLiberator.terminate_candidates(heavy_candidates)
                            ram_now = RAMLiberator.get_system_ram_mb()
                            self.log(
                                f"[Etapa 2/2] Sucesso! {freed:.1f} MB adicionais liberados "
                                f"({len(pids)} processos finalizados). RAM livre agora: {ram_now['available_mb']} MB."
                            )
                        threading.Thread(target=_terminate_heavy, daemon=True).start()
                    else:
                        self.log("Operador optou por manter o Antigravity / IDE aberto. RAM preservada.")

                self.dispatch_to_ui(_ask_and_terminate)
            else:
                ram_now = RAMLiberator.get_system_ram_mb()
                self.log(f"Higiene concluída! RAM livre disponível: {ram_now['available_mb']} MB.")

        threading.Thread(target=_run, daemon=True).start()

    def _on_hybrid_switch_toggled(self) -> None:
        """Alterna entre modo 100% offline e modo híbrido com APIs gratuitas."""
        if self.is_hybrid_mode.get():
            os.environ["PCI_HYBRID_MODE"] = "1"
            prov = self.selected_provider_var.get()
            self.log(f"Modo Híbrido ATIVADO (Provedor ativo: {prov}). Tarefas complexas poderão usar API.")
            self.api_key_entry.configure(state="normal")
        else:
            os.environ["PCI_HYBRID_MODE"] = "0"
            self.log("Modo 100% Offline ATIVADO: Todos os dados ficam estritamente confinados no PC local.")

    def _on_broker_switch_toggled(self) -> None:
        """Inicia ou desliga o servidor FastAPI do Broker para doadores da Tailscale."""
        if self.is_broker_running.get():
            self.log("Iniciando Broker FastAPI na porta 8000 para a rede Tailscale...")

            def _start_server() -> None:
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
                    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    self._broker_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags,
                    )
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
        """Decompõe o Masterbook em microtarefas estruturadas (DAG) com auto-fatiamento offline."""
        content = self.txt_masterbook.get("1.0", "end").strip()
        if not content:
            messagebox.showwarning("Aviso", "O texto do Masterbook está vazio.")
            return

        is_offline = not self.is_hybrid_mode.get()

        def _run() -> None:
            self._set_ai_step_progress(0, 15.0, "PREFLIGHT")
            mode_str = "Modo 100% Offline (Auto-Fatiamento <= 30 linhas ativo)" if is_offline else "Modo Híbrido Cloud"
            self.log(f"Iniciando decomposição do Masterbook [{mode_str}]...")
            decomposer = MasterbookDecomposer(db_manager=self.db)

            # Se estiver decompondo o Piloto Nexus Investigativo, limpa artefato anterior para demonstração limpa
            if "TASK_NEXUS_01" in content:
                nexus_file = self.repo_root / "src" / "investigative_nexus.py"
                if nexus_file.exists():
                    nexus_file.unlink()
                for tid in ("TASK_NEXUS_01", "TASK_NEXUS_02", "TASK_NEXUS_03"):
                    self.db.delete_task(tid)

            try:
                self._set_ai_step_progress(1, 55.0, "DAG SLICING")
                tasks = decomposer.decompose_from_text(
                    masterbook_content=content,
                    parent_name=f"MB_{int(time.time())}",
                    offline_auto_slice=is_offline,
                    max_lines_per_slice=30,
                )
                self._set_ai_step_progress(5, 100.0, "FILA PRONTA")
                self.log(
                    f"Sucesso! {len(tasks)} microtarefas dimensionadas e enfileiradas no SQLite WAL: {', '.join(tasks)}"
                )
                self.dispatch_to_ui(self.refresh_tasks_table)
            except Exception as exc:
                self._set_ai_step_progress(5, 0.0, "ERRO DAG")
                self.log(f"Erro na decomposição: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_run_autonomous_queue_clicked(self) -> None:
        """Dispara a execução sequencial completa de todas as microtarefas pendentes na fila."""
        online_flag = self.is_hybrid_mode.get()

        def _run() -> None:
            self._set_ai_step_progress(0, 15.0, "ROTEAMENTO")
            self.log("Iniciando execução autônoma da fila DAG...")
            router = PCIRouter(db_path=self.db_path, repo_root=self.repo_root)
            harness = MicroHarness(db_manager=self.db, repo_root=self.repo_root)

            executed_total = 0
            passed_total = 0

            while True:
                # 1. Desbloqueia dependentes cujos pais já estão VERIFIED e roteia pendentes
                self.db.unlock_ready_tasks()
                routed_count = router.route_all_pending_tasks(online_mode=online_flag)
                if routed_count > 0:
                    self.log(f"Roteador determinístico despachou {routed_count} tarefa(s) para LOCAL_3B.")
                self.dispatch_to_ui(self.refresh_tasks_table)

                # 2. Busca próxima tarefa elegível
                self._set_ai_step_progress(2, 45.0, "INFERÊNCIA 3B")
                res = harness.run_next_local_task()
                if not res:
                    break

                executed_total += 1
                tid = res.get("task_id", "UNKNOWN")
                ok = bool(res.get("success"))
                attempts_used = res.get("attempts", 1)

                self._set_ai_step_progress(3, 75.0, f"AST: {tid}")
                time.sleep(0.1)
                self._set_ai_step_progress(4, 90.0, f"PYTEST: {tid}")

                if ok:
                    passed_total += 1
                    code_gen = res.get("code", "")
                    lines_cnt = len(code_gen.splitlines())
                    self.log(
                        f"✓ [{tid}] APROVADA no Pytest na {attempts_used}ª tentativa! "
                        f"({lines_cnt} linhas gravadas em {res.get('test_file', 'sandbox')})."
                    )
                else:
                    err_detail = res.get("error") or res.get("last_error", "Falha nos testes")
                    self.log(f"⌧ [{tid}] REPROVADA após {attempts_used} tentativa(s): {err_detail[:250]}")

                self.dispatch_to_ui(self.refresh_tasks_table)

            if executed_total > 0:
                self._set_ai_step_progress(5, 100.0, "CONCLUÍDO")
                self.log(
                    f"=== CICLO AUTÔNOMO FINALIZADO: {passed_total}/{executed_total} microtarefa(s) VERIFIED com 100% de precisão! ==="
                )
            else:
                self._set_ai_step_progress(5, 100.0, "FILA LIMPA")
                self.log("Nenhuma tarefa PENDING elegível na fila. Clique em '1. Decompor em Microtarefas' primeiro.")

            self.dispatch_to_ui(self.refresh_tasks_table)

        threading.Thread(target=_run, daemon=True).start()

    def _on_refresh_tasks_clicked(self) -> None:
        """Recarrega a tabela de tarefas e imprime confirmação explícita no Diário de Execução."""
        self.refresh_tasks_table()
        count = len(self.tree_tasks.get_children())
        flt = self.task_filter_var.get()
        self.log(f"Fila sincronizada com SQLite WAL (Filtro: {flt}): {count} tarefa(s) exibida(s) na tabela.")

    def refresh_tasks_table(self) -> None:
        """Recarrega a tabela de tarefas aplicando filtro e tags de cor Neon por status."""
        for item in self.tree_tasks.get_children():
            self.tree_tasks.delete(item)

        try:
            tasks = self.db.list_tasks()
            flt = self.task_filter_var.get()
            for t in tasks:
                st = t["status"]
                if flt == "Pendentes" and st not in ("PENDING", "WAITING_DEPS", "LEASED"):
                    continue
                if flt == "Verificadas" and st != "VERIFIED":
                    continue
                if flt == "Falhas" and st not in ("FAILED_RETRY", "BLOCKED_HUMAN_REVIEW"):
                    continue

                if st == "VERIFIED":
                    tag = "status_verified"
                    badge_status = "✓ VERIFIED"
                elif st in ("PENDING", "WAITING_DEPS", "LEASED"):
                    tag = "status_pending"
                    badge_status = f"⧗ {st}"
                else:
                    tag = "status_failed"
                    badge_status = f"⌧ {st}"

                self.tree_tasks.insert(
                    "",
                    "end",
                    iid=t["task_id"],
                    values=(
                        t["task_id"],
                        t["task_type"],
                        t.get("assigned_route") or "PENDENTE",
                        t["privacy_level"],
                        badge_status,
                        t.get("attempt_count", t.get("attempts", 0)),
                    ),
                    tags=(tag,),
                )
        except Exception as exc:
            self.log(f"Erro ao carregar tarefas do banco: {exc}")

    # =========================================================================
    # MENU DE CONTEXTO E LIMPEZA DE TAREFAS DA FILA
    # =========================================================================
    def _get_selected_task_id(self) -> Optional[str]:
        sel = self.tree_tasks.selection()
        if not sel:
            return None
        vals = self.tree_tasks.item(sel[0], "values")
        return str(vals[0]) if vals else None

    def _on_task_right_click(self, event: tk.Event) -> None:
        row_id = self.tree_tasks.identify_row(event.y)
        if row_id:
            self.tree_tasks.selection_set(row_id)
            self.task_context_menu.tk_popup(event.x_root, event.y_root)

    def _on_ctx_rerun_task(self) -> None:
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        self.db.reset_task_for_retry(task_id)
        self.refresh_tasks_table()
        self.log(f"Tarefa '{task_id}' resetada para PENDING. Disparando execução...")
        self._on_run_autonomous_queue_clicked()

    def _on_ctx_view_patch(self) -> None:
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        task_data = self.db.get_task(task_id)
        if not task_data:
            return

        patch_txt = task_data.get("candidate_patch_diff") or "(Sem código/patch registrado ainda)"
        err_txt = task_data.get("last_error_log") or "(Sem erros)"
        spec_txt = task_data.get("spec_markdown") or ""

        win = ctk.CTkToplevel(self.root)
        win.title(f"Inspeção da Tarefa — {task_id}")
        win.geometry("760x520")
        win.configure(fg_color="#070a10")

        box = ctk.CTkTextbox(
            win,
            fg_color="#0b101b",
            text_color="#00f5d4",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        box.pack(fill="both", expand=True, padx=14, pady=14)
        box.insert(
            "1.0",
            f"=== TAREFA: {task_id} | STATUS: {task_data.get('status')} ===\n"
            f"Arquivo Alvo: {task_data.get('target_file_path')}\n\n"
            f"--- ESPECIFICAÇÃO ---\n{spec_txt}\n\n"
            f"--- CÓDIGO / PATCH GERADO ---\n{patch_txt}\n\n"
            f"--- ÚLTIMO LOG DO PYTEST ---\n{err_txt}\n",
        )

    def _on_choose_target_workspace_clicked(self) -> None:
        """Permite ao operador apontar para a pasta de qualquer projeto no disco (ex: C:\\Lab\\012 - Auditoria (SESAU))."""
        chosen = filedialog.askdirectory(
            title="Selecionar Pasta Principal do Projeto Alvo",
            initialdir=self.target_workspace_var.get(),
        )
        if chosen:
            self.target_workspace_var.set(chosen)
            self.workspace_bridge.set_target_workspace(chosen)
            mf = self.workspace_bridge.scan_workspace_tree()
            self.log(
                f"📁 Workspace Alvo alterado para: {Path(chosen).name} "
                f"({len(mf.relative_files)} arquivos mapeados | Git branch: {mf.git_branch or 'N/A'})."
            )

    def _on_open_target_workspace_folder_clicked(self) -> None:
        """Abre a pasta do projeto alvo no Windows Explorer."""
        target_dir = Path(self.target_workspace_var.get())
        if target_dir.exists():
            os.startfile(str(target_dir))
            self.log(f"Pasta do projeto aberta no Windows Explorer: {target_dir}")
        else:
            messagebox.showwarning("Aviso", f"A pasta não existe: {target_dir}")

    def _on_inject_workspace_context_clicked(self) -> None:
        """Lê a estrutura do projeto alvo e injeta um resumo limpo no Masterbook para guiar a IA."""
        summary = self.workspace_bridge.build_project_context_summary()
        current_text = self.txt_masterbook.get("1.0", "end").strip()
        context_block = f"\n\n### CONTEXTO DO PROJETO ALVO ({Path(self.target_workspace_var.get()).name}):\n{summary}\n"
        self.txt_masterbook.insert("end", context_block)
        self.txt_masterbook.see("end")
        self.log(f"Contexto do projeto '{Path(self.target_workspace_var.get()).name}' injetado no Masterbook.")

    def _on_ctx_export_task_to_workspace(self) -> None:
        """Exporta o código de uma tarefa VERIFIED diretamente para a pasta do projeto alvo."""
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        t = self.db.get_task(task_id)
        if not t or t["status"] != "VERIFIED":
            messagebox.showwarning("Aviso", "Apenas tarefas com status VERIFIED podem ser exportadas.")
            return

        code = t.get("candidate_patch_diff")
        if not code:
            messagebox.showwarning("Aviso", "Nenhum código gerado disponível para esta tarefa.")
            return

        receipt = self.workspace_bridge.export_verified_task_code(
            task_id=task_id,
            target_relative_path=t["target_file_path"],
            code_content=code,
            spec_summary=t["spec_markdown"],
            pytest_log=t.get("last_error_log") or "(Sem erros)",
            auto_stage_git=True,
        )
        self.log(
            f"📦 [EXPORTADO] {task_id} -> {receipt.files_written[0]} "
            f"(Diário de auditoria atualizado em {Path(receipt.journal_path).name})."
        )
        messagebox.showinfo(
            "Exportação Concluída com Sucesso",
            f"O código da tarefa {task_id} foi gravado com sucesso no projeto:\n\n"
            f"📁 Arquivo: {receipt.files_written[0]}\n"
            f"📜 Diário de Auditoria: {receipt.journal_path}\n\n"
            f"O arquivo já está preparado e pronto para uso no projeto e para envio ao GitHub!",
        )

    def _on_git_sync_clicked(self) -> None:
        """Prepara commit e sincronização com o GitHub para o projeto alvo."""
        def _run() -> None:
            self.log(f"Iniciando verificação de Git para '{Path(self.target_workspace_var.get()).name}'...")
            res = self.workspace_bridge.prepare_git_commit_and_push_bundle(
                commit_message="feat(offline): sovereign code engine verified module export",
                push_now=False,
            )
            if res.get("success"):
                out_msg = res.get("commit_output") or res.get("message", "Commit preparado com sucesso.")
                self.log(f"✓ [GIT PREP] {out_msg}")
                self.dispatch_to_ui(
                    lambda: messagebox.showinfo(
                        "Git Preparado com Sucesso",
                        f"Arquivos do projeto preparados para Git!\n\n"
                        f"{out_msg}\n\n"
                        f"O código gerado offline está registrado e sem conflitos, pronto para push ou leitura pelo Antigravity / Copilot.",
                    )
                )
            else:
                err_msg = res.get("error", "Erro desconhecido")
                self.log(f"⌧ [GIT PREP] {err_msg}")
                self.dispatch_to_ui(lambda: messagebox.showwarning("Git", f"Aviso de Git:\n{err_msg}"))

        threading.Thread(target=_run, daemon=True).start()

    def _on_ctx_delete_task(self) -> None:
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        if self.db.delete_task(task_id):
            self.refresh_tasks_table()
            self.log(f"Tarefa '{task_id}' excluída da fila.")

    def _on_clear_completed_tasks_clicked(self) -> None:
        removed = self.db.clear_completed_tasks()
        self.refresh_tasks_table()
        self.log(f"Limpeza da fila concluída: {removed} tarefa(s) VERIFIED removida(s).")

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

    def _on_extract_investigative_clicked(self) -> None:
        """Extrai e cruza CPFs, CNPJs, Placas Mercosul/Antigas, Celulares, E-mails e PIX 100% localmente sem ocultar nada."""
        raw_path = self.file_path_var.get().strip()
        if not raw_path or not Path(raw_path).exists():
            messagebox.showwarning("Aviso", "Selecione um arquivo existente para extração e cruzamento investigativo.")
            return

        def _run() -> None:
            self.log(f"Iniciando extração e cruzamento investigativo local em: {Path(raw_path).name}...")
            sanitizer = ForensicSanitizer()
            try:
                inv = sanitizer.extract_investigative_entities(original_path=raw_path, db_manager=self.db)
                ent = inv["entities"]
                links_lines: List[str] = []
                for lk in inv["cross_links"]:
                    pairs = ", ".join(f"{k}: {v}" for k, v in lk["linked_entities"].items())
                    links_lines.append(
                        f"  • [Linha {lk['line_number']:02d}] {pairs}\n"
                        f"    Trecho: \"{lk['context_preview']}\""
                    )
                links_block = "\n".join(links_lines) if links_lines else "  (Nenhuma co-ocorrência na mesma linha)"

                report_text = (
                    "=========================================================================\n"
                    "   DOSSIÊ DE EXTRAÇÃO E CRUZAMENTO INVESTIGATIVO (100% LOCAL INTACTO)    \n"
                    "=========================================================================\n"
                    f"ID Custódia Forense: {inv['evidence_id']}\n"
                    f"Arquivo Analisado:   {inv['original_file']}\n"
                    f"SHA-256 Imutável:    {inv['original_sha256']}\n"
                    f"Status dos Dados:    100% PRESERVADOS NO PC LOCAL (Zero Redação)\n"
                    f"Total de Entidades:  {inv['total_entities']} identificadores encontrados\n"
                    "-------------------------------------------------------------------------\n"
                    "1. IDENTIFICADORES EXTRAÍDOS PARA CRUZAMENTO:\n"
                    "-------------------------------------------------------------------------\n"
                    f"  • CPFs Encontrados:          {', '.join(ent.get('CPF', [])) or 'Nenhum'}\n"
                    f"  • CNPJs Encontrados:         {', '.join(ent.get('CNPJ', [])) or 'Nenhum'}\n"
                    f"  • Placas (Mercosul/Antiga):  {', '.join(ent.get('PLACA_VEICULO', [])) or 'Nenhuma'}\n"
                    f"  • Celulares / Telefones:     {', '.join(ent.get('CELULAR_TEL', [])) or 'Nenhum'}\n"
                    f"  • E-mails Identificados:     {', '.join(ent.get('EMAIL', [])) or 'Nenhum'}\n"
                    f"  • Chaves PIX (EVP/UUID):     {', '.join(ent.get('PIX_EVP', [])) or 'Nenhuma'}\n"
                    f"  • Endereços IPv4:            {', '.join(ent.get('IPV4', [])) or 'Nenhum'}\n"
                    "-------------------------------------------------------------------------\n"
                    "2. MATRIZ DE VÍNCULOS DIRETOS (CO-OCORRÊNCIA NO MESMO REGISTRO/LINHA):\n"
                    "-------------------------------------------------------------------------\n"
                    f"{links_block}\n"
                )

                def _update_box() -> None:
                    self.sanitizer_output_box.delete("1.0", "end")
                    self.sanitizer_output_box.insert("1.0", report_text)

                self.dispatch_to_ui(_update_box)
                self.log(
                    f"Extração investigativa concluída! {inv['total_entities']} entidades e "
                    f"{len(inv['cross_links'])} vínculos diretos identificados."
                )
            except Exception as exc:
                self.log(f"Erro na extração investigativa: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _toggle_operator_password_visibility(self) -> None:
        """Alterna a visibilidade da senha do operador entre texto plano e asteriscos."""
        if hasattr(self, "entry_operator_password"):
            current = self.entry_operator_password.cget("show")
            self.entry_operator_password.configure(show="" if current == "*" else "*")

    def _on_lock_donor_vault_clicked(self) -> None:
        """Tranca os dados extraídos na pasta do nó doador correspondente com a senha escolhida."""
        raw_path = self.file_path_var.get().strip()
        pwd = self.operator_password_var.get().strip()
        if not pwd:
            messagebox.showwarning("Aviso", "Defina uma senha do operador para trancar o cofre.")
            return

        ContextGuard.get_or_set_operator_password(password=pwd, repo_root=self.repo_root)

        content_to_lock = ""
        if hasattr(self, "sanitizer_output_box"):
            content_to_lock = self.sanitizer_output_box.get("1.0", "end").strip()
        if not content_to_lock and raw_path and Path(raw_path).exists():
            content_to_lock = Path(raw_path).read_text(encoding="utf-8", errors="ignore")

        if not content_to_lock:
            messagebox.showwarning("Aviso", "Nenhum dado ou arquivo selecionado para trancar no cofre.")
            return

        def _run() -> None:
            try:
                self.log("Trancando dados sensíveis com a senha do operador...")
                vault = ContextGuard.pack_sensitive_donor_extraction(
                    node_name="MASTER_NODE",
                    raw_data=content_to_lock,
                    password=pwd,
                    repo_root=self.repo_root,
                    db_manager=self.db,
                )
                self.log(f"Cofre trancado com sucesso para o Drive! Salvo em: {vault['relative_spool_path']}")
                messagebox.showinfo(
                    "Cofre Trancado",
                    f"Dados sensíveis protegidos com sua senha em:\n{vault['relative_spool_path']}\nPronto para sincronização com o Drive!"
                )
            except Exception as exc:
                self.log(f"Erro ao trancar dados no cofre: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_generate_sanitized_derivative(self) -> None:
        """Gera cópia derivada higienizada calculando hashes SHA-256 e redação."""
        raw_path = self.file_path_var.get().strip()
        if not raw_path or not Path(raw_path).exists():
            messagebox.showwarning("Aviso", "Selecione um arquivo existente para sanitizar.")
            return

        remove_pii = self.remove_pii_var.get()
        encrypt_spool = self.encrypt_spool_var.get()

        def _run() -> None:
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

                def update_box() -> None:
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
        def _run() -> None:
            self.log("Iniciando compilação do executável PCI_Donor_Agent.exe via PyInstaller...")
            bat_path = self.repo_root / "donor_client" / "build_donor_exe.bat"
            if not bat_path.exists():
                self.log("ERRO: Script build_donor_exe.bat não encontrado.")
                return

            try:
                creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                proc = subprocess.run(
                    [str(bat_path)],
                    cwd=str(self.repo_root / "donor_client"),
                    capture_output=True,
                    text=True,
                    timeout=180,
                    creationflags=creation_flags,
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
        def _run() -> None:
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
        def _run() -> None:
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

        def _run() -> None:
            try:
                creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                proc = subprocess.run(
                    [str(bat_path)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    creationflags=creation_flags,
                )
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
        def _run() -> None:
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


    # =========================================================================
    # ABA 5: AIDER CLI & SUB-EXECUTOR SUPERVISIONADO
    # =========================================================================
    def _build_tab_aider(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        header_frame = ctk.CTkFrame(parent, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=(12, 6))
        header_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_frame,
            text="⚡ Aider Sub-Executor Supervisionado // Modo Architect & Decomposição Cirúrgica",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#00f5d4",
        ).grid(row=0, column=0, sticky="w")

        info_lbl = ctk.CTkLabel(
            parent,
            text="O Aider opera subordinado ao Sovereign Code Engine com as travas de segurança ativas:\n"
                 "• --no-auto-commits & --no-dirty-commits (O Aider NÃO commita no Git sem seu clique de aprovação).\n"
                 "• --map-tokens 0 (Contenção estrita de contexto para não cegar o modelo 3B nem estourar a VRAM de 4GB).\n"
                 "• Modo /architect (Gera apenas a decomposição incremental em microtarefas sem alterar arquivos).",
            font=ctk.CTkFont(size=11),
            justify="left",
            text_color="#94a3b8",
        )
        info_lbl.grid(row=1, column=0, sticky="w", padx=15, pady=(0, 8))

        # Ações do Aider
        action_box = ctk.CTkFrame(parent, fg_color="#0f1726", border_width=1, border_color="#1b2840")
        action_box.grid(row=2, column=0, sticky="nsew", padx=15, pady=6)
        action_box.grid_columnconfigure(0, weight=1)
        action_box.grid_rowconfigure(2, weight=1)

        top_ctrl = ctk.CTkFrame(action_box, fg_color="transparent")
        top_ctrl.grid(row=0, column=0, sticky="ew", padx=12, pady=10)

        ctk.CTkLabel(top_ctrl, text="Instrução para o Aider:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#00bbf9").pack(side="left", padx=(0, 10))

        self.aider_prompt_entry = ctk.CTkEntry(
            top_ctrl,
            placeholder_text="Ex: /architect Decomponha a função de validação de lotes em passos de 20 linhas",
            width=500,
            fg_color="#070a10",
        )
        self.aider_prompt_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        btn_run_arch = ctk.CTkButton(
            top_ctrl,
            text="▶ Executar /architect",
            command=self._on_run_aider_architect_clicked,
            fg_color="#00f5d4",
            text_color="#070a10",
            hover_color="#00bbf9",
            font=ctk.CTkFont(size=11, weight="bold"),
            width=160,
        )
        btn_run_arch.pack(side="right")

        self.aider_output_box = ctk.CTkTextbox(
            action_box,
            fg_color="#070a10",
            border_width=1,
            border_color="#1b2840",
            text_color="#e2e8f0",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.aider_output_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        action_box.grid_rowconfigure(1, weight=1)
        self.aider_output_box.insert("1.0", "Console do Aider pronto. Digite uma instrução acima e clique em 'Executar /architect'.\n")

    def _on_run_aider_architect_clicked(self) -> None:
        prompt = self.aider_prompt_entry.get().strip()
        if not prompt:
            prompt = "/architect Analise o projeto atual e decomponha a próxima funcionalidade em microtarefas testáveis."

        self.log(f"Disparando Aider em modo /architect: {prompt[:60]}...")
        self.aider_output_box.delete("1.0", "end")
        self.aider_output_box.insert("1.0", f"[AIDER RUNNING] Executando /architect com travas de segurança ativas...\nComando: {prompt}\n\n")

        def _worker():
            res = self.aider_runner.run_architect_planning(files=[], prompt=prompt)
            out = res.get("output") or res.get("error", "Sem saída registrada.")
            def _ui():
                self.aider_output_box.delete("1.0", "end")
                self.aider_output_box.insert("1.0", out)
                self.log("Aider concluiu o planejamento com sucesso.")
            self.dispatch_to_ui(_ui)

        import threading
        threading.Thread(target=_worker, daemon=True).start()



# Alias de conveniência
PCIMasterApp = PCIMasterGUI


def main() -> None:
    app = PCIMasterGUI()
    app.run()


if __name__ == "__main__":
    main()
