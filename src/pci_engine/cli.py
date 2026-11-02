"""
Módulo: pci_engine/cli.py
Entrada unificada de linha de comando da versão 2.0/2.1 do Sovereign Code Engine (PCI Master).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from pci_engine.database import DatabaseManager
from pci_engine.decomposer import MasterbookDecomposer
from pci_engine.micro_harness import MicroHarness
from pci_engine.preflight import PreflightChecker
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.router import PCIRouter
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
    db = DatabaseManager(REPO_ROOT / "data" / "jobs.db")
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


def run_routing_cycle(online_mode: bool = False):
    print("=" * 65)
    print(f" [ROTEADOR COGNITIVO v2.0] DESPACHO DETERMINÍSTICO (Online={online_mode})")
    print("=" * 65)
    db_path = REPO_ROOT / "data" / "jobs.db"
    router = PCIRouter(db_path=db_path, repo_root=REPO_ROOT)
    reclaimed = router.reclaim_expired_leases()
    if reclaimed:
        print(f">>> Leases expirados recuperados: {reclaimed}")

    policy_path = REPO_ROOT / "config" / "routing_policy.json"
    api_quotas = {}
    if policy_path.exists():
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        api_quotas = policy.get("providers", {})

    with router._get_conn() as conn:
        tasks = conn.execute(
            """
            SELECT * FROM tasks_queue
            WHERE status = 'PENDING'
              AND (depends_on_task_id IS NULL OR depends_on_task_id IN (
                  SELECT task_id FROM tasks_queue WHERE status = 'VERIFIED'
              ))
            ORDER BY created_at ASC
            """
        ).fetchall()

    if not tasks:
        print(">>> Nenhuma tarefa pendente desbloqueada na DAG.")
        return

    for t in tasks:
        decision = router.route_task(t, api_quotas=api_quotas, online_mode=online_mode)
        print(f">>> Tarefa {t['task_id']} [{t['task_type']}|{t['privacy_level']}] -> ROTA: {decision}")
        with router._get_conn() as conn:
            conn.execute(
                "UPDATE tasks_queue SET assigned_route = ? WHERE task_id = ?",
                (decision, t["task_id"]),
            )
            conn.commit()


def decompose_masterbook(file_path: str):
    print("=" * 65)
    print(f" [DECOMPOSITOR DE MASTERBOOK] QUEBRA EM DAG: {file_path}")
    print("=" * 65)
    db = DatabaseManager(REPO_ROOT / "data" / "jobs.db")
    decomposer = MasterbookDecomposer(db_manager=db)
    enqueued = decomposer.decompose_file(file_path)
    print(f">>> Sucesso: {len(enqueued)} microtarefa(s) atômica(s) enfileirada(s): {enqueued}")


def launch_micro_harness(task_id: str):
    print("=" * 65)
    print(f" [PCI MICRO-HARNESS v2.1] EXECUÇÃO CIRÚRGICA DA TAREFA: {task_id}")
    print("=" * 65)
    db = DatabaseManager(REPO_ROOT / "data" / "jobs.db")
    harness = MicroHarness(repo_root=REPO_ROOT, db_manager=db)
    res = harness.execute_queued_task(task_id)

    if res.get("success"):
        print(f"\n>>> SUCESSO! Tarefa {task_id} concluída em {res.get('attempts')} tentativa(s).")
    else:
        print(f"\n>>> FALHA na execução da tarefa: {res.get('last_error') or res.get('error')}")


def run_next_local():
    print("=" * 65)
    print(" [LOCAL RUNNER 3B] BUSCANDO PRÓXIMA MICROTAREFA ELEGÍVEL")
    print("=" * 65)
    db = DatabaseManager(REPO_ROOT / "data" / "jobs.db")
    harness = MicroHarness(repo_root=REPO_ROOT, db_manager=db)
    res = harness.run_next_local_task()
    if res:
        if res.get("success"):
            print(f">>> SUCESSO na tarefa {res.get('task_id')}!")
        else:
            print(f">>> FALHA na tarefa {res.get('task_id')}: {res.get('last_error')}")
    else:
        print(">>> Nenhuma microtarefa local pendente na fila.")


def launch_aider(target_files: list[str]):
    print("=" * 65)
    print(" [AIDER CLI] SESSÃO RESTRITA COM TESTES AUTOMÁTICOS")
    print("=" * 65)
    cmd = ["aider", "--config", str(REPO_ROOT / ".aider.conf.yml")] + target_files
    subprocess.run(cmd, cwd=str(REPO_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Sovereign Code Engine v2.1 - CLI")
    parser.add_argument("--status", action="store_true", help="Audita status físico e da fila v2.0")
    parser.add_argument("--clean-ram", action="store_true", help="Libera memória RAM fechando abas ociosas")
    parser.add_argument("--route", action="store_true", help="Executa ciclo de decisão e despacho do roteador")
    parser.add_argument("--online", action="store_true", help="Ativa modo online para cotas de API gratuita")
    parser.add_argument("--decompose", metavar="FILE", help="Decompõe arquivo de Masterbook em DAG atômica")
    parser.add_argument("--start-broker", action="store_true", help="Inicia API FastAPI para nós doadores (Tailscale)")
    parser.add_argument("--run-nightly", action="store_true", help="Dispara processamento noturno Colibrì")
    parser.add_argument("--aider", nargs="+", metavar="ARQUIVO", help="Dispara Aider com TDD nos arquivos passados")
    parser.add_argument("--run-harness", metavar="TASK_ID", help="Executa tarefa específica com MicroHarness (<1k tokens)")
    parser.add_argument("--run-local", action="store_true", help="Puxa e executa próxima microtarefa elegível com o modelo local 3B")
    args = parser.parse_args()

    if args.clean_ram:
        clean_ram()
    elif args.route:
        run_routing_cycle(online_mode=args.online)
    elif args.decompose:
        decompose_masterbook(args.decompose)
    elif args.start_broker:
        start_broker()
    elif args.run_nightly:
        worker = NightlyColibriWorker(REPO_ROOT / "data" / "jobs.db", REPO_ROOT)
        worker.execute_batch()
    elif args.aider:
        launch_aider(args.aider)
    elif args.run_harness:
        launch_micro_harness(args.run_harness)
    elif args.run_local:
        run_next_local()
    else:
        check_status()
        print("\nComandos disponíveis:")
        print("  python -m pci_engine.cli --status              (Audita hardware e fila SQLite)")
        print("  python -m pci_engine.cli --clean-ram           (Higieniza RAM do host)")
        print("  python -m pci_engine.cli --decompose <doc.md>  (Decompõe Masterbook em microtarefas DAG)")
        print("  python -m pci_engine.cli --route [--online]    (Roteador Cognitivo despacha tarefas)")
        print("  python -m pci_engine.cli --run-local           (Executa próxima tarefa no modelo 3B local)")
        print("  python -m pci_engine.cli --run-harness <id>    (Executa tarefa específica no MicroHarness)")
        print("  python -m pci_engine.cli --start-broker        (Sobe API FastAPI para Doadores Tailscale)")
        print("  python -m pci_engine.cli --run-nightly         (Executa fila noturna Colibrì mmap)")
        print("  python -m pci_engine.cli --aider <arqs>        (Abre sessão contida no Aider)")


if __name__ == "__main__":
    main()
