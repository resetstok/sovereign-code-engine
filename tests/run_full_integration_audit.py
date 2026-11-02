"""
tests/run_full_integration_audit.py
ETAPA 10 — AUDITORIA PROFUNDA DE INTEGRAÇÃO (END-TO-END).

Prova viva de que os componentes do Sovereign Code Engine (PCI Master System v2.0 / v2.1)
se comunicam perfeitamente em tempo de execução:
1. Teste de Fluxo Agêntico Completo: Orquestrador cria worktree/sandbox, roda Pytest,
   aprova código com `assert somar(2, 2) == 4`, comita na branch e atualiza status para VERIFIED.
2. Teste de Queda de Doador (Lease Expiration): Tarefa LEASED vencida há 1 hora é recuperada
   automaticamente pelo Roteador (reclaim_expired_leases) voltando para PENDING com attempt_count incrementado.
3. Inicialização Limpa da GUI: Importa e instancia a classe PCIMasterGUI sem erros,
   comprovando carregamento do CustomTkinter, PIL e montagem completa das abas e bindings.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Configura paths de importação
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from PIL import Image
import customtkinter as ctk

from pci_engine.database import DatabaseManager
from pci_engine.gui_app import PCIMasterGUI, PCIMasterApp
from pci_engine.orchestrator import TaskExecutionOrchestrator
from pci_engine.router import PCIRouter


# =============================================================================
# 1. TESTE DE FLUXO AGÊNTICO COMPLETO
# =============================================================================
def test_agentic_workflow_execution(db: DatabaseManager | None = None) -> None:
    print("\n" + "=" * 75)
    print("TESTE 1: Fluxo Agêntico Completo (TDD Reverso em Sandbox Git Worktree)")
    print("=" * 75)

    if db is None:
        db_path = REPO_ROOT / "data" / "jobs.db"
        db = DatabaseManager(db_path=db_path)

    task_id = f"TASK_E2E_SUM_{int(datetime.now().timestamp())}"
    target_file = "calculadora.py"
    test_code = (
        "from calculadora import somar\n"
        "def test_somar():\n"
        "    assert somar(2, 2) == 4\n"
    )

    # 1. Insere microtarefa no banco
    db.insert_task(
        task_id=task_id,
        parent_masterbook="MB_INTEGRATION_AUDIT",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=12,
        spec_markdown="Implementar função somar(a, b) que retorna a + b.",
        test_code=test_code,
        target_file_path=target_file,
        status="PENDING",
    )
    print(f"  [+] Tarefa {task_id} inserida na fila jobs.db com status 'PENDING'.")

    # 2. Inicializa o orquestrador
    orchestrator = TaskExecutionOrchestrator(repo_root=REPO_ROOT, db_path=db.db_path)

    # 3. Simula retorno de código gerado pela IA (injeção estrita com marcação markdown)
    candidate_code_text = "def somar(a, b):\n    return a + b\n"
    simulated_llm_markdown = f"```python\n{candidate_code_text}```"
    orchestrator._call_llm = lambda prompt, route=None: simulated_llm_markdown

    # 4. Executa ciclo agêntico completo no sandbox
    print("  [*] Executando ciclo de contenção agêntica no worktree/sandbox...")
    result = orchestrator.execute_microtask(task_id, max_attempts=2)

    assert result["success"] is True, f"Orquestrador falhou na execução: {result}"
    print(f"  [+] Sandbox Pytest executado com sucesso! Tentativas: {result['attempts']}.")
    print(f"  [+] Branch de isolamento gerada/comitada: {result.get('branch')}.")

    # 5. Validação no banco de dados
    task_record = db.get_task(task_id)
    assert task_record is not None
    assert task_record["status"] == "VERIFIED", f"Status esperado 'VERIFIED', obtido: '{task_record['status']}'"
    assert "def somar(a, b)" in task_record["candidate_patch_diff"]
    assert task_record["completed_at"] is not None
    print(f"  [+] Status no jobs.db atualizado para 'VERIFIED' com patch gravado.")
    print("[OK - TESTE 1] Fluxo agêntico completou o ciclo TDD com commit e aprovação.")


# =============================================================================
# 2. TESTE DE QUEDA DE DOADOR (LEASE EXPIRATION & RECLAIM)
# =============================================================================
def test_donor_lease_expiration_and_reclaim(db: DatabaseManager | None = None) -> None:
    print("\n" + "=" * 75)
    print("TESTE 2: Queda de Doador e Recuperação Automática de Leases Expirados")
    print("=" * 75)

    if db is None:
        db_path = REPO_ROOT / "data" / "jobs.db"
        db = DatabaseManager(db_path=db_path)

    router = PCIRouter(db_path=db.db_path, repo_root=REPO_ROOT)

    # 1. Registra nó doador para garantir integridade referencial de chave estrangeira
    donor_id = "DONOR_AUDIT_FALLEN_NODE"
    db.register_node(
        node_id=donor_id,
        node_name="PC_AMIGO_DESCONECTADO",
        role="DONOR",
        ram_total_mb=16384,
        vram_total_mb=8192,
        ssd_free_gb=150.0,
        supported_models=["qwen2.5-coder:7b"],
        status="OFFLINE",
    )

    # 2. Insere tarefa travada em 'LEASED' com expiração há 1 hora no passado
    task_id = f"TASK_EXPIRED_LEASE_{int(datetime.now().timestamp())}"
    expired_timestamp = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    db.insert_task(
        task_id=task_id,
        parent_masterbook="MB_INTEGRATION_AUDIT",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=20,
        spec_markdown="Tarefa pública concedida a doador que sofreu queda de conexão.",
        test_code="def test_sample(): assert True",
        target_file_path="sample_donor_job.py",
        status="LEASED",
        max_attempts=3,
    )

    with db.get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks_queue
            SET status = 'LEASED',
                leased_by_node = ?,
                lease_expires_at = ?,
                attempt_count = 0
            WHERE task_id = ?
            """,
            (donor_id, expired_timestamp, task_id),
        )
        conn.commit()

    task_pre = db.get_task(task_id)
    assert task_pre["status"] == "LEASED"
    assert task_pre["attempt_count"] == 0
    print(f"  [+] Tarefa {task_id} configurada como 'LEASED' com expiração em: {expired_timestamp}.")

    # 3. Invoca o mecanismo do Roteador para recuperar tarefas órfãs
    reclaimed_count = router.reclaim_expired_leases()
    assert reclaimed_count >= 1, "Roteador não recuperou a tarefa com lease vencido!"

    # 4. Comprova retorno para 'PENDING' e incremento de attempt_count
    task_post = db.get_task(task_id)
    assert task_post["status"] == "PENDING", f"Status deveria ser 'PENDING', obtido: '{task_post['status']}'"
    assert task_post["attempt_count"] == 1, f"attempt_count deveria ser 1, obtido: {task_post['attempt_count']}"
    assert task_post["leased_by_node"] is None, "leased_by_node não foi limpo!"
    assert task_post["lease_expires_at"] is None, "lease_expires_at não foi limpo!"
    assert "Timeout de Lease" in (task_post["last_error_log"] or "")

    print(f"  [+] Reclaim executado com sucesso: {reclaimed_count} tarefa(s) recuperada(s).")
    print(f"  [+] Tarefa {task_id} restaurada para 'PENDING' com attempt_count = 1.")
    print("[OK - TESTE 2] Queda de doador tratada de forma resiliente e atômica.")


