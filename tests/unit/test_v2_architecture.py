"""
Testes unitários e de integração da arquitetura v2.0 (Broker SQLite, Roteador, Sanitizador e Lease).
"""

import json
import sqlite3
import tempfile
from pathlib import Path
import pytest

from pci_engine.db import JobsDatabaseV2
from pci_engine.preflight import PreflightChecker
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.router import PCIRouterAndBroker
from pci_engine.orchestrator import MasterbookOrchestrator, MicrotaskDAGNode
from pci_engine.sanitizer import ForensicSanitizer


class TestV2Architecture:

    def test_01_db_schema_and_integrity(self, tmp_path):
        db_file = tmp_path / "test_jobs.db"
        db = JobsDatabaseV2(db_path=db_file)
        with db.get_connection() as conn:
            # Verifica se todas as 5 tabelas existem
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            assert "nodes" in tables
            assert "capability_matrix" in tables
            assert "tasks_queue" in tables
            assert "kudos_ledger" in tables
            assert "evidence_manifest" in tables

            # Verifica modo WAL
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert journal.upper() == "WAL"

    def test_02_router_decision_tree(self, tmp_path):
        db_file = tmp_path / "test_jobs.db"
        router = PCIRouterAndBroker(db_path=db_file)

        # Simula nó Master
        with router._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO nodes (node_id, node_name, role, ram_total_mb, vram_total_mb, ssd_free_gb, supported_models, status)
                VALUES ('MASTER_NODE', 'MASTER', 'MASTER', 8000, 4000, 100.0, '["qwen2.5-coder:3b"]', 'ONLINE')
                """
            )
            conn.execute(
                """
                INSERT INTO capability_matrix (node_id, model_name, task_type, sample_size, success_rate, benchmark_version)
                VALUES ('MASTER_NODE', 'qwen2.5-coder:3b', 'EXTRACTION', 25, 0.92, 'v2.0')
                """
            )
            conn.commit()

        api_quotas = {"groq": {"remaining_requests": 20}}

        # Cenário 1: STRICT_LOCAL curta (<= 35) -> LOCAL_3B
        task_1 = {
            "privacy_level": "STRICT_LOCAL",
            "estimated_lines": 20,
            "task_type": "EXTRACTION",
            "attempt_count": 0,
            "max_attempts": 3,
        }
        assert router.route_task(task_1, api_quotas, online_mode=True) == "LOCAL_3B"

        # Cenário 2: STRICT_LOCAL longa (> 35) -> NIGHTLY_14B
        task_2 = {
            "privacy_level": "STRICT_LOCAL",
            "estimated_lines": 50,
            "task_type": "EXTRACTION",
            "attempt_count": 0,
            "max_attempts": 3,
        }
        assert router.route_task(task_2, api_quotas, online_mode=True) == "NIGHTLY_14B"

        # Cenário 3: REFACTOR -> NIGHTLY_14B
        task_3 = {
            "privacy_level": "PUBLIC",
            "estimated_lines": 40,
            "task_type": "REFACTOR",
            "attempt_count": 0,
            "max_attempts": 3,
        }
        assert router.route_task(task_3, api_quotas, online_mode=True) == "NIGHTLY_14B"

        # Cenário 4: PUBLIC complexa (> 30 linhas) online -> CLOUD_API:groq
        task_4 = {
            "privacy_level": "PUBLIC",
            "estimated_lines": 60,
            "task_type": "PARSER_OSINT",
            "attempt_count": 0,
            "max_attempts": 3,
        }
        assert router.route_task(task_4, api_quotas, online_mode=True) == "CLOUD_API:groq"

    def test_03_pull_with_lease_and_expiration(self, tmp_path):
        db_file = tmp_path / "test_jobs.db"
        router = PCIRouterAndBroker(db_path=db_file)

        # Insere nó e tarefa elegível
        with router._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO nodes (node_id, node_name, role, ram_total_mb, vram_total_mb, ssd_free_gb, supported_models, status)
                VALUES ('DONOR_NODE_01', 'DONOR_PC', 'DONOR', 8000, 4000, 50.0, '["qwen2.5-coder:3b"]', 'ONLINE')
                """
            )
            conn.execute(
                """
                INSERT INTO tasks_queue (
                    task_id, parent_masterbook, task_type, privacy_level,
                    estimated_lines, spec_markdown, test_code, target_file_path, status
                ) VALUES (
                    'TASK_LEASE_01', 'MB_PILOT', 'EXTRACTION', 'PUBLIC',
                    25, 'Spec content', 'def test_dummy(): pass', 'src/sample.py', 'PENDING'
                )
                """
            )
            conn.commit()

        # Doador faz pull com lease
        leased = router.lease_task_for_donor(donor_node_id="DONOR_NODE_01", lease_minutes=30)
        assert leased is not None
        assert leased["task_id"] == "TASK_LEASE_01"

        with router._get_conn() as conn:
            row = conn.execute("SELECT status, leased_by_node FROM tasks_queue WHERE task_id = 'TASK_LEASE_01'").fetchone()
            assert row["status"] == "LEASED"
            assert row["leased_by_node"] == "DONOR_NODE_01"

        # Força expiração do lease simulando data passada
        with router._get_conn() as conn:
            conn.execute("UPDATE tasks_queue SET lease_expires_at = '2000-01-01T00:00:00' WHERE task_id = 'TASK_LEASE_01'")
            conn.commit()

        reclaimed = router.reclaim_expired_leases()
        assert reclaimed == 1

        with router._get_conn() as conn:
            row = conn.execute("SELECT status, attempt_count, last_error_log FROM tasks_queue WHERE task_id = 'TASK_LEASE_01'").fetchone()
            assert row["status"] == "PENDING"
            assert row["attempt_count"] == 1
            assert "Timeout de Lease" in row["last_error_log"]

    def test_04_verification_and_kudos_ledger(self, tmp_path):
        db_file = tmp_path / "test_jobs.db"
        router = PCIRouterAndBroker(db_path=db_file)

        # Insere nó doador e tarefa
        with router._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO nodes (node_id, node_name, role, ram_total_mb, vram_total_mb, ssd_free_gb, supported_models, status)
                VALUES ('DONOR_02', 'PC_DOADOR', 'DONOR', 16000, 8000, 200.0, '["qwen2.5-coder:3b"]', 'ONLINE')
                """
            )
            conn.execute(
                """
                INSERT INTO tasks_queue (
                    task_id, parent_masterbook, task_type, privacy_level,
                    estimated_lines, spec_markdown, test_code, target_file_path, status, leased_by_node
                ) VALUES (
                    'TASK_VERIFY_01', 'MB_PILOT', 'EXTRACTION', 'PUBLIC',
                    20, 'Spec content', 'def test_soma():\n    from sample import soma\n    assert soma(2, 3) == 5\n', 'sample.py', 'LEASED', 'DONOR_02'
                )
                """
            )
            conn.commit()

        # Código candidato válido que satisfaz o teste
        candidate_code = "def soma(a, b):\n    return a + b\n"
        success = router.verify_and_integrate_submission(
            task_id="TASK_VERIFY_01",
            node_id="DONOR_02",
            candidate_code=candidate_code,
            compute_sec=4.0,
        )
        assert success is True

        with router._get_conn() as conn:
            task = conn.execute("SELECT status, candidate_patch_diff FROM tasks_queue WHERE task_id = 'TASK_VERIFY_01'").fetchone()
            assert task["status"] == "VERIFIED"
            assert task["candidate_patch_diff"] == candidate_code

            # Kudos: 20 * 0.5 + 4.0 * 0.05 = 10.0 + 0.20 = 10.2
            node = conn.execute("SELECT kudos_balance FROM nodes WHERE node_id = 'DONOR_02'").fetchone()
            assert node["kudos_balance"] == 10.2

            tx = conn.execute("SELECT * FROM kudos_ledger WHERE task_id = 'TASK_VERIFY_01'").fetchone()
            assert tx["kudos_awarded"] == 10.2
            assert tx["compute_seconds"] == 4.0

    def test_05_forensic_sanitizer(self, tmp_path):
        sanitizer = ForensicSanitizer()

        source_file = tmp_path / "raw_evidence.json"
        source_content = (
            '{\n'
            '  "cpf": "123.456.789-00",\n'
            '  "cnpj": "12.345.678/0001-90",\n'
            '  "email": "auditor@orgao.gov.br",\n'
            '  "ip": "192.168.1.100",\n'
            '  "local_path": "C:\\\\Users\\\\ACER\\\\Documents\\\\doc.pdf"\n'
            '}'
        )
        source_file.write_text(source_content, encoding="utf-8")

        res = sanitizer.create_sanitized_derivative(
            original_path=source_file,
            output_dir=tmp_path / "sanitized",
            strip_metadata_switch=True,
        )

        assert res["original_sha256"] != res["derivative_sha256"]
        assert res["metadata_stripped"] is True
        assert "CPF" in res["stripped_categories"]
        assert "CNPJ" in res["stripped_categories"]
        assert "EMAIL" in res["stripped_categories"]

        cleaned_text = Path(res["derivative_path"]).read_text(encoding="utf-8")
        assert "123.456.789-00" not in cleaned_text
        assert "<REDACTED_CPF>" in cleaned_text
        assert "<REDACTED_CNPJ>" in cleaned_text
        assert "<REDACTED_EMAIL>" in cleaned_text

    def test_06_orchestrator_dag_dependencies(self, tmp_path):
        db_file = tmp_path / "test_jobs.db"
        orchestrator = MasterbookOrchestrator(db_path=db_file)

        # Enfileira Tarefa Pai
        parent = MicrotaskDAGNode(
            task_id="TASK_PARENT",
            parent_masterbook="MB_PILOT",
            depends_on_task_id=None,
            task_type="EXTRACTION",
            privacy_level="PUBLIC",
            permission_level=2,
            estimated_lines=20,
            spec_markdown="Parent spec",
            test_code="def test_p(): pass",
            target_file_path="src/parent.py",
        )
        orchestrator.enqueue_microtask(parent)

        # Enfileira Tarefa Filha que depende do Pai
        child = MicrotaskDAGNode(
            task_id="TASK_CHILD",
            parent_masterbook="MB_PILOT",
            depends_on_task_id="TASK_PARENT",
            task_type="CRUD",
            privacy_level="PUBLIC",
            permission_level=2,
            estimated_lines=25,
            spec_markdown="Child spec",
            test_code="def test_c(): pass",
            target_file_path="src/child.py",
        )
        orchestrator.enqueue_microtask(child)

        # Inicialmente, Child está WAITING_DEPS
        with orchestrator.db.get_connection() as conn:
            c_row = conn.execute("SELECT status FROM tasks_queue WHERE task_id = 'TASK_CHILD'").fetchone()
            assert c_row["status"] == "WAITING_DEPS"

        # Simula conclusão e aprovação do Pai
        with orchestrator.db.get_connection() as conn:
            conn.execute("UPDATE tasks_queue SET status = 'VERIFIED' WHERE task_id = 'TASK_PARENT'")
            conn.commit()

        # Atualiza DAG
        promoted = orchestrator.update_dag_dependencies()
        assert promoted == 1

        with orchestrator.db.get_connection() as conn:
            c_row = conn.execute("SELECT status FROM tasks_queue WHERE task_id = 'TASK_CHILD'").fetchone()
            assert c_row["status"] == "PENDING"
