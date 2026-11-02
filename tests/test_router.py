"""
Testes Unitários para o Roteador Cognitivo (PCIRouter) e Decompositor de Masterbook (Etapa 3).
Verifica os 7 ramos da árvore de decisão determinística, recuperação de leases expirados,
GBNF/Pydantic schemas e importador manual de tarefas .md.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.router import PCIRouter
from pci_engine.decomposer import (
    MasterbookDecomposer,
    MicrotaskDefinition,
    MasterbookDecompositionResult,
    TaskPrivacyLevel,
    TaskTypeEnum,
)


@pytest.fixture
def clean_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_jobs.db"
    db_mgr = DatabaseManager(db_path=db_file)
    # Registra o nó mestre padrão para integridade referencial de chaves estrangeiras
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
def router(clean_db: DatabaseManager, tmp_path: Path) -> PCIRouter:
    return PCIRouter(db_path=clean_db.db_path, repo_root=tmp_path)


# =============================================================================
# TESTES DA ÁRVORE DE DECISÃO DETERMINÍSTICA (7 RAMOS DO PCIROUTER)
# =============================================================================

def test_route_branch_1_blocked_human_review(router: PCIRouter):
    """Ramo 1: Tarefa com attempt_count >= max_attempts deve ir para BLOCKED_HUMAN_REVIEW."""
    task = {
        "task_id": "T_FAIL",
        "privacy_level": "PUBLIC",
        "estimated_lines": 20,
        "task_type": "EXTRACTION",
        "attempt_count": 3,
        "max_attempts": 3,
    }
    route = router.route_task(task, api_quotas={}, online_mode=True)
    assert route == "BLOCKED_HUMAN_REVIEW"


def test_route_branch_2_strict_local(router: PCIRouter):
    """Ramo 2: Tarefas STRICT_LOCAL nunca saem do Master PC (LOCAL_3B se <= 35, senão NIGHTLY_14B)."""
    # <= 35 linhas -> LOCAL_3B
    task_small = {
        "task_id": "T_LOCAL_SMALL",
        "privacy_level": "STRICT_LOCAL",
        "estimated_lines": 30,
        "task_type": "EXTRACTION",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    quotas = {"groq": {"remaining_requests": 100}}
    assert router.route_task(task_small, api_quotas=quotas, online_mode=True) == "LOCAL_3B"

    # > 35 linhas -> NIGHTLY_14B (mesmo com APIs e Doadores disponíveis)
    task_large = {
        "task_id": "T_LOCAL_LARGE",
        "privacy_level": "STRICT_LOCAL",
        "estimated_lines": 50,
        "task_type": "EXTRACTION",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    assert router.route_task(task_large, api_quotas=quotas, online_mode=True) == "NIGHTLY_14B"


def test_route_branch_3_local_3b_qualification(clean_db: DatabaseManager, router: PCIRouter):
    """Ramo 3: Linhas <= 30 e taxa >= 0.86 na capability_matrix deve ir para LOCAL_3B."""
    task = {
        "task_id": "T_LOCAL_QUAL",
        "privacy_level": "PUBLIC",
        "estimated_lines": 25,
        "task_type": "CRUD",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    # Insere capability satisfatória
    clean_db.record_capability("MASTER_NODE", "qwen2.5-coder:3b", "CRUD", sample_size=10, success_rate=0.92)
    assert router.route_task(task, api_quotas={}, online_mode=False) == "LOCAL_3B"

    # Se capability for baixa (< 0.86), não deve ir para LOCAL_3B
    clean_db.record_capability("MASTER_NODE", "qwen2.5-coder:3b", "CRUD", sample_size=10, success_rate=0.75)
    assert router.route_task(task, api_quotas={}, online_mode=False) != "LOCAL_3B"


def test_route_branch_4_heavy_refactor_or_large(router: PCIRouter):
    """Ramo 4: Refatoração ou código > 120 linhas vai para NIGHTLY_14B."""
    task_refactor = {
        "task_id": "T_REFACTOR",
        "privacy_level": "PUBLIC",
        "estimated_lines": 20,
        "task_type": "REFACTOR",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    assert router.route_task(task_refactor, api_quotas={}, online_mode=False) == "NIGHTLY_14B"

    task_large = {
        "task_id": "T_LARGE",
        "privacy_level": "PUBLIC",
        "estimated_lines": 150,
        "task_type": "CRUD",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    assert router.route_task(task_large, api_quotas={}, online_mode=False) == "NIGHTLY_14B"


def test_route_branch_5_cloud_api(router: PCIRouter):
    """Ramo 5: Modo Online ativo com cota em APIs gratuitas para tarefas públicas/sanitizadas."""
    task = {
        "task_id": "T_CLOUD",
        "privacy_level": "SANITIZED_DERIVATIVE",
        "estimated_lines": 60,
        "task_type": "CRUD",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    quotas = {
        "groq": {"remaining_requests": 50},
        "cerebras": {"remaining_requests": 10},
    }
    assert router.route_task(task, api_quotas=quotas, online_mode=True) == "CLOUD_API:groq"

    # Se groq estiver sem cota, usa cerebras
    quotas_no_groq = {
        "groq": {"remaining_requests": 0},
        "cerebras": {"remaining_requests": 20},
    }
    assert router.route_task(task, api_quotas=quotas_no_groq, online_mode=True) == "CLOUD_API:cerebras"


def test_route_branch_6_donor_pool(clean_db: DatabaseManager, router: PCIRouter):
    """Ramo 6: Se APIs esgotadas/offline e houver Doador qualificado online -> DONOR_POOL."""
    task = {
        "task_id": "T_DONOR",
        "privacy_level": "PUBLIC",
        "estimated_lines": 50,
        "task_type": "PARSER_OSINT",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    # Sem doador online -> cai para NIGHTLY_14B
    assert router.route_task(task, api_quotas={}, online_mode=False) == "NIGHTLY_14B"

    # Cadastra nó doador online e qualificado
    clean_db.register_node(
        node_id="DONOR_PC_01",
        node_name="PC_DOADOR_1",
        role="DONOR",
        ram_total_mb=16384,
        vram_total_mb=8192,
        ssd_free_gb=100.0,
        supported_models=["qwen2.5-coder:7b"],
        status="ONLINE",
    )
    clean_db.record_capability(
        node_id="DONOR_PC_01",
        model_name="qwen2.5-coder:7b",
        task_type="PARSER_OSINT",
        sample_size=10,
        success_rate=0.90,
    )

    assert router.route_task(task, api_quotas={}, online_mode=False) == "DONOR_POOL"


def test_route_branch_7_fallback_nightly(router: PCIRouter):
    """Ramo 7: Fallback determinístico sem vazamento de dados vai para NIGHTLY_14B."""
    task = {
        "task_id": "T_FALLBACK",
        "privacy_level": "PUBLIC",
        "estimated_lines": 80,
        "task_type": "CRUD",
        "attempt_count": 0,
        "max_attempts": 3,
    }
    # Sem APIs, sem doadores -> NIGHTLY_14B
    assert router.route_task(task, api_quotas={}, online_mode=False) == "NIGHTLY_14B"


# =============================================================================
# TESTES DE RECUPERAÇÃO DE LEASES EXPIRADOS
# =============================================================================

def test_reclaim_expired_leases(clean_db: DatabaseManager, router: PCIRouter):
    """Verifica que leases vencidos voltam para PENDING e incrementam attempt_count."""
    clean_db.register_node(
        node_id="DONOR_01",
        node_name="PC_DOADOR_ALPHA",
        role="DONOR",
        ram_total_mb=16384,
        vram_total_mb=8192,
        ssd_free_gb=100.0,
        supported_models=["qwen2.5-coder:7b"],
        status="ONLINE",
    )

    past_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    clean_db.insert_task(
        task_id="TASK_LEASE_EXPIRED",
        parent_masterbook="MB_TEST",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=20,
        spec_markdown="spec",
        test_code="def test_a(): pass",
        target_file_path="src/a.py",
        status="LEASED",
        max_attempts=3,
    )
    with clean_db.get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks_queue
            SET leased_by_node = 'DONOR_01', lease_expires_at = ?, attempt_count = 0
            WHERE task_id = 'TASK_LEASE_EXPIRED'
            """,
            (past_time,),
        )

    reclaimed = router.reclaim_expired_leases()
    assert reclaimed == 1

    t = clean_db.get_task("TASK_LEASE_EXPIRED")
    assert t["status"] == "PENDING"
    assert t["leased_by_node"] is None
    assert t["lease_expires_at"] is None
    assert t["attempt_count"] == 1
    assert "Timeout de Lease" in t["last_error_log"]


