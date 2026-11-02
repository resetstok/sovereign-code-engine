"""
Testes Unitários para o Processador Noturno Colibrì (Etapa 6/7).
Valida a montagem do comando llama-server com contenção de RAM (--mmap 1),
varredura de comentários # REFACTOR_NIGHTLY, checagem de usuário ativo,
higiene preventiva de RAM, execução em lote e commit na branch nightly/colibri-batch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.nightly_colibri import NightlyColibriWorker


@pytest.fixture
def clean_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_nightly_jobs.db"
    db = DatabaseManager(db_path=db_file)
    db.register_node(
        node_id="MASTER_NIGHTLY_COLIBRI",
        node_name="PC_MASTER",
        role="MASTER",
        ram_total_mb=8192,
        vram_total_mb=4096,
        ssd_free_gb=100.0,
        supported_models=["qwen2.5-coder:14b"],
        status="ONLINE",
    )
    return db


@pytest.fixture
def worker(clean_db: DatabaseManager, tmp_path: Path) -> NightlyColibriWorker:
    return NightlyColibriWorker(
        db_path=clean_db.db_path,
        repo_root=tmp_path,
        server_url="http://127.0.0.1:8080",
        model_name="qwen2.5-coder:14b",
    )


# =============================================================================
# 1. PARÂMETROS DO LLAMA-SERVER E PARSER
# =============================================================================

def test_build_llama_server_command():
    """Garante que a linha de comando do llama-server contém travas estritas de RAM e VRAM."""
    cmd = NightlyColibriWorker.build_llama_server_command(
        model_gguf_path="models/qwen2.5-coder-14b-q4_k_m.gguf",
        port=8080,
        gpu_layers=16,
        ctx_size=4096,
        threads=4,
    )
    assert "llama-server" in cmd[0]
    assert "--mmap" in cmd
    assert cmd[cmd.index("--mmap") + 1] == "1"
    assert "-ngl" in cmd
    assert cmd[cmd.index("-ngl") + 1] == "16"
    assert "-c" in cmd
    assert cmd[cmd.index("-c") + 1] == "4096"


def test_extract_code_variants():
    """Valida extração de código com blocos python e sem blocos."""
    sample = "```python\ndef refatorar():\n    return 'limpo'\n```"
    assert NightlyColibriWorker._extract_code(sample) == "def refatorar():\n    return 'limpo'"

    sample_generic = "```\ndef limpo(): pass\n```"
    assert NightlyColibriWorker._extract_code(sample_generic) == "def limpo(): pass"


# =============================================================================
# 2. VARREDURA DE COMENTÁRIOS # REFACTOR_NIGHTLY
# =============================================================================

def test_scan_refactor_comments(worker: NightlyColibriWorker, tmp_path: Path):
    """Testa a descoberta de comentários # REFACTOR_NIGHTLY no projeto e inserção na fila."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    module_code = (
        "def calcular_imposto(valor):\n"
        "    # REFACTOR_NIGHTLY: Otimizar calculo de aliquotas usando dicionario estatico\n"
        "    return valor * 0.15\n"
    )
    (src_dir / "fiscal.py").write_text(module_code, encoding="utf-8")

    # Executa varredura
    scanned = worker.scan_refactor_comments()
    assert len(scanned) == 1
    assert scanned[0]["instruction"] == "Otimizar calculo de aliquotas usando dicionario estatico"
    assert scanned[0]["line"] == 2

    # Verifica se a tarefa foi criada no jobs.db com a rota correta
    tasks = worker.fetch_nightly_tasks()
    assert len(tasks) == 1
    t = tasks[0]
    assert t["task_type"] == "REFACTOR"
    assert t["assigned_route"] == "NIGHTLY_14B"
    assert t["status"] == "NIGHTLY_BATCH"

    # Segunda varredura não deve duplicar tarefas existentes
    scanned_again = worker.scan_refactor_comments()
    assert len(scanned_again) == 0


# =============================================================================
# 3. DETECÇÃO DE ATIVIDADE DO USUÁRIO E HIGIENE DE RAM
# =============================================================================

def test_user_activity_detection():
    """Testa o detector de atividade do usuário."""
    # Como função pura, deve retornar booleano sem levantar exceção
    active = NightlyColibriWorker.is_user_active(idle_threshold_sec=3600)
    assert isinstance(active, bool)


