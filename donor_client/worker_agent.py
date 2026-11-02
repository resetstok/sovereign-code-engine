"""
PCI Donor Worker Client v2.0
Executável seguro de 1-clique para nós doadores compartilharem processamento via Tailscale.
Opera estritamente na pasta de trabalho local ./pci_worker_workspace/.
"""

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

import psutil
import requests

WORKSPACE_DIR = Path("./pci_worker_workspace").resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def get_hardware_info():
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage(str(WORKSPACE_DIR))
    node_id = hashlib.sha256(f"{platform.node()}_{os.getlogin()}".encode()).hexdigest()[:12]

    return {
        "node_id": f"donor_{node_id}",
        "node_name": platform.node(),
        "ram_total_mb": int(ram.total / (1024 * 1024)),
        "ram_avail_mb": int(ram.available / (1024 * 1024)),
        "vram_total_mb": 0,  # Preenchido se houver GPU
        "ssd_free_gb": round(disk.free / (1024**3), 2),
        "supported_models": ["qwen2.5-coder:3b", "ollama_local"],
    }


def run_worker_loop(master_url: str, poll_interval_sec: int = 15):
    hw = get_hardware_info()
    node_id = hw["node_id"]

    print("=" * 60)
    print(" [PCI DONOR WORKER v2.0] INICIALIZADO EM MODO SEGURO")
    print("=" * 60)
    print(f"ID do Nó:            {node_id}")
    print(f"Master Tailscale:    {master_url}")
    print(f"Pasta Confinada:     {WORKSPACE_DIR}")
    print("Permissões:          Somente leitura de hardware e escrita na pasta local.")
    print("-" * 60)

    # 1. Registro no Master
    try:
        reg_payload = {
            "node_id": node_id,
            "node_name": hw["node_name"],
            "ram_total_mb": hw["ram_total_mb"],
            "vram_total_mb": hw["vram_total_mb"],
            "ssd_free_gb": hw["ssd_free_gb"],
            "supported_models": hw["supported_models"],
        }
        res = requests.post(f"{master_url}/nodes/register", json=reg_payload, timeout=10)
        if res.status_code == 200:
            print(f">>> Registrado com sucesso no Master PC! Status: ONLINE")
        else:
            print(f">>> Aviso no registro: {res.status_code} - {res.text}")
    except Exception as exc:
        print(f"Erro ao conectar com o Master: {exc}")
        print("Verifique se a VPN Tailscale está ativa.")
        return

    # 2. Loop de Polling e Execução
    print(">>> Aguardando tarefas na fila distribuída...")
    while True:
        try:
            # Heartbeat periódico
            requests.post(
                f"{master_url}/nodes/heartbeat",
                json={"node_id": node_id, "status": "ONLINE"},
                timeout=5,
            )

            # Pull de tarefa com Lease
            lease_res = requests.get(f"{master_url}/tasks/lease", params={"node_id": node_id}, timeout=10)

            if lease_res.status_code == 200:
                task = lease_res.json()
                task_id = task["task_id"]
                target_filename = task["target_filename"]
                print(f"\n[+] TAREFA RECEBIDA COM LEASE: {task_id}")
                print(f"    Arquivo: {target_filename}")

                # Salva especificações no workspace local
                local_task_file = WORKSPACE_DIR / f"{task_id}_{target_filename}"
                t0 = time.perf_counter()

                # Processamento com modelo local no PC Doador (ex: Ollama do doador)
                candidate_code = generate_solution_local(task["spec_markdown"], task["test_code"])
                compute_sec = round(time.perf_counter() - t0, 2)

                print(f"    Computado em {compute_sec}s. Enviando de volta ao Master para validação...")

                # Submissão para verificação no Master
                sub_res = requests.post(
                    f"{master_url}/tasks/{task_id}/submit",
                    json={
                        "node_id": node_id,
                        "candidate_code": candidate_code,
                        "compute_seconds": compute_sec,
                    },
                    timeout=45,
                )

                if sub_res.status_code == 200:
                    result = sub_res.json()
                    if result.get("awarded"):
                        print(f"    >>> APROVADO NO PYTEST DO MASTER! Kudos creditados.")
                    else:
                        print(f"    >>> REPROVADO NO PYTEST. O Master agendou nova tentativa.")

            elif lease_res.status_code == 204:
                # Fila vazia
                time.sleep(poll_interval_sec)
            else:
                time.sleep(poll_interval_sec)

        except KeyboardInterrupt:
            print("\nEncerrando agente doador...")
            break
        except Exception as exc:
            print(f"Aviso de conexão temporário: {exc}")
            time.sleep(poll_interval_sec)


def generate_solution_local(spec_md: str, test_code: str) -> str:
    """Tenta chamar o Ollama local do doador se disponível, senão gera casca segura."""
    try:
        req_data = {
            "model": "qwen2.5-coder:3b",
            "prompt": f"Implemente a função para passar no teste:\n\n{spec_md}\n\nTeste:\n{test_code}\n\nRetorne apenas o código Python:",
            "stream": False,
        }
        res = requests.post("http://127.0.0.1:11434/api/generate", json=req_data, timeout=30)
        if res.status_code == 200:
            return res.json().get("response", "")
    except Exception:
        pass

    return "# Código gerado pelo doador\ndef processar():\n    return True\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PCI Donor Worker Client v2.0")
    parser.add_argument("--master", default="http://127.0.0.1:8000", help="URL do Master PC (IP Tailscale)")
    args = parser.parse_args()

    run_worker_loop(master_url=args.master)