def test_reclaim_expired_leases_to_blocked_when_max_attempts(clean_db: DatabaseManager, router: PCIRouter):
    """Verifica que quando o lease expira e atinge max_attempts, vai para BLOCKED_HUMAN_REVIEW."""
    clean_db.register_node(
        node_id="DONOR_02",
        node_name="PC_DOADOR_BETA",
        role="DONOR",
        ram_total_mb=16384,
        vram_total_mb=8192,
        ssd_free_gb=100.0,
        supported_models=["qwen2.5-coder:7b"],
        status="ONLINE",
    )

    past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    clean_db.insert_task(
        task_id="TASK_LEASE_FINAL",
        parent_masterbook="MB_TEST",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=20,
        spec_markdown="spec",
        test_code="def test_b(): pass",
        target_file_path="src/b.py",
        status="LEASED",
        max_attempts=2,
    )
    with clean_db.get_connection() as conn:
        conn.execute(
            """
            UPDATE tasks_queue
            SET leased_by_node = 'DONOR_02', lease_expires_at = ?, attempt_count = 1
            WHERE task_id = 'TASK_LEASE_FINAL'
            """,
            (past_time,),
        )

    reclaimed = router.reclaim_expired_leases()
    assert reclaimed == 1

    t = clean_db.get_task("TASK_LEASE_FINAL")
    assert t["status"] == "BLOCKED_HUMAN_REVIEW"
    assert t["attempt_count"] == 2