def test_preflight_hygiene(worker: NightlyColibriWorker):
    """Verifica que a rotina preventiva de limpeza de RAM e caches executa com sucesso."""
    hygiene = worker.run_preflight_hygiene()
    assert "ram_after_mb" in hygiene
    assert "total_freed_mb" in hygiene
    assert hygiene["ram_after_mb"] > 0


# =============================================================================
# 4. FILTRAGEM DE TAREFAS NOTURNAS E EXECUÇÃO EM LOTE
# =============================================================================

def test_fetch_nightly_tasks(clean_db: DatabaseManager, worker: NightlyColibriWorker):
    """Verifica que apenas tarefas destinadas à madrugada são selecionadas."""
    clean_db.insert_task(
        task_id="TASK_NIGHT_01",
        parent_masterbook="MB_NIGHT",
        task_type="REFACTOR",
        privacy_level="PUBLIC",
        estimated_lines=150,
        spec_markdown="Refatorar módulo legado",
        test_code="def test_ref(): pass",
        target_file_path="src/legacy.py",
        status="PENDING",
    )
    clean_db.update_task_status("TASK_NIGHT_01", status="PENDING", assigned_route="NIGHTLY_14B")

    clean_db.insert_task(
        task_id="TASK_DAY_01",
        parent_masterbook="MB_NIGHT",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=20,
        spec_markdown="spec",
        test_code="def test_crud(): pass",
        target_file_path="src/crud.py",
        status="PENDING",
    )
    clean_db.update_task_status("TASK_DAY_01", status="PENDING", assigned_route="LOCAL_3B")

    clean_db.insert_task(
        task_id="TASK_DONE_01",
        parent_masterbook="MB_NIGHT",
        task_type="REFACTOR",
        privacy_level="PUBLIC",
        estimated_lines=100,
        spec_markdown="spec",
        test_code="def test_done(): pass",
        target_file_path="src/done.py",
        status="VERIFIED",
    )

    tasks = worker.fetch_nightly_tasks()
    task_ids = [t["task_id"] for t in tasks]
    assert len(tasks) == 1
    assert "TASK_NIGHT_01" in task_ids
    assert "TASK_DAY_01" not in task_ids
    assert "TASK_DONE_01" not in task_ids


def test_execute_batch_success(clean_db: DatabaseManager, worker: NightlyColibriWorker, monkeypatch):
    """Testa o ciclo de lote noturno com sandbox aprovando a submissão."""
    clean_db.insert_task(
        task_id="TASK_BATCH_01",
        parent_masterbook="MB_BATCH",
        task_type="REFACTOR",
        privacy_level="PUBLIC",
        estimated_lines=80,
        spec_markdown="Cria otimizacao",
        test_code="def test_otim(): assert True",
        target_file_path="src/otim.py",
        status="NIGHTLY_BATCH",
    )

    monkeypatch.setattr(
        worker,
        "_call_inference_engine",
        lambda prompt: "```python\ndef execute(): return True\n```",
    )
    monkeypatch.setattr(
        worker.router,
        "verify_and_integrate_submission",
        lambda task_id, node_id, candidate_code, compute_sec: True,
    )
    monkeypatch.setattr(worker, "commit_to_nightly_branch", lambda *args, **kwargs: True)

    success_count = worker.execute_batch(max_tasks=5)
    assert success_count == 1

    tasks_remaining = worker.fetch_nightly_tasks()
    assert len(tasks_remaining) == 0


def test_run_nightly_pipeline_user_active_abort(worker: NightlyColibriWorker, monkeypatch):
    """Verifica que se o usuário estiver ativo no PC, o pipeline noturno é abortado imediatamente."""
    monkeypatch.setattr(worker, "is_user_active", lambda idle_threshold_sec: True)
    res = worker.run_nightly_pipeline(check_user=True)
    assert res["status"] == "ABORTED_USER_ACTIVE"
    assert res["success_count"] == 0


def test_run_nightly_pipeline_complete(worker: NightlyColibriWorker, clean_db: DatabaseManager, monkeypatch):
    """Testa o ciclo completo orquestrado do pipeline noturno."""
    monkeypatch.setattr(worker, "is_user_active", lambda idle_threshold_sec: False)
    monkeypatch.setattr(worker, "start_llama_server", lambda **kw: False)
    monkeypatch.setattr(worker, "execute_batch", lambda max_tasks: 2)

    res = worker.run_nightly_pipeline(check_user=True)
    assert res["status"] == "COMPLETED"
    assert res["success_count"] == 2
    assert "hygiene" in res
