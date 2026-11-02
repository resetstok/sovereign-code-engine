"""
Módulo: pci_engine/cli.py
Entrada unificada de linha de comando da versão 2.0 do Sovereign Code Engine.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from pci_engine.db import JobsDatabaseV2
from pci_engine.orchestrator import MasterbookOrchestrator
from pci_engine.preflight import PreflightChecker
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.router import PCIRouterAndBroker
from pci_engine.nightly_colibri import NightlyColibriWorker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def check_status():
    print("=" * 65)
    print(" [PCI MASTER v2.0] STATUS E BARREIRAS FÍSICAS DE HARDWARE")
    print("=" * 65)
    health = PreflightChecker.check_health(REPO_ROOT)
    print(f"SSD Livre:        {health['ssd_free_gb']:.1f} GB (Mínimo seguro: 40.0 GB)")
    print(f"RAM Livre:        {health['ram_free_mb']:.1f} MB / Total: {health['ram_total_mb']:.1f} MB")
    print(f"Local 3B Apto?    {'SIM' if health['can_run_local_3b'] else 'NÃO (RAM < 1.800 MB)'}")

    gpu = health["gpu"]
    if gpu["available"]:
        print(f"GPU:              {gpu['name']}")
        print(f"VRAM Total/Usada: {gpu['vram_total_mb']:.0f} MB / {gpu['vram_used_mb']:.0f} MB (Livre: {gpu['vram_free_mb']:.0f} MB)")
        print(f"Status VRAM:      {'SEGURO' if gpu['is_safe'] else 'ALERTA: Perto do teto de 4GB'}")
    else:
        print("GPU:              Não detectada via nvidia-smi")

    # Contagem de Tarefas no Banco
    db = JobsDatabaseV2(REPO_ROOT / "data" / "jobs.db")
    with db.get_connection() as conn:
        counts = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks_queue GROUP BY status"
        ).fetchall()
        nodes_online = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE status = 'ONLINE' AND role = 'DONOR'"
        ).fetchone()[0]

    print("\n" + "=" * 65)
    print(" [BROKER SQLITE v2.0] FILA DE TAREFAS E NÓS DOADORES")
    print("=" * 65)
    print(f"Nós Doadores Ativos (Tailscale): {nodes_online}")
    if counts:
        for r in counts:
            print(f" - {r['status']:<22}: {r['cnt']} tarefa(s)")
    else:
        print(" - Fila vazia no momento.")


def clean_ram():
    print("=" * 65)
    print(" [HIGIENE DE PROCESSOS] LIBERAÇÃO DE RAM")
    print("=" * 65)
    candidates, potential_mb = RAMLiberator.scan_releasable_processes()
    print(f"Processos candidatos encontrados: {len(candidates)}")
    print(f"Potencial de liberação: {potential_mb:.1f} MB (~{potential_mb/1024:.2f} GB)")

    if candidates:
        for c in candidates[:6]:
            print(f" - {c.name:<18} ({c.count:2d} procs) | {c.total_rss_mb:6.1f} MB | {c.description}")
        confirm = input("\nDeseja encerrar os processos ociosos para liberar RAM? (s/N): ").strip().lower()
        if confirm == 's':
            rel, term = RAMLiberator.terminate_candidates(candidates)
            print(f"\n>>> Sucesso: {len(term)} processos terminados. {rel:.1f} MB liberados!")


def start_broker(host: str = "0.0.0.0", port: int = 8000):
    print("=" * 65)
    print(f" [BROKER API v2.0] INICIANDO SERVIDOR FASTAPI EM {host}:{port}")
    print("=" * 65)
    import uvicorn
    uvicorn.run("pci_engine.broker_api:app", host=host, port=port, reload=False)


def run_routing_cycle():
    print("=" * 65)
    print(" [ROTEADOR COGNITIVO v2.0] DESPACHO DETERMINÍSTICO")
    print("=" * 65)
    orchestrator = MasterbookOrchestrator(REPO_ROOT / "data" / "jobs.db")
    policy_path = REPO_ROOT / "config" / "routing_policy.json"
    api_quotas = {}
    if policy_path.exists():
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        api_quotas = policy.get("providers", {})

    cycle = orchestrator.dispatch_next_cycle(api_quotas=api_quotas, online_mode=False)
    if cycle:
        print(f">>> Tarefa {cycle['task_id']} despachada com sucesso!")
        print(f"    Rota Decidida:  {cycle['route']}")
        print(f"    Tipo:           {cycle['task_type']}")
        print(f"    Privacidade:    {cycle['privacy']}")
        print(f"    Arquivo Alvo:   {cycle['target']}")
    else:
        print(">>> Nenhuma tarefa pendente na DAG para despacho.")


def launch_aider(target_files: list[str]):
    print("=" * 65)
    print(" [AIDER CLI] SESSÃO RESTRITA COM TESTES AUTOMÁTICOS")
    print("=" * 65)
    cmd = ["aider", "--config", str(REPO_ROOT / ".aider.conf.yml")] + target_files
    subprocess.run(cmd, cwd=str(REPO_ROOT))


def launch_micro_harness(task_id: str):
    print("=" * 65)
    print(f" [PCI MICRO-HARNESS v2.1] EXECUÇÃO CIRÚRGICA DA TAREFA: {task_id}")
    print("=" * 65)
    from pci_engine.micro_harness import MicroHarness
    db = JobsDatabaseV2(REPO_ROOT / "data" / "jobs.db")
    with db.get_connection() as conn:
        task = conn.execute("SELECT * FROM tasks_queue WHERE task_id = ?", (task_id,)).fetchone()
        if not task:
            print(f"Erro: Tarefa {task_id} não encontrada na fila.")
            return

    harness = MicroHarness(repo_root=REPO_ROOT)
    res = harness.execute_task_cycle(
        task_id=task_id,
        spec_markdown=task["spec_markdown"],
        test_code=task["test_code"],
        target_file_path=task["target_file_path"],
    )

    if res["success"]:
        print(f"\n>>> SUCESSO! Tarefa {task_id} concluída em {res['attempts']} tentativa(s).")
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE tasks_queue SET status = 'VERIFIED', candidate_patch_diff = ? WHERE task_id = ?",
                (res["code"], task_id),
            )
            conn.commit()
    else:
        print(f"\n>>> FALHA na execução da tarefa: {res.get('last_error')}")


def main():
    parser = argparse.ArgumentParser(description="Sovereign Code Engine v2.1 - CLI")
    parser.add_argument("--status", action="store_true", help="Audita status físico e da fila v2.0")
    parser.add_argument("--clean-ram", action="store_true", help="Libera memória RAM fechando abas ociosas")
    parser.add_argument("--route", action="store_true", help="Executa ciclo de decisão e despacho do roteador")
    parser.add_argument("--start-broker", action="store_true", help="Inicia API FastAPI para nós doadores (Tailscale)")
    parser.add_argument("--run-nightly", action="store_true", help="Dispara processamento noturno Colibrì")
    parser.add_argument("--aider", nargs="+", metavar="ARQUIVO", help="Dispara Aider com TDD nos arquivos passados")
    parser.add_argument("--run-harness", metavar="TASK_ID", help="Executa tarefa com MicroHarness ultraleve (<1k tokens)")
    args = parser.parse_args()

    if args.clean_ram:
        clean_ram()
    elif args.route:
        run_routing_cycle()
    elif args.start_broker:
        start_broker()
    elif args.run_nightly:
        worker = NightlyColibriWorker(REPO_ROOT / "data" / "jobs.db", REPO_ROOT)
        worker.execute_batch()
    elif args.aider:
        launch_aider(args.aider)
    elif args.run_harness:
        launch_micro_harness(args.run_harness)
    else:
        check_status()
        print("\nComandos disponíveis:")
        print("  python -m pci_engine.cli --status        (Audita hardware e fila SQLite)")
        print("  python -m pci_engine.cli --clean-ram     (Higieniza RAM do host)")
        print("  python -m pci_engine.cli --route         (Roteador Cognitivo decide próximo envio)")
        print("  python -m pci_engine.cli --start-broker  (Sobe API FastAPI para Doadores Tailscale)")
        print("  python -m pci_engine.cli --run-nightly   (Executa fila noturna Colibrì mmap)")
        print("  python -m pci_engine.cli --aider <arqs>  (Abre sessão contida no Aider)")


if __name__ == "__main__":
    main()