# =============================================================================
# 3. INICIALIZAÇÃO LIMPA DA INTERFACE GRÁFICA (GUI)
# =============================================================================
def test_clean_gui_initialization() -> None:
    print("\n" + "=" * 75)
    print("TESTE 3: Inicialização Limpa da Interface Gráfica CustomTkinter")
    print("=" * 75)

    # 1. Valida módulos gráficos
    print(f"  CustomTkinter Versão: {ctk.__version__}")
    print(f"  Pillow (PIL) Versão: {Image.__version__}")
    assert ctk.__version__ is not None
    assert Image.__version__ is not None

    # 2. Instancia root oculta para não bloquear a sessão gráfica
    root = ctk.CTk()
    root.withdraw()

    try:
        app = PCIMasterGUI(root=root, repo_root=REPO_ROOT)
        assert app.root is not None
        assert hasattr(app, "_setup_ui")
        assert hasattr(app, "tabview")
        assert hasattr(app, "is_hybrid_mode")
        assert hasattr(app, "is_broker_running")
        assert hasattr(app, "remove_pii_var")

        print("  [+] PCIMasterGUI instanciada sem conflitos de dependências.")
        print("  [+] CTkTabview, monitor de hardware e variáveis reativas montadas com sucesso.")
    finally:
        root.destroy()
        print("  [+] Janela e instâncias liberadas da memória com sucesso.")

    print("[OK - TESTE 3] Interface gráfica testada e pronta para o operador.")


# =============================================================================
# EXECUTOR PRINCIPAL DA AUDITORIA DE INTEGRAÇÃO
# =============================================================================
def main():
    print("#" * 75)
    print("INICIANDO ETAPA 10 — AUDITORIA PROFUNDA DE INTEGRAÇÃO (END-TO-END)")
    print("SOVEREIGN CODE ENGINE (PCI MASTER SYSTEM v2.0 / v2.1)")
    print("#" * 75)

    try:
        test_agentic_workflow_execution()
        test_donor_lease_expiration_and_reclaim()
        test_clean_gui_initialization()

        print("\n" + "#" * 75)
        print("[INTEGRAÇÃO COMPLETA] - O motor agêntico funciona de ponta a ponta.")
        print("#" * 75)
        sys.exit(0)

    except Exception as exc:
        print("\n" + "!" * 75)
        print(f"[FALHA NA INTEGRAÇÃO]: {exc}")
        print("!" * 75)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
