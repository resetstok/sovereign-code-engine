"""
Testes Unitários e de Integração para o MicroHarness (Etapa 4).
Valida o prompt de sistema cirúrgico (<350 tokens), Runtime Invariants (read-before-edit),
truncamento de terminal, diário append-only de falhas, ciclo TDD e integração com jobs.db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.micro_harness import MicroHarness, SYSTEM_PROMPT, JournalEntry


@pytest.fixture
def clean_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_harness_jobs.db"
    db_mgr = DatabaseManager(db_path=db_file)
    db_mgr.register_node(
        node_id="MASTER_NODE",
        node_name="PC_MASTER",
        role="MASTER",
        ram_total_mb=8192,
        vram_total_mb=4096,
        ssd_free_gb=120.0,
        supported_models=["qwen2.5-coder:3b"],
        status="ONLINE",
    )
    return db_mgr


@pytest.fixture
def harness(clean_db: DatabaseManager, tmp_path: Path) -> MicroHarness:
    return MicroHarness(
        repo_root=tmp_path,
        db_manager=clean_db,
        model="qwen2.5-coder:3b",
    )


# =============================================================================
# 1. VALIDAÇÃO DO SYSTEM PROMPT E ORÇAMENTO DE TOKENS
# =============================================================================

def test_system_prompt_token_budget():
    """Garante que o system prompt é cirúrgico e não excede ~350 tokens (< 1500 chars)."""
    words = SYSTEM_PROMPT.split()
    assert len(words) < 200, f"System prompt muito longo: {len(words)} palavras"
    assert len(SYSTEM_PROMPT) < 1500, f"System prompt com {len(SYSTEM_PROMPT)} caracteres"
    assert "TRAVA INVARIANTE" in SYSTEM_PROMPT
    assert "TDD REVERSO" in SYSTEM_PROMPT


# =============================================================================
# 2. RUNTIME INVARIANTS (READ-BEFORE-EDIT / READ-BEFORE-OVERWRITE)
# =============================================================================

def test_runtime_invariant_read_before_edit(harness: MicroHarness, tmp_path: Path):
    """Tentativa de editar arquivo sem leitura prévia deve ser bloqueada em tempo de execução."""
    file_path = tmp_path / "calc.py"
    file_path.write_text("def somar(a, b): return a\n", encoding="utf-8")

    # Edição antes de ler -> VIOLAÇÃO
    res = harness.tool_edit("calc.py", "return a", "return a + b")
    assert "INVARIANT_VIOLATION" in res
    assert "Proibido editar 'calc.py' sem leitura prévia" in res

    # Lê o arquivo
    read_text = harness.tool_read("calc.py")
    assert "return a" in read_text
    assert "calc.py" in harness.read_files_session

    # Agora a edição deve ser autorizada e aplicada
    edit_ok = harness.tool_edit("calc.py", "return a", "return a + b")
    assert "OK: Edição aplicada" in edit_ok
    assert "return a + b" in file_path.read_text(encoding="utf-8")


def test_runtime_invariant_read_before_overwrite(harness: MicroHarness, tmp_path: Path):
    """Tentativa de sobrescrever arquivo existente sem leitura prévia deve ser bloqueada."""
    file_path = tmp_path / "existente.py"
    file_path.write_text("conteudo_antigo = True\n", encoding="utf-8")

    # Sobrescrever sem ler -> VIOLAÇÃO
    res = harness.tool_write("existente.py", "conteudo_novo = True\n")
    assert "INVARIANT_VIOLATION" in res

    # Lê o arquivo
    harness.tool_read("existente.py")

    # Agora a escrita deve funcionar
    res_ok = harness.tool_write("existente.py", "conteudo_novo = True\n")
    assert "OK:" in res_ok
    assert "conteudo_novo = True" in file_path.read_text(encoding="utf-8")


def test_runtime_invariant_new_file_write(harness: MicroHarness, tmp_path: Path):
    """Arquivos novos que não existem podem ser criados diretamente."""
    res = harness.tool_write("novo_arquivo.py", "print('Criado')\n")
    assert "OK:" in res
    assert (tmp_path / "novo_arquivo.py").exists()
    assert "novo_arquivo.py" in harness.read_files_session


# =============================================================================
# 3. TRUNCAMENTO DE TERMINAL (TOOL_BASH)
# =============================================================================

def test_tool_bash_output_truncation(harness: MicroHarness):
    """Valida que tool_bash limita a saída para não estourar o KV cache do 3B."""
    # Gera 100 linhas de saída
    cmd = 'python -c "for i in range(100): print(f\'linha_{i}\')"'
    out = harness.tool_bash(cmd)
    assert "[EXIT_CODE: 0]" in out
    assert len(out) <= 700
    # Deve conter as últimas linhas
    assert "linha_99" in out


def test_tool_bash_exit_code_on_failure(harness: MicroHarness):
    """Valida retorno de código de saída de falha."""
    out = harness.tool_bash('python -c "raise ValueError(\'erro proposital\')"')
    assert "[EXIT_CODE: 1]" in out
    assert "ValueError: erro proposital" in out


# =============================================================================
# 4. DIÁRIO APPEND-ONLY E EXTRAÇÃO DE CÓDIGO
# =============================================================================

def test_journal_reflection_in_prompt(harness: MicroHarness):
    """Verifica que o diário de tentativas anteriores é injetado no prompt de reflexão."""
    harness.journal.append(
        JournalEntry(
            attempt=1,
            failed_test_output="AssertionError: assert 1 == 2",
            action_taken="Retornou 1 em vez de 2",
        )
    )

    prompt = harness.build_prompt_with_journal(
        task_spec="Implemente a função dobro(x)",
        test_code="def test_dobro(): assert dobro(2) == 4",
        target_file="src/math.py",
    )

    assert "DIÁRIO DE TENTATIVAS ANTERIORES QUE FALHARAM" in prompt
    assert "AssertionError: assert 1 == 2" in prompt
    assert "Tentativa 1" in prompt


def test_extract_python_code():
    """Valida a extração de blocos python markdown ou retorno de texto plano."""
    md_code = "Aqui está o código:\n```python\ndef foo():\n    return 42\n```\nEspero ter ajudado."
    extracted = MicroHarness._extract_python_code(md_code)
    assert extracted == "def foo():\n    return 42"

    generic_md = "```\ndef bar():\n    return 10\n```"
    assert MicroHarness._extract_python_code(generic_md) == "def bar():\n    return 10"

    plain_code = "def plain(): return 'raw'"
    assert MicroHarness._extract_python_code(plain_code) == plain_code


# =============================================================================
# 5. CICLO TDD E INTEGRAÇÃO COM JOBS.DB
# =============================================================================

def test_execute_queued_task_success(clean_db: DatabaseManager, harness: MicroHarness, monkeypatch):
    """Testa a execução completa de uma tarefa da fila jobs.db com sucesso no TDD."""
    clean_db.insert_task(
        task_id="TASK_TDD_001",
        parent_masterbook="MB_PILOTO",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=15,
        spec_markdown="Crie uma função somar(a, b) que retorna a soma.",
        test_code="from modulo_soma import somar\ndef test_somar(): assert somar(3, 5) == 8\n",
        target_file_path="modulo_soma.py",
        status="PENDING",
        max_attempts=3,
    )

    # Mock do _call_llm para gerar código correto
    monkeypatch.setattr(
        harness,
        "_call_llm",
        lambda prompt: "```python\ndef somar(a, b):\n    return a + b\n```",
    )

    res = harness.execute_queued_task("TASK_TDD_001")
    assert res["success"] is True
    assert res["attempts"] == 1

    # Verifica status atualizado no banco
    t = clean_db.get_task("TASK_TDD_001")
    assert t["status"] == "VERIFIED"
    assert "def somar(a, b):" in t["candidate_patch_diff"]
    assert t["completed_at"] is not None

    # Verifica que a capability matrix foi atualizada
    cap = clean_db.get_capability("MASTER_NODE", "qwen2.5-coder:3b", "CRUD")
    assert cap is not None
    assert cap["sample_size"] == 1
    assert cap["success_rate"] == 1.0


def test_dependency_unlock_after_verification(clean_db: DatabaseManager, harness: MicroHarness, monkeypatch):
    """Testa se a conclusão de uma tarefa desbloqueia a tarefa dependente (WAITING_DEPS -> PENDING)."""
    clean_db.insert_task(
        task_id="TASK_PARENT",
        parent_masterbook="MB_DAG",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=10,
        spec_markdown="Gere base.py",
        test_code="from base import VAL\ndef test_val(): assert VAL == 100\n",
        target_file_path="base.py",
        status="PENDING",
    )
    clean_db.insert_task(
        task_id="TASK_CHILD",
        parent_masterbook="MB_DAG",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=12,
        spec_markdown="Use base.py",
        test_code="from base import VAL\ndef test_child(): assert VAL == 100\n",
        target_file_path="child.py",
        depends_on_task_id="TASK_PARENT",
        status="WAITING_DEPS",
    )

    # Mock para resolver TASK_PARENT
    monkeypatch.setattr(
        harness,
        "_call_llm",
        lambda prompt: "```python\nVAL = 100\n```",
    )

    harness.execute_queued_task("TASK_PARENT")

    # Verifica se a filha foi automaticamente desbloqueada para PENDING
    child = clean_db.get_task("TASK_CHILD")
    assert child["status"] == "PENDING"


def test_execute_queued_task_failure_and_blocked(clean_db: DatabaseManager, harness: MicroHarness, monkeypatch):
    """Verifica que quando o modelo erra todas as tentativas, a tarefa vai para BLOCKED_HUMAN_REVIEW."""
    clean_db.insert_task(
        task_id="TASK_FAILING",
        parent_masterbook="MB_PILOTO",
        task_type="PARSER_OSINT",
        privacy_level="STRICT_LOCAL",
        estimated_lines=20,
        spec_markdown="Crie funcao impossivel",
        test_code="from impossivel import func\ndef test_func(): assert func() == 'correto'\n",
        target_file_path="impossivel.py",
        status="PENDING",
        max_attempts=2,
    )

    # Mock gerando código que sempre falha no teste
    monkeypatch.setattr(
        harness,
        "_call_llm",
        lambda prompt: "```python\ndef func():\n    return 'errado'\n```",
    )

    res = harness.execute_queued_task("TASK_FAILING")
    assert res["success"] is False

    t = clean_db.get_task("TASK_FAILING")
    assert t["status"] == "BLOCKED_HUMAN_REVIEW"
    assert t["attempt_count"] == 2
    assert "AssertionError" in t["last_error_log"] or "Falha" in t["last_error_log"]


def test_run_next_local_task(clean_db: DatabaseManager, harness: MicroHarness, monkeypatch):
    """Testa a busca e despacho automático da próxima tarefa elegível pelo runner local."""
    clean_db.insert_task(
        task_id="TASK_AUTO_01",
        parent_masterbook="MB_RUNNER",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=15,
        spec_markdown="Cria greet",
        test_code="from greet import ola\ndef test_ola(): assert ola() == 'ola'\n",
        target_file_path="greet.py",
        status="PENDING",
    )

    monkeypatch.setattr(
        harness,
        "_call_llm",
        lambda prompt: "```python\ndef ola():\n    return 'ola'\n```",
    )

    result = harness.run_next_local_task()
    assert result is not None
    assert result["success"] is True

    t = clean_db.get_task("TASK_AUTO_01")
    assert t["status"] == "VERIFIED"
