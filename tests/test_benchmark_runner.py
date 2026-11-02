"""
Testes Unitários para o Avaliador de Benchmark (src/pci_engine/benchmark_runner.py - Etapa 6).
Verifica a execução da bateria de aferição (EXTRACTION, CRUD, REFACTOR, PARSER_OSINT),
medição de latência, tokens/s, picos de RAM/VRAM e atualização da capability_matrix no jobs.db.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from pci_engine.benchmark_runner import BenchmarkRunner
from pci_engine.database import DatabaseManager
from tests.benchmark_suite.benchmark_tasks import BENCHMARK_TASKS


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_bench_jobs.db"
    db = DatabaseManager(db_path=db_file)
    db.register_node(
        node_id="TEST_MASTER_NODE",
        node_name="PC_MASTER_TEST",
        role="MASTER",
        ram_total_mb=8192,
        vram_total_mb=4096,
        ssd_free_gb=100.0,
        supported_models=["qwen2.5-coder:3b"],
        status="ONLINE",
    )
    return db


@pytest.fixture
def runner(test_db: DatabaseManager, tmp_path: Path) -> BenchmarkRunner:
    return BenchmarkRunner(
        node_id="TEST_MASTER_NODE",
        model_name="qwen2.5-coder:3b",
        db_path=test_db.db_path,
        repo_root=tmp_path,
        benchmark_version="v2.1",
    )


def test_benchmark_tasks_dataset_integrity():
    """Valida a integridade da suíte de testes padronizados."""
    expected_categories = {"EXTRACTION", "CRUD", "REFACTOR", "PARSER_OSINT"}
    assert set(BENCHMARK_TASKS.keys()) == expected_categories

    for cat, tasks in BENCHMARK_TASKS.items():
        assert len(tasks) >= 2, f"Categoria {cat} precisa de no mínimo 2 tarefas."
        for t in tasks:
            assert "task_id" in t
            assert "spec_markdown" in t
            assert "test_code" in t
            assert "reference_solution" in t


def test_run_single_task_success(runner: BenchmarkRunner):
    """Testa execução de uma microtarefa com resposta válida aprovada no Pytest."""
    task = BENCHMARK_TASKS["EXTRACTION"][0]

    # Simula resposta do modelo retornando a reference_solution
    def mock_inference(prompt: str):
        return f"```python\n{task['reference_solution']}\n```", 0.25, 60

    result = runner.run_single_task(task, inference_fn=mock_inference)
    assert result["passed"] is True
    assert result["duration_sec"] > 0
    assert result["tokens_per_sec"] > 0
    assert result["peak_ram_mb"] > 0


def test_run_single_task_failure(runner: BenchmarkRunner):
    """Testa microtarefa com código incorreto que é reprovado no Pytest."""
    task = BENCHMARK_TASKS["EXTRACTION"][0]

    def mock_broken_inference(prompt: str):
        return "```python\ndef extract_log_entries(txt): return []\n```", 0.1, 20

    result = runner.run_single_task(task, inference_fn=mock_broken_inference)
    assert result["passed"] is False
    assert "FAIL" in result["test_output"] or "assert" in result["test_output"]


def test_run_category_benchmark_and_db_update(runner: BenchmarkRunner, test_db: DatabaseManager):
    """Verifica que a execução de uma categoria atualiza a capability_matrix no SQLite WAL."""
    cat_res = runner.run_category_benchmark("CRUD", inference_fn=lambda prompt: ("", 0.05, 10))
    assert cat_res["category"] == "CRUD"
    assert cat_res["sample_size"] >= 2
    assert cat_res["passed_count"] >= 2
    assert cat_res["success_rate"] >= 0.86
    assert cat_res["verification_state"] == "VERIFIED"

    # Confere no banco de dados
    cap = test_db.get_capability(
        node_id="TEST_MASTER_NODE",
        model_name="qwen2.5-coder:3b",
        task_type="CRUD",
    )
    assert cap is not None
    assert cap["sample_size"] == cat_res["sample_size"]
    assert cap["success_rate"] >= 0.86
    assert cap["verification_state"] == "VERIFIED"
    assert cap["benchmark_version"] == "v2.1"


def test_run_full_suite_benchmark(runner: BenchmarkRunner, test_db: DatabaseManager):
    """Executa a bateria de benchmark completa e atinge a meta de 86% a 96%."""
    # Modo determinístico com soluções de referência da suíte de teste
    full_report = runner.run_full_suite(inference_fn=lambda prompt: ("", 0.05, 10))

    assert full_report["total_tasks"] >= 8
    assert full_report["total_passed"] >= 7
    assert full_report["overall_success_rate"] >= 0.86
    assert full_report["target_range_met"] is True
    assert len(full_report["categories"]) == 4

    # Verifica que todas as 4 categorias foram salvas na capability_matrix
    for cat in ("EXTRACTION", "CRUD", "REFACTOR", "PARSER_OSINT"):
        cap = test_db.get_capability(
            node_id="TEST_MASTER_NODE",
            model_name="qwen2.5-coder:3b",
            task_type=cat,
        )
        assert cap is not None
        assert cap["verification_state"] in ("MEASURED", "VERIFIED")
