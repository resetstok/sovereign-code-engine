"""
Módulo: code_engine/cli.py
Ponto de entrada unificado da linha de comando para o Sovereign Code Engine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from code_engine.orchestrator import CodeEngineOrchestrator
from code_engine.ram_liberator import RAMLiberator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def check_ram(interactive: bool = False):
    print("=" * 65)
    print(" [1] HIGIENE E MONITORAMENTO DE MEMÓRIA RAM")
    print("=" * 65)
    ram = RAMLiberator.get_system_ram_mb()
    print(f"RAM Total:      {ram['total_mb']:.1f} MB (~{ram['total_mb']/1024:.2f} GB)")
    print(f"RAM Disponível: {ram['available_mb']:.1f} MB (~{ram['available_mb']/1024:.2f} GB)")
    print(f"Uso de Memória: {ram['percent_used']}%")

    candidates, potential_mb = RAMLiberator.scan_releasable_processes()
    print(f"\nProcessos em segundo plano candidatos à liberação: {len(candidates)}")
    print(f"Potencial de RAM a liberar: {potential_mb:.1f} MB (~{potential_mb/1024:.2f} GB)")

    if candidates:
        print("\nTop candidatos:")
        for c in candidates[:8]:
            print(f" - {c.name:<18} ({c.count:2d} processos) | RSS: {c.total_rss_mb:6.1f} MB | {c.description}")

    if interactive and candidates:
        confirm = input("\nDeseja solicitar o fechamento dos processos ociosos para liberar RAM? (s/N): ").strip().lower()
        if confirm == 's':
            released_mb, terminated = RAMLiberator.terminate_candidates(candidates)
            print(f"\n>>> Sucesso: {len(terminated)} processos encerrados. {released_mb:.1f} MB de RAM liberados!")


def check_gpu():
    print("\n" + "=" * 65)
    print(" [2] STATUS DA GPU (NVIDIA GTX 1650 4GB VRAM)")
    print("=" * 65)
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            name, total, used, free, util = [x.strip() for x in res.stdout.strip().split(",")]
            print(f"Placa:          {name}")
            print(f"VRAM Total:     {total} MB")
            print(f"VRAM Usada:     {used} MB (Modelo local ativo)")
            print(f"VRAM Livre:     {free} MB")
            print(f"Uso da GPU:     {util}%")
            if int(used) > 3800:
                print(">>> ALERTA: VRAM perto do teto! Mantenha modelos <= 3B parâmetros.")
            else:
                print(">>> STATUS IDEAL: Modelo cabe 100% na VRAM sem transbordar para a RAM do sistema.")
    except Exception as e:
        print(f"Não foi possível consultar nvidia-smi: {e}")


def check_ollama():
    print("\n" + "=" * 65)
    print(" [3] SERVIDOR LOCAL DE INFERÊNCIA (OLLAMA)")
    print("=" * 65)
    import urllib.request
    try:
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with opener.open(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            print("Status do Servidor: ATIVO e respondendo em http://127.0.0.1:11434")
            print(f"Modelos Locais Instalados ({len(models)}):")
            for m in models:
                rec = " [RECOMENDADO PARA GTX 1650]" if any(k in m for k in ["3b", "1.5b", "2b"]) else " [PERIGO: >4GB VRAM]"
                print(f" - {m:<38} {rec}")
    except Exception as e:
        print(f"Ollama offline ou não respondendo na porta 11434 ({e}).")


def check_git():
    print("\n" + "=" * 65)
    print(" [4] CONTROLE DE VERSÃO (GIT & GITHUB)")
    print("=" * 65)
    try:
        res = subprocess.run(["git", "status", "-s"], cwd=str(REPO_ROOT), capture_output=True, text=True)
        branch_res = subprocess.run(["git", "branch", "--show-current"], cwd=str(REPO_ROOT), capture_output=True, text=True)
        remote_res = subprocess.run(["git", "remote", "-v"], cwd=str(REPO_ROOT), capture_output=True, text=True)

        branch = branch_res.stdout.strip() or "main (inicial)"
        print(f"Branch atual:  {branch}")
        print(f"Remotes:       {remote_res.stdout.strip() or 'Nenhum remote configurado ainda'}")
        dirty = len(res.stdout.strip().splitlines())
        print(f"Modificações:  {dirty} arquivos pendentes")
    except Exception as e:
        print(f"Git não detectado: {e}")


def run_next_job():
    print("\n" + "=" * 65)
    print(" [5] EXECUÇÃO DA FILA DE PROGRAMAÇÃO (JOBS.DB - CONCORRÊNCIA N=1)")
    print("=" * 65)
    db_path = REPO_ROOT / "data" / "jobs.db"
    orchestrator = CodeEngineOrchestrator(db_path=db_path, repo_root=REPO_ROOT)
    processed = orchestrator.recover_and_run_once()
    if processed > 0:
        print(f">>> Sucesso: {processed} tarefa(s) processada(s) e auditada(s) com commits atômicos!")
    else:
        print(">>> Fila limpa. Nenhuma tarefa pendente.")


def launch_aider(target_files: list[str]):
    print("\n" + "=" * 65)
    print(" [6] DISPARANDO AIDER EM MODO TDD CONTIDO")
    print("=" * 65)
    cmd = [
        "aider",
        "--config", str(REPO_ROOT / ".aider.conf.yml"),
    ] + target_files

    print(f"Comando: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(REPO_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Sovereign Code Engine - Gestão de Desenvolvimento Local")
    parser.add_argument("--status", action="store_true", help="Audita status de RAM, VRAM, Ollama e Git")
    parser.add_argument("--clean-ram", action="store_true", help="Libera RAM fechando navegadores ociosos")
    parser.add_argument("--run-next", action="store_true", help="Executa o próximo job da fila no jobs.db")
    parser.add_argument("--aider", nargs="+", metavar="ARQUIVO", help="Inicia sessão Aider confinada com os arquivos indicados")
    args = parser.parse_args()

    if args.clean_ram:
        check_ram(interactive=True)
    elif args.run_next:
        run_next_job()
    elif args.aider:
        launch_aider(args.aider)
    else:
        check_ram(interactive=False)
        check_gpu()
        check_ollama()
        check_git()
        print("\nComandos disponíveis:")
        print("  python -m code_engine.cli --status       (Audita o ambiente completo)")
        print("  python -m code_engine.cli --clean-ram    (Libera RAM antes de codar)")
        print("  python -m code_engine.cli --run-next     (Executa a próxima micro-tarefa da fila)")
        print("  python -m code_engine.cli --aider <arq1> <arq2> (Abre o Aider com TDD automático)")


if __name__ == "__main__":
    main()