# =============================================================================
# TESTES DO DECOMPOSITOR DE MASTERBOOK E IMPORTAÇÃO MANUAL
# =============================================================================

def test_pydantic_schema_validation():
    """Valida que o schema Pydantic de microtarefas funciona conforme especificado."""
    task = MicrotaskDefinition(
        task_id="TASK_VALID_001",
        depends_on_task_id=None,
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        permission_level=2,
        estimated_lines=25,
        target_file_path="src/parser.py",
        spec_markdown="Escreva uma função que parseia dados.",
        test_code="def test_parse(): assert True",
        max_attempts=3,
    )
    assert task.task_id == "TASK_VALID_001"
    assert task.estimated_lines == 25

    result = MasterbookDecompositionResult(
        parent_masterbook="MASTERBOOK_TEST",
        tasks=[task],
    )
    assert len(result.tasks) == 1
    # Verifica que gera JSON Schema válido para o Ollama
    schema = MasterbookDecompositionResult.model_json_schema()
    assert "properties" in schema
    assert "tasks" in schema["properties"]


def test_deterministic_markdown_decomposition(clean_db: DatabaseManager):
    """Testa a decomposição determinística de texto Markdown em DAG encadeada."""
    decomposer = MasterbookDecomposer(db_manager=clean_db)

    markdown_doc = """
# Masterbook Exemplo

### TAREFA TASK_GEO_01: Extrair coordenadas
Arquivo-Alvo: `src/geo.py`
Requisitos: Parsear latitudes e longitudes válidas.
```python
def test_geo():
    assert True
```

### TAREFA TASK_GEO_02: Calcular distâncias
Target: `src/distance.py`
Requisitos: Calcular distância euclidiana.
```python
def test_distance():
    assert True
```
"""
    task_ids = decomposer.decompose_from_text(
        markdown_doc, parent_name="MB_GEO", force_fallback=True
    )
    assert len(task_ids) == 2
    assert "TASK_GEO_01" in task_ids
    assert "TASK_GEO_02" in task_ids

    # Verifica status e dependência no banco
    t1 = clean_db.get_task("TASK_GEO_01")
    t2 = clean_db.get_task("TASK_GEO_02")

    assert t1["status"] == "PENDING"
    assert t1["depends_on_task_id"] is None
    assert t1["target_file_path"] == "src/geo.py"

    assert t2["status"] == "WAITING_DEPS"
    assert t2["depends_on_task_id"] == "TASK_GEO_01"
    assert t2["target_file_path"] == "src/distance.py"


def test_import_manual_task_file_and_directory(clean_db: DatabaseManager, tmp_path: Path):
    """Testa a ingestão direta de arquivos de especificação .md de tasks/active/."""
    active_dir = tmp_path / "tasks" / "active"
    active_dir.mkdir(parents=True)

    task_file_1 = active_dir / "TASK_IMPORT_001.md"
    task_file_1.write_text(
        """# Tarefa de Parser
Arquivo-Alvo: `src/csv_parser.py`
Privacidade: `STRICT_LOCAL`
Linhas: 18
Tipo: `PARSER_OSINT`

Parseia arquivo CSV com integridade forense.

```python
def test_csv_parser():
    assert 1 + 1 == 2
```
""",
        encoding="utf-8",
    )

    task_file_2 = active_dir / "TASK_IMPORT_002.md"
    task_file_2.write_text(
        """# Tarefa de Formatação
Target: `src/formatter.py`
Privacy: `PUBLIC`
Lines: 22
Type: `CRUD`

```python
def test_formatter():
    assert True
```
""",
        encoding="utf-8",
    )

    decomposer = MasterbookDecomposer(db_manager=clean_db)
    imported = decomposer.import_all_active_tasks(active_dir=active_dir)

    assert len(imported) == 2
    assert "TASK_IMPORT_001" in imported
    assert "TASK_IMPORT_002" in imported

    t1 = clean_db.get_task("TASK_IMPORT_001")
    assert t1["privacy_level"] == "STRICT_LOCAL"
    assert t1["estimated_lines"] == 18
    assert t1["task_type"] == "PARSER_OSINT"
    assert t1["target_file_path"] == "src/csv_parser.py"
    assert "assert 1 + 1 == 2" in t1["test_code"]
