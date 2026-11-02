"""
Testes Unitários e de Integração para o Executor de Contenção Estrita (src/pci_engine/orchestrator.py).
Valida:
1. Prompt de sistema minimalista (<800 tokens) e diário de no máximo 3 linhas.
2. Trava em tempo de execução 'read-before-edit' (RuntimeInvariantViolation).
3. Verificação sintática prévia com ast.parse() antes de rodar o Pytest.
4. Isolamento em Git Worktree / branch sandbox pci-task/<id> com timeout de 30s e paginação de log (<500 chars).
5. Ciclo TDD completo com transição para VERIFIED e atualização da capability_matrix.
6. Adaptador Aider/OpenCode CLI com flags estritas.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.orchestrator import (
    TaskExecutionOrchestrator,
    RuntimeInvariantViolation,
    SYSTEM_PROMPT_MINIMAL,
)


@pytest.fixture
def clean_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_orchestrator_jobs.db"
    db = DatabaseManager(db_path=db_file)
    db.register_node(
        node_id="MASTER_NODE",
        node_name="PC_MASTER",
        role="MASTER",
        ram_total_mb=8192,
        vram_total_mb=4096,
        ssd_free_gb=100.0,
        supported_models=["qwen2.5-coder:3b"],
        status="ONLINE",
    )
    return db


@pytest.fixture
def orchestrator(clean_db: DatabaseManager, tmp_path: Path) -> TaskExecutionOrchestrator:
    return TaskExecutionOrchestrator(
        repo_root=tmp_path,
        db_path=clean_db.db_path,
        ollama_url="http://127.0.0.1:11434",
        model="qwen2.5-coder:3b",
    )


# =============================================================================
# 1. PROMPT MINIMALISTA (<800 TOKENS) E DIÁRIO DE ERROS
# =============================================================================

def test_system_prompt_minimal_budget():
    """Garante que o system prompt é ultraleve (< 800 tokens / < 1000 caracteres)."""
    words = SYSTEM_PROMPT_MINIMAL.split()
    assert len(words) < 150, f"System prompt muito longo: {len(words)} palavras"
    assert len(SYSTEM_PROMPT_MINIMAL) < 1000
    assert "TDD Reverso" in SYSTEM_PROMPT_MINIMAL


def test_build_prompt_limits_error_to_3_lines(orchestrator: TaskExecutionOrchestrator):
    """Verifica que o diário de erro anterior é truncado em no máximo 3 linhas."""
    multi_line_error = (
        "Traceback (most recent call last):\n"
        "  File 'test.py', line 10, in test_fn\n"
        "    assert resultado == 42\n"
        "E   AssertionError: assert 0 == 42\n"
        "E     - 42\n"
        "E     + 0\n"
    )
    prompt = orchestrator.build_prompt(
        spec_markdown="Implemente fn() que retorna 42",
        test_code="def test_fn(): assert fn() == 42",
        target_file_path="src/fn.py",
        current_content="def fn(): return 0\n",
        previous_error_summary=multi_line_error,
    )

    assert "### CONTEÚDO ATUAL DE 'src/fn.py':" in prompt
    assert "### DIÁRIO DE ERRO ANTERIOR" in prompt
    # Conta as linhas dentro do diário de erro no prompt
    error_section = prompt.split("### DIÁRIO DE ERRO ANTERIOR (NÃO REPITA ESTA FALHA):\n")[1].split("\n\n")[0]
    error_lines = [l for l in error_section.splitlines() if l.startswith("- ")]
    assert len(error_lines) <= 3


# =============================================================================
# 2. RUNTIME INVARIANTS (READ-BEFORE-EDIT E AST.PARSE)
# =============================================================================

def test_runtime_invariant_read_before_edit(orchestrator: TaskExecutionOrchestrator, tmp_path: Path):
    """Arquivo existente que não foi lido antes de editar deve disparar RuntimeInvariantViolation."""
    existing_file = tmp_path / "existente.py"
    existing_file.write_text("def antigo(): pass\n", encoding="utf-8")

    # Tentativa de verificar sem ter lido
    with pytest.raises(RuntimeInvariantViolation) as exc_info:
        orchestrator.verify_read_before_edit("existente.py")
    assert "não foi lido previamente" in str(exc_info.value)

    # Lê o arquivo
    content = orchestrator.read_target_file("existente.py")
    assert content == "def antigo(): pass\n"

    # Agora a validação passa sem exceção
    orchestrator.verify_read_before_edit("existente.py")


def test_syntax_validation_via_ast_parse():
    """Valida que código com erro de sintaxe é rejeitado antes de gastar tempo rodando pytest."""
    valid_code = "def somar(a, b):\n    return a + b\n"
    is_valid, err = TaskExecutionOrchestrator.validate_syntax(valid_code)
    assert is_valid is True
    assert err is None

    # Sintaxe com parênteses não fechado
    invalid_code = "def quebrado(a, b:\n    return a + b\n"
    is_valid_inv, err_inv = TaskExecutionOrchestrator.validate_syntax(invalid_code)
    assert is_valid_inv is False
    assert "SyntaxError" in err_inv


def test_extract_clean_code():
    """Valida extração cirúrgica de código de blocos markdown."""
    raw = "Aqui está a solução:\n```python\ndef saudacao():\n    return 'ola'\n```\nPronto!"
    assert TaskExecutionOrchestrator.extract_clean_code(raw) == "def saudacao():\n    return 'ola'"


# =============================================================================
# 3. CICLO TDD COMPLETO EM GIT WORKTREE / SANDBOX E ATUALIZAÇÃO JOBS.DB
# =============================================================================

def test_execute_microtask_success(clean_db: DatabaseManager, orchestrator: TaskExecutionOrchestrator, monkeypatch):
    """
    Simula uma microtarefa real onde o executor gera uma função Python simples,
    testa com Pytest no sandbox e valida o status VERIFIED no jobs.db.
    """
    clean_db.insert_task(
        task_id="TASK_CALC_001",
        parent_masterbook="MB_PILOTO",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=15,
        spec_markdown="Implemente a função somar(a, b) que retorna a soma dos dois números.",
        test_code="from src.calculadora import somar\ndef test_somar(): assert somar(10, 5) == 15\n",
        target_file_path="src/calculadora.py",
        status="PENDING",
        max_attempts=3,
    )

    # Mock do _call_llm para gerar código correto
    mock_llm_code = "```python\ndef somar(a: int, b: int) -> int:\n    return a + b\n```"
    monkeypatch.setattr(orchestrator, "_call_llm", lambda prompt, route=None: mock_llm_code)

    res = orchestrator.execute_microtask("TASK_CALC_001")

    assert res["success"] is True
    assert res["attempts"] == 1
    assert "def somar" in res["code"]

    # Verifica atualização no banco jobs.db
    t = clean_db.get_task("TASK_CALC_001")
    assert t["status"] == "VERIFIED"
    assert "def somar(a: int, b: int)" in t["candidate_patch_diff"]
    assert t["completed_at"] is not None

    # Verifica atualização na capability_matrix
    cap = clean_db.get_capability("MASTER_NODE", "qwen2.5-coder:3b", "CRUD")
    assert cap is not None
    assert cap["sample_size"] == 1
    assert cap["success_rate"] == 1.0


def test_syntax_error_rejected_before_pytest(clean_db: DatabaseManager, orchestrator: TaskExecutionOrchestrator, monkeypatch):
    """Garante que código com sintaxe quebrada não roda pytest e incrementa erro no diário."""
    clean_db.insert_task(
        task_id="TASK_SYNTAX_FAIL",
        parent_masterbook="MB_PILOTO",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=10,
        spec_markdown="Gere funcao",
        test_code="def test_dummy(): assert True\n",
        target_file_path="src/syntax_fail.py",
        status="PENDING",
        max_attempts=2,
    )

    # Mock gerando código com sintaxe inválida
    monkeypatch.setattr(
        orchestrator,
        "_call_llm",
        lambda prompt, route=None: "```python\ndef quebrado(\n```",
    )

    res = orchestrator.execute_microtask("TASK_SYNTAX_FAIL", max_attempts=2)
    assert res["success"] is False
    assert "SyntaxError" in res["last_error"]

    t = clean_db.get_task("TASK_SYNTAX_FAIL")
    assert t["status"] == "BLOCKED_HUMAN_REVIEW"
    assert "SyntaxError" in t["last_error_log"]


def test_traceback_tail_pagination(clean_db: DatabaseManager, orchestrator: TaskExecutionOrchestrator, monkeypatch):
    """Verifica que traceback longo é paginado mantendo no máximo os últimos 500 caracteres."""
    clean_db.insert_task(
        task_id="TASK_FAIL_PAGINATE",
        parent_masterbook="MB_PILOTO",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=12,
        spec_markdown="Gere funcao com falha",
        test_code="from fail_mod import test_val\ndef test_val(): assert test_val == 999\n",
        target_file_path="fail_mod.py",
        status="PENDING",
        max_attempts=1,
    )

    monkeypatch.setattr(
        orchestrator,
        "_call_llm",
        lambda prompt, route=None: "```python\ntest_val = 111\n```",
    )

    res = orchestrator.execute_microtask("TASK_FAIL_PAGINATE", max_attempts=1)
    assert res["success"] is False
    assert len(res["last_error"]) <= 500


# =============================================================================
# 4. ADAPTADOR AIDER / OPENCODE CLI
# =============================================================================

def test_aider_cli_adapter_flags(orchestrator: TaskExecutionOrchestrator):
    """Valida montagem das flags estritas para o Aider CLI (--no-repomap, etc)."""
    retcode, cmd = orchestrator.launch_aider_cli(
        target_files=["src/auth.py", "src/models.py"],
        test_cmd="pytest -q --tb=short",
        extra_flags=["--yes-always"],
        dry_run=True,
    )
    assert retcode == 0
    assert "--no-repomap" in cmd
    assert "--message-history-size" in cmd
    assert cmd[cmd.index("--message-history-size") + 1] == "3"
    assert "--test-cmd" in cmd
    assert cmd[cmd.index("--test-cmd") + 1] == "pytest -q --tb=short"
    assert "--yes-always" in cmd
    assert "src/auth.py" in cmd
    assert "src/models.py" in cmd
