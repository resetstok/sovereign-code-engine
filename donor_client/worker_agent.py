"""
Módulo: donor_client/worker_agent.py
Cliente Doador Autônomo e Seguro (PCI Donor Worker Agent v2.0/v2.1).
Permite que máquinas voluntárias na VPN Tailscale doem processamento para a fila do Master.
Confinado estritamente à pasta local ./pci_worker_workspace/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger("donor_worker")

WORKSPACE_DIR = Path("./pci_worker_workspace").resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


class DonorWorkerAgent:
    """Agente de doação de computação com ciclo Pull -> Execute -> Submit."""

    def __init__(
        self,
        master_url: str = "http://127.0.0.1:8000",
        workspace_dir: Path | str = WORKSPACE_DIR,
        ollama_url: str = "http://127.0.0.1:11434",
        preferred_model: str = "qwen2.5-coder:3b",
    ):
        self.master_url = master_url.rstrip("/")
        self.workspace = Path(workspace_dir).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.ollama_url = ollama_url.rstrip("/")
        self.preferred_model = preferred_model
        self.hw_info = self.get_hardware_info()
        self.node_id = self.hw_info["node_id"]

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
        self, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None, timeout: int = 15
    ) -> Tuple[int, Any]:
        """Faz requisição HTTP direta sem depender de bibliotecas externas (resiliente a proxy)."""
        url = f"{self.master_url}{endpoint}"
        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}

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
        """Cadastra o nó doador no Master PC."""
        reg_payload = {
            "node_id": self.node_id,
            "node_name": self.hw_info["node_name"],
            "ram_total_mb": self.hw_info["ram_total_mb"],
            "vram_total_mb": self.hw_info["vram_total_mb"],
            "ssd_free_gb": self.hw_info["ssd_free_gb"],
            "supported_models": self.hw_info["supported_models"],
        }
        code, resp = self._http_request("/nodes/register", method="POST", data=reg_payload)
        return code == 200

    def send_heartbeat(self) -> bool:
        """Envia batimento cardíaco ao Master."""
        code, resp = self._http_request(
            "/nodes/heartbeat",
            method="POST",
            data={"node_id": self.node_id, "status": "ONLINE"},
            timeout=5,
        )
        return code == 200

    def pull_lease(self) -> Optional[Dict[str, Any]]:
        """Busca uma tarefa elegível com lease garantido de 45 minutos."""
        code, resp = self._http_request(f"/tasks/lease?node_id={self.node_id}", method="GET")
        if code == 200 and isinstance(resp, dict):
            return resp
        return None

    def solve_task(self, spec_markdown: str, test_code: str) -> str:
        """Invoca o Ollama local do doador para computar a solução de código."""
        prompt = (
            f"Você é um gerador de código Python. Implemente o código necessário para passar no teste.\n\n"
            f"REQUISITOS:\n{spec_markdown}\n\n"
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
            with opener.open(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_text = data.get("response", "")
                return self._extract_code(raw_text)
        except Exception as exc:
            logger.warning(f"Ollama local indisponível no doador ({exc}). Gerando stub seguro.")

        return "# Código padrão gerado por doador\ndef processar():\n    return True\n"

    def submit_solution(self, task_id: str, candidate_code: str, compute_sec: float) -> Tuple[bool, bool]:
        """Submete a solução para validação no sandbox Git Worktree do Master."""
        payload = {
            "node_id": self.node_id,
            "candidate_code": candidate_code,
            "compute_seconds": compute_sec,
        }
        code, resp = self._http_request(f"/tasks/{task_id}/submit", method="POST", data=payload, timeout=45)
        if code == 200 and isinstance(resp, dict):
            return True, resp.get("awarded", False)
        return False, False

    def run_once(self) -> Dict[str, Any]:
        """Executa um ciclo único de verificação de tarefa."""
        self.send_heartbeat()
        task = self.pull_lease()
        if not task:
            return {"status": "NO_TASKS"}

        task_id = task["task_id"]
        t0 = time.perf_counter()
        candidate = self.solve_task(task["spec_markdown"], task["test_code"])
        compute_sec = round(time.perf_counter() - t0, 2)

        submitted, awarded = self.submit_solution(task_id, candidate, compute_sec)
        return {
            "status": "COMPLETED",
            "task_id": task_id,
            "compute_seconds": compute_sec,
            "submitted": submitted,
            "awarded": awarded,
        }

    def run_loop(self, poll_interval_sec: int = 15, max_iterations: Optional[int] = None):
        """Loop principal de trabalho do nó doador."""
        print("=" * 65)
        print(" [PCI DONOR WORKER v2.0] AGENTE DE DOAÇÃO CONFINADO")
        print("=" * 65)
        print(f"ID do Nó:            {self.node_id}")
        print(f"Master Tailscale:    {self.master_url}")
        print(f"Pasta Confinada:     {self.workspace}")
        print("-" * 65)

        if not self.register():
            print(">>> Erro ao conectar/registrar no Master PC.")
            return

        print(">>> Registrado com sucesso no Master PC! Status: ONLINE")
        print(">>> Aguardando tarefas elegíveis na fila...")

        iterations = 0
        while True:
            try:
                res = self.run_once()
                if res["status"] == "COMPLETED":
                    status_text = "APROVADO (Kudos concedidos)" if res.get("awarded") else "REPROVADO no Pytest"
                    print(f">>> Tarefa {res['task_id']} concluída em {res['compute_seconds']}s -> {status_text}")
                else:
                    time.sleep(poll_interval_sec)

                iterations += 1
                if max_iterations and iterations >= max_iterations:
                    break
            except KeyboardInterrupt:
                print("\nEncerrando agente doador com segurança.")
                break
            except Exception as exc:
                logger.warning(f"Erro transitório no loop do doador: {exc}")
                time.sleep(poll_interval_sec)

    @staticmethod
    def _extract_code(text: str) -> str:
        if "```python" in text:
            parts = text.split("```python")
            return parts[1].split("```")[0].strip()
        elif "```" in text:
            parts = text.split("```")
            return parts[1].split("```")[0].strip()
        return text.strip()


def main():
    parser = argparse.ArgumentParser(description="PCI Donor Worker Client v2.0")
    parser.add_argument("--master", default="http://127.0.0.1:8000", help="URL do Master PC (IP Tailscale)")
    parser.add_argument("--poll", type=int, default=15, help="Intervalo de consulta em segundos")
    args = parser.parse_args()

    agent = DonorWorkerAgent(master_url=args.master)
    agent.run_loop(poll_interval_sec=args.poll)


if __name__ == "__main__":
    main()
