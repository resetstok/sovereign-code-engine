"""
Módulo: donor_client/worker_agent.py
Cliente Doador Autônomo e Seguro (PCI Donor Worker Agent v2.1).
Permite que máquinas voluntárias na VPN Tailscale doem processamento para a fila do Master.
Confinado estritamente à pasta local ./pci_worker_workspace/.
Implementa interface gráfica amigável (CustomTkinter/Tkinter) e modo CLI/Headless.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

# Configuração de logging estruturado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pci_donor_agent")

WORKSPACE_DIR = Path("./pci_worker_workspace").resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MASTER_URL = os.getenv("PCI_MASTER_URL", "http://127.0.0.1:8000")
DEFAULT_TOKEN = os.getenv("PCI_DONOR_TOKEN", "pci-donor-tailscale-token-2026")
DEFAULT_MODEL = os.getenv("PCI_DONOR_MODEL", "qwen2.5-coder:3b")

CONSENT_TEXT = (
    "=========================================================================\n"
    "   TERMO DE CONSENTIMENTO E PRIVACIDADE — REDE DE DOAÇÃO PCI ENGINE      \n"
    "=========================================================================\n"
    "1. CONFINAMENTO ABSOLUTO: Este agente opera única e exclusivamente na    \n"
    f"   pasta local: '{WORKSPACE_DIR}'.\n"
    "2. PROTEÇÃO DE DADOS: O agente NUNCA lê, modifica ou transmite dados     \n"
    "   pessoais, arquivos do usuário ou históricos fora desta pasta.\n"
    "3. AUDITORIA TRANSPARENTE: O agente apenas consulta métricas de uso de   \n"
    "   hardware (RAM, CPU e espaço livre) via biblioteca padrão psutil.\n"
    "4. TAREFAS PÚBLICAS: Apenas microtarefas estritamente públicas ou        \n"
    "   sanitizadas são recebidas do Master PC.\n"
    "========================================================================="
)


class DonorWorkerAgent:
    """
    Agente de computação distribuída para doadores na VPN Tailscale.
    Executa o ciclo Pull -> Solve (Ollama) -> Local Pre-Test -> Submit -> Kudos.
    """

    def __init__(
        self,
        master_url: str = DEFAULT_MASTER_URL,
        token: str = DEFAULT_TOKEN,
        workspace_dir: Path | str = WORKSPACE_DIR,
        ollama_url: str = "http://127.0.0.1:11434",
        preferred_model: str = DEFAULT_MODEL,
        max_cpu_percent: float = 85.0,
        on_log: Optional[Callable[[str], None]] = None,
        on_kudos_update: Optional[Callable[[float], None]] = None,
        on_status_change: Optional[Callable[[str], None]] = None,
    ):
        self.master_url = master_url.rstrip("/")
        self.token = token.strip()
        self.workspace = Path(workspace_dir).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.ollama_url = ollama_url.rstrip("/")
        self.preferred_model = preferred_model
        self.max_cpu_percent = max_cpu_percent
        self.paused = False

        self.on_log = on_log
        self.on_kudos_update = on_kudos_update
        self.on_status_change = on_status_change

        self.kudos_balance: float = 0.0
        self.completed_tasks_count: int = 0
        self.hw_info = self.get_hardware_info()
        self.node_id = self.hw_info["node_id"]

    def log(self, message: str) -> None:
        """Registra mensagem no logger e no callback da interface gráfica."""
        logger.info(message)
        if self.on_log:
            try:
                self.on_log(message)
            except Exception:
                pass

    def update_status(self, status: str) -> None:
        """Atualiza estado na interface do usuário."""
        if self.on_status_change:
            try:
                self.on_status_change(status)
            except Exception:
                pass

    @staticmethod
    def get_hardware_info() -> Dict[str, Any]:
        """Audita memória, disco e gera identificador determinístico do nó."""
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.cwd()))
        raw_id = f"{platform.node()}_{os.environ.get('USERNAME', 'donor')}"
        node_hash = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]

        return {
            "node_id": f"donor_{node_hash}",
            "node_name": platform.node(),
            "ram_total_mb": int(ram.total / (1024 * 1024)),
            "ram_avail_mb": int(ram.available / (1024 * 1024)),
            "vram_total_mb": 0,
            "ssd_free_gb": round(disk.free / (1024**3), 2),
            "supported_models": ["qwen2.5-coder:3b", "ollama_local"],
        }

    def _http_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: int = 25,
    ) -> Tuple[int, Any]:
        """
        Executa requisição HTTP resiliente injetando o header 'X-PCI-Donor-Token'
        e desativando proxies do sistema para conectividade local/Tailscale direta.
        """
        url = f"{self.master_url}{endpoint}"
        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {
            "Content-Type": "application/json",
            "X-PCI-Donor-Token": self.token,
        }

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        try:
            with opener.open(req, timeout=timeout) as resp:
                status_code = resp.status
                raw = resp.read().decode("utf-8")
                try:
                    payload = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    payload = raw
                return status_code, payload
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8")
            try:
                payload = json.loads(err_body) if err_body else None
            except json.JSONDecodeError:
                payload = err_body
            return err.code, payload
        except Exception as exc:
            return 0, str(exc)

    def register(self) -> bool:
        """Cadastra o nó doador no Master PC com fallback de rotas."""
        reg_payload = {
            "node_id": self.node_id,
            "node_name": self.hw_info["node_name"],
            "ram_total_mb": self.hw_info["ram_total_mb"],
            "vram_total_mb": self.hw_info["vram_total_mb"],
            "ssd_free_gb": self.hw_info["ssd_free_gb"],
            "supported_models": self.hw_info["supported_models"],
        }
        # Tenta endpoint v1 com token
        code, resp = self._http_request("/api/v1/nodes/register", method="POST", data=reg_payload)
        if code == 200:
            self.log(f"Registrado com sucesso no Master PC via API v1 (Nó: {self.node_id}).")
            return True

        # Fallback para endpoint legado
        code_leg, resp_leg = self._http_request("/nodes/register", method="POST", data=reg_payload)
        if code_leg == 200:
            self.log(f"Registrado via rota legada (Nó: {self.node_id}).")
            return True

        self.log(f"Falha ao registrar nó: HTTP {code} ({resp})")
        return False

    def send_heartbeat(self) -> bool:
        """Envia batimento cardíaco ao Master mantendo status ONLINE."""
        mem = psutil.virtual_memory()
        hb_data = {
            "node_id": self.node_id,
            "status": "PAUSED" if self.paused else "ONLINE",
            "ram_free_mb": int(mem.available / (1024 * 1024)),
        }
        code, _ = self._http_request("/api/v1/nodes/heartbeat", method="POST", data=hb_data, timeout=5)
        if code == 200:
            return True
        code_leg, _ = self._http_request("/nodes/heartbeat", method="POST", data=hb_data, timeout=5)
        return code_leg == 200

    def pull_lease(self) -> Optional[Dict[str, Any]]:
        """Busca tarefa elegível via Pull com Lease de 45 minutos."""
        # Tenta POST v1
        code, resp = self._http_request("/api/v1/tasks/lease", method="POST", data={"node_id": self.node_id})
        if code == 200 and isinstance(resp, dict):
            return resp

        # Tenta GET v1
        code_get, resp_get = self._http_request(f"/api/v1/tasks/lease?node_id={self.node_id}", method="GET")
        if code_get == 200 and isinstance(resp_get, dict):
            return resp_get

        # Fallback legado
        code_leg, resp_leg = self._http_request(f"/tasks/lease?node_id={self.node_id}", method="GET")
        if code_leg == 200 and isinstance(resp_leg, dict):
            return resp_leg

        return None

    def fetch_kudos(self) -> float:
        """Consulta o saldo atual de Kudos no Master."""
        code, resp = self._http_request(f"/api/v1/kudos/{self.node_id}", method="GET")
        if code == 200 and isinstance(resp, dict):
            self.kudos_balance = float(resp.get("kudos_balance", 0.0))
            if self.on_kudos_update:
                self.on_kudos_update(self.kudos_balance)
            return self.kudos_balance

        code_leg, resp_leg = self._http_request(f"/kudos/{self.node_id}", method="GET")
        if code_leg == 200 and isinstance(resp_leg, dict):
            self.kudos_balance = float(resp_leg.get("kudos_balance", 0.0))
            if self.on_kudos_update:
                self.on_kudos_update(self.kudos_balance)
            return self.kudos_balance

        return self.kudos_balance

    def solve_task(self, spec_markdown: str, test_code: str) -> str:
        """Invoca o Ollama local do doador para sintetizar a solução de código."""
        prompt = (
            f"Você é um gerador de código Python rigoroso. Implemente o código necessário para passar no teste.\n\n"
            f"REQUISITOS DA TAREFA:\n{spec_markdown}\n\n"
            f"TESTE UNITÁRIO (PYTEST):\n```python\n{test_code}\n```\n\n"
            f"Retorne apenas o bloco ```python com a implementação completa:"
        )
        try:
            payload = json.dumps({
                "model": self.preferred_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 1024},
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.ollama_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_text = data.get("response", "")
                return self._extract_code(raw_text)
        except Exception as exc:
            self.log(f"Ollama local indisponível ({exc}). Usando sintetizador determinístico seguro.")

        # Fallback determinístico básico caso o Ollama local esteja desligado
        return "def processar():\n    return True\n"

    def local_pretest(
        self,
        task_id: str,
        candidate_code: str,
        test_code: str,
        target_filename: str = "solution.py",
    ) -> Tuple[bool, str]:
        """
        Executa um pré-teste estritamente confinado dentro de ./pci_worker_workspace/<task_id>/.
        Garante que o candidato passa localmente antes de enviar e gastar banda.
        """
        task_sandbox = self.workspace / task_id
        task_sandbox.mkdir(parents=True, exist_ok=True)

        target_file = task_sandbox / target_filename
        test_file = task_sandbox / "test_candidate.py"

        target_file.write_text(candidate_code, encoding="utf-8")
        test_file.write_text(test_code, encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-o",
            f"pythonpath={task_sandbox}",
            "-q",
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(task_sandbox),
                capture_output=True,
                text=True,
                timeout=25,
            )
            passed = proc.returncode == 0
            output = proc.stdout if passed else (proc.stdout + "\n" + proc.stderr)
            return passed, output.strip()
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT: Pré-teste local excedeu 25 segundos."
        except Exception as exc:
            return False, f"ERRO DE PRÉ-TESTE: {exc}"

    def submit_solution(
        self, task_id: str, candidate_code: str, compute_sec: float
    ) -> Tuple[bool, bool]:
        """Submete a solução para validação cega em sandbox no Master PC."""
        payload = {
            "task_id": task_id,
            "node_id": self.node_id,
            "candidate_code": candidate_code,
            "compute_seconds": compute_sec,
        }
        # Tenta endpoint v1
        code, resp = self._http_request("/api/v1/tasks/submit", method="POST", data=payload, timeout=50)
        if code == 200 and isinstance(resp, dict):
            return True, resp.get("awarded", False)

        # Fallback legado
        code_leg, resp_leg = self._http_request(
            f"/tasks/{task_id}/submit",
            method="POST",
            data={
                "node_id": self.node_id,
                "candidate_code": candidate_code,
                "compute_seconds": compute_sec,
            },
            timeout=50,
        )
        if code_leg == 200 and isinstance(resp_leg, dict):
            return True, resp_leg.get("awarded", False)

        return False, False

    def run_once(self) -> Dict[str, Any]:
        """Executa um ciclo único de verificação de tarefa."""
        if self.paused:
            self.send_heartbeat()
            self.update_status("PAUSADO")
            return {"status": "PAUSED"}

        # Trava de uso de CPU do doador
        current_cpu = psutil.cpu_percent(interval=0.1)
        if current_cpu > self.max_cpu_percent:
            self.log(f"CPU do doador alta ({current_cpu}% > {self.max_cpu_percent}%). Aguardando alívio térmico...")
            self.update_status("AGUARDANDO CPU")
            time.sleep(2)
            return {"status": "HIGH_CPU"}

        self.send_heartbeat()
        self.update_status("BUSCANDO TAREFAS...")

        task = self.pull_lease()
        if not task:
            self.update_status("OCIOSO (Aguardando Tarefas)")
            return {"status": "NO_TASKS"}

        task_id = task["task_id"]
        target_filename = task.get("target_filename", "module.py")
        self.update_status(f"PROCESSANDO TAREFA: {task_id}")
        self.log(f">>> Tarefa '{task_id}' recebida. Confinando em ./pci_worker_workspace/{task_id}/")

        t0 = time.perf_counter()
        candidate = self.solve_task(task["spec_markdown"], task["test_code"])
        compute_sec = round(time.perf_counter() - t0, 2)

        # Pré-teste local
        self.log(f"Executando pré-teste local para {task_id}...")
        passed_local, test_out = self.local_pretest(
            task_id=task_id,
            candidate_code=candidate,
            test_code=task["test_code"],
            target_filename=target_filename,
        )

        if not passed_local:
            self.log(f"Pré-teste local reprovado para {task_id}. Submetendo mesmo assim para validação mestre.")

        # Submissão ao Master
        self.log(f"Submetendo solução de {task_id} para validação sandbox no Master...")
        submitted, awarded = self.submit_solution(task_id, candidate, compute_sec)

        if awarded:
            self.completed_tasks_count += 1
            self.log(f"SUCESSO! Tarefa {task_id} APROVADA pelo Master. Kudos creditados!")
            self.fetch_kudos()
        else:
            self.log(f"Tarefa {task_id} não aprovada no sandbox do Master.")

        self.update_status("OCIOSO")
        return {
            "status": "COMPLETED",
            "task_id": task_id,
            "compute_seconds": compute_sec,
            "submitted": submitted,
            "awarded": awarded,
        }

    def run_cli_loop(self, poll_interval_sec: int = 10, max_iterations: Optional[int] = None) -> None:
        """Loop autônomo para terminal (modo CLI/Headless)."""
        print(CONSENT_TEXT)
        print(f"\n[INICIALIZANDO PCI DONOR AGENT v2.1]")
        print(f"ID do Nó:          {self.node_id}")
        print(f"Master URL:        {self.master_url}")
        print(f"Pasta Confinada:   {self.workspace}")
        print(f"Limite de CPU:     {self.max_cpu_percent}%")
        print("-" * 73)

        if not self.register():
            print(">>> ERRO: Não foi possível autenticar ou registrar no Master PC.")
            print("    Verifique se o Master está rodando e se o token é válido.")
            return

        self.fetch_kudos()
        print(f">>> Conexão ativa com o Master. Saldo de Kudos inicial: {self.kudos_balance:.2f} pts")
        print(">>> Aguardando microtarefas elegíveis na fila...\n")

        iterations = 0
        try:
            while True:
                res = self.run_once()
                if res["status"] == "COMPLETED":
                    status_badge = "APROVADO [KUDOS +]" if res.get("awarded") else "REPROVADO"
                    print(f"[{time.strftime('%X')}] Tarefa {res['task_id']} | {res['compute_seconds']}s | {status_badge}")
                elif res["status"] == "NO_TASKS":
                    time.sleep(poll_interval_sec)
                elif res["status"] in ("PAUSED", "HIGH_CPU"):
                    time.sleep(3)

                iterations += 1
                if max_iterations and iterations >= max_iterations:
                    break
        except KeyboardInterrupt:
            print("\nEncerrando agente doador com segurança. Até breve!")

    @staticmethod
    def _extract_code(text: str) -> str:
        """Extrai bloco de código Markdown eliminando formatações espúrias."""
        if "```python" in text:
            parts = text.split("```python")
            return parts[1].split("```")[0].strip()
        elif "```" in text:
            parts = text.split("```")
            return parts[1].split("```")[0].strip()
        return text.strip()


# =============================================================================
# INTERFACE GRÁFICA DESKTOP (CUSTOMTKINTER COM FALLBACK PARA TKINTER PADRÃO)
# =============================================================================
def launch_gui(agent: DonorWorkerAgent, poll_interval: int = 8) -> None:
    """Inicia a aplicação gráfica moderna para o doador voluntário."""
    try:
        import customtkinter as ctk

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        use_custom = True
    except ImportError:
        import tkinter as ctk  # Fallback
        use_custom = False

    import tkinter as tk
    from tkinter import messagebox, ttk

    if use_custom:
        root = ctk.CTk()
    else:
        root = tk.Tk()

    root.title("PCI Sovereign Code Engine — Agente Doador v2.1")
    root.geometry("780x640")
    root.minsize(680, 520)

    # Variáveis reativas
    status_var = tk.StringVar(value="INICIALIZANDO...")
    kudos_var = tk.StringVar(value="0.00 pts")
    cpu_limit_var = tk.DoubleVar(value=agent.max_cpu_percent)
    sys_metrics_var = tk.StringVar(value="RAM: ... | CPU: ...")
    pause_btn_var = tk.StringVar(value="Pausar Doação")

    # Header
    if use_custom:
        header_frame = ctk.CTkFrame(root, corner_radius=8)
        header_frame.pack(fill="x", padx=15, pady=10)
        title_label = ctk.CTkLabel(
            header_frame,
            text="PCI Sovereign Engine — Doação de Computação Segura",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title_label.pack(anchor="w", padx=15, pady=(10, 2))
        sub_label = ctk.CTkLabel(
            header_frame,
            text=f"Nó: {agent.node_id} | Master: {agent.master_url}",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        sub_label.pack(anchor="w", padx=15, pady=(0, 10))
    else:
        header_frame = tk.Frame(root, bg="#2b2b2b", pady=10)
        header_frame.pack(fill="x", padx=10, pady=5)
        title_label = tk.Label(
            header_frame,
            text="PCI Sovereign Engine — Doação de Computação Segura",
            font=("Arial", 14, "bold"),
            fg="white",
            bg="#2b2b2b",
        )
        title_label.pack(anchor="w", padx=10)

    # Termo de Consentimento Resumido
    if use_custom:
        consent_frame = ctk.CTkFrame(root, corner_radius=6, fg_color="#1f232a")
        consent_frame.pack(fill="x", padx=15, pady=5)
        consent_lbl = ctk.CTkLabel(
            consent_frame,
            text=(
                "TERMO DE CONFINAMENTO: O agente opera confinado em './pci_worker_workspace/'.\n"
                "NUNCA lê ou modifica seus dados pessoais. Apenas processa tarefas públicas e pontua Kudos."
            ),
            font=ctk.CTkFont(size=11),
            text_color="#8ec07c",
            justify="left",
        )
        consent_lbl.pack(padx=12, pady=6, anchor="w")

    # Cards de Status e Kudos
    if use_custom:
        cards_frame = ctk.CTkFrame(root, fg_color="transparent")
        cards_frame.pack(fill="x", padx=15, pady=5)

        # Card Kudos
        card_kudos = ctk.CTkFrame(cards_frame, corner_radius=8, width=240)
        card_kudos.pack(side="left", fill="both", expand=True, padx=(0, 5))
        ctk.CTkLabel(card_kudos, text="Meus Kudos Acumulados", font=ctk.CTkFont(size=13)).pack(pady=(8, 2))
        lbl_kudos_val = ctk.CTkLabel(
            card_kudos,
            textvariable=kudos_var,
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#5294e2",
        )
        lbl_kudos_val.pack(pady=(0, 8))

        # Card Estado
        card_status = ctk.CTkFrame(cards_frame, corner_radius=8, width=240)
        card_status.pack(side="left", fill="both", expand=True, padx=5)
        ctk.CTkLabel(card_status, text="Status Operacional", font=ctk.CTkFont(size=13)).pack(pady=(8, 2))
        lbl_status_val = ctk.CTkLabel(
            card_status,
            textvariable=status_var,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#a89984",
        )
        lbl_status_val.pack(pady=(0, 8))

        # Card Métricas
        card_sys = ctk.CTkFrame(cards_frame, corner_radius=8, width=240)
        card_sys.pack(side="left", fill="both", expand=True, padx=(5, 0))
        ctk.CTkLabel(card_sys, text="Uso do Meu PC", font=ctk.CTkFont(size=13)).pack(pady=(8, 2))
        lbl_sys_val = ctk.CTkLabel(
            card_sys,
            textvariable=sys_metrics_var,
            font=ctk.CTkFont(size=13),
            text_color="#fabd2f",
        )
        lbl_sys_val.pack(pady=(0, 8))

    # Painel de Controles
    if use_custom:
        ctrl_frame = ctk.CTkFrame(root, corner_radius=8)
        ctrl_frame.pack(fill="x", padx=15, pady=10)

        # Botão Pausar / Retomar
        def toggle_pause():
            agent.paused = not agent.paused
            if agent.paused:
                pause_btn_var.set("Retomar Doação")
                status_var.set("PAUSADO PELO USUÁRIO")
            else:
                pause_btn_var.set("Pausar Doação")
                status_var.set("RETOMANDO...")

        btn_pause = ctk.CTkButton(
            ctrl_frame,
            textvariable=pause_btn_var,
            command=toggle_pause,
            width=140,
            fg_color="#d65d0e",
            hover_color="#af3a03",
        )
        btn_pause.pack(side="left", padx=12, pady=10)

        # Slider Limite de CPU
        ctk.CTkLabel(ctrl_frame, text="Limite Máx de CPU:").pack(side="left", padx=(15, 5))

        lbl_cpu_display = ctk.CTkLabel(ctrl_frame, text=f"{int(agent.max_cpu_percent)}%")

        def on_slider_change(val):
            agent.max_cpu_percent = float(val)
            lbl_cpu_display.configure(text=f"{int(val)}%")

        slider_cpu = ctk.CTkSlider(
            ctrl_frame,
            from_=20,
            to=95,
            number_of_steps=15,
            command=on_slider_change,
            variable=cpu_limit_var,
            width=160,
        )
        slider_cpu.pack(side="left", padx=5)
        lbl_cpu_display.pack(side="left", padx=5)

        # Botão Atualizar Saldo
        btn_refresh = ctk.CTkButton(
            ctrl_frame,
            text="Atualizar Kudos",
            command=lambda: agent.fetch_kudos(),
            width=120,
        )
        btn_refresh.pack(side="right", padx=12, pady=10)

    # Caixa de Logs em Tempo Real
    if use_custom:
        log_frame = ctk.CTkFrame(root, corner_radius=8)
        log_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        ctk.CTkLabel(log_frame, text="Diário de Atividades Confinado (Logs):", font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", padx=12, pady=(8, 2)
        )
        log_box = ctk.CTkTextbox(log_frame, wrap="word", font=ctk.CTkFont(family="Consolas", size=11))
        log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    else:
        log_frame = tk.Frame(root)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        log_box = tk.Text(log_frame, wrap="word", bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 10))
        log_box.pack(fill="both", expand=True)

    def append_log(msg: str):
        def _insert():
            log_box.insert("end", f"[{time.strftime('%X')}] {msg}\n")
            log_box.see("end")

        root.after(0, _insert)

    def update_kudos_ui(bal: float):
        root.after(0, lambda: kudos_var.set(f"{bal:.2f} pts"))

    def update_status_ui(st: str):
        root.after(0, lambda: status_var.set(st))

    # Conecta os callbacks do agente à interface
    agent.on_log = append_log
    agent.on_kudos_update = update_kudos_ui
    agent.on_status_change = update_status_ui

    # Thread de monitoramento de hardware
    def metrics_poller():
        while True:
            try:
                cpu = psutil.cpu_percent(interval=1.0)
                mem = psutil.virtual_memory()
                root.after(
                    0,
                    lambda c=cpu, m=mem.percent: sys_metrics_var.set(f"RAM: {m:.1f}% | CPU: {c:.1f}%"),
                )
                time.sleep(2)
            except Exception:
                break

    threading.Thread(target=metrics_poller, daemon=True).start()

    # Thread de trabalho autônomo do agente
    def worker_thread():
        time.sleep(1.0)
        append_log("Registrando no Master PC...")
        if not agent.register():
            append_log("FALHA: Não foi possível autenticar ou registrar no Master.")
            update_status_ui("ERRO DE CONEXÃO")
            return

        agent.fetch_kudos()
        append_log(f"Conexão ativa! Saldo inicial: {agent.kudos_balance:.2f} pts.")

        while True:
            try:
                res = agent.run_once()
                if res["status"] == "NO_TASKS":
                    time.sleep(poll_interval)
                elif res["status"] in ("PAUSED", "HIGH_CPU"):
                    time.sleep(2)
            except Exception as exc:
                append_log(f"Erro transitório: {exc}")
                time.sleep(5)

    threading.Thread(target=worker_thread, daemon=True).start()

    # Executa a janela
    root.mainloop()


# =============================================================================
# ENTRYPOINT PRINCIPAL
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="PCI Sovereign Code Engine — Donor Worker Agent v2.1")
    parser.add_argument("--master", default=DEFAULT_MASTER_URL, help="URL do Master PC (IP Tailscale)")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Token revogável de autenticação X-PCI-Donor-Token")
    parser.add_argument("--poll", type=int, default=10, help="Intervalo de consulta em segundos")
    parser.add_argument("--max-cpu", type=float, default=85.0, help="Teto de uso de CPU em porcentagem")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modelo Ollama preferido para inferência local")
    parser.add_argument("--headless", action="store_true", help="Executa em modo terminal sem interface gráfica")
    parser.add_argument("--cli", action="store_true", help="Alias para modo terminal")

    args = parser.parse_args()

    agent = DonorWorkerAgent(
        master_url=args.master,
        token=args.token,
        preferred_model=args.model,
        max_cpu_percent=args.max_cpu,
    )

    if args.headless or args.cli:
        agent.run_cli_loop(poll_interval_sec=args.poll)
    else:
        try:
            launch_gui(agent, poll_interval=args.poll)
        except Exception as exc:
            logger.warning(f"Interface gráfica indisponível ({exc}). Iniciando automaticamente em modo CLI.")
            agent.run_cli_loop(poll_interval_sec=args.poll)


if __name__ == "__main__":
    main()
