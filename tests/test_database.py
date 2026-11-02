"""
Testes de Fundação: Validação do Banco de Dados SQLite em modo WAL e das 5 tabelas (Etapa 1).
"""

import sqlite3
from pathlib import Path
import pytest

from pci_engine.database import DatabaseManager


class TestDatabaseFoundation:

    def test_01_wal_mode_and_foreign_keys_enabled(self, tmp_path):
        db_path = tmp_path / "test_foundation_jobs.db"
        db = DatabaseManager(db_path=db_path)

        with db.get_connection() as conn:
            # 1. Modo WAL ativo
            journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert journal.upper() == "WAL"

            # 2. Chaves estrangeiras ativas
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            assert fk == 1

            # 3. As 5 tabelas foram criadas
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
            ]
            expected_tables = {
                "nodes",
                "capability_matrix",
                "tasks_queue",
                "kudos_ledger",
            }
            assert expected_tables.issubset(set(tables))

    def test_02_crud_nodes(self, tmp_path):
        db_path = tmp_path / "test_nodes.db"
        db = DatabaseManager(db_path=db_path)

        # Inserção
        db.register_node(
            node_id="NODE_MASTER_01",
            node_name="PC_MASTER",
            role="MASTER",
            ram_total_mb=8192,
            vram_total_mb=4096,
            ssd_free_gb=120.5,
            supported_models=["qwen2.5-coder:3b", "ollama_local"],
            status="ONLINE",
        )

        node = db.get_node("NODE_MASTER_01")
        assert node is not None
        assert node["node_name"] == "PC_MASTER"
        assert node["role"] == "MASTER"
        assert node["kudos_balance"] == 0.0
        assert "qwen2.5-coder:3b" in node["supported_models"]

        # Atualização idempotente via ON CONFLICT
        db.register_node(
            node_id="NODE_MASTER_01",
            node_name="PC_MASTER_RENAMED",
            role="MASTER",
            ram_total_mb=8192,
            vram_total_mb=4096,
            ssd_free_gb=110.0,
            supported_models=["qwen2.5-coder:3b"],
            status="BUSY",
        )
        updated = db.get_node("NODE_MASTER_01")
        assert updated["node_name"] == "PC_MASTER_RENAMED"
        assert updated["status"] == "BUSY"

    def test_03_crud_capability_matrix(self, tmp_path):
        db_path = tmp_path / "test_cap.db"
        db = DatabaseManager(db_path=db_path)

        # Cria nó primeiro (chave estrangeira)
        db.register_node(
            node_id="NODE_DONOR_01",
            node_name="DOADOR_ALFA",
            role="DONOR",
            ram_total_mb=16384,
            vram_total_mb=8192,
            ssd_free_gb=250.0,
            supported_models=["qwen2.5-coder:3b"],
        )

        # Registra métrica de capacidade
        db.record_capability(
            node_id="NODE_DONOR_01",
            model_name="qwen2.5-coder:3b",
            task_type="EXTRACTION",
            sample_size=25,
            success_rate=0.92,
            avg_tokens_per_sec=42.5,
            verification_state="VERIFIED",
            benchmark_version="v2.0",
        )

        cap = db.get_capability("NODE_DONOR_01", "qwen2.5-coder:3b", "EXTRACTION")
        assert cap is not None
        assert cap["success_rate"] == 0.92
        assert cap["sample_size"] == 25
        assert cap["verification_state"] == "VERIFIED"

    def test_04_crud_tasks_queue_and_status(self, tmp_path):
        db_path = tmp_path / "test_tasks.db"
        db = DatabaseManager(db_path=db_path)

        # Inserção de tarefa
        task_id = db.insert_task(
            task_id="TASK_GEO_001",
            parent_masterbook="MB_PILOTO",
            task_type="EXTRACTION",
            privacy_level="PUBLIC",
            estimated_lines=22,
            spec_markdown="### Extrair coordenadas de JSON",
            test_code="def test_geo(): assert True",
            target_file_path="src/geo_extractor.py",
            status="PENDING",
        )
        assert task_id == "TASK_GEO_001"

        t = db.get_task("TASK_GEO_001")
        assert t is not None
        assert t["status"] == "PENDING"
        assert t["estimated_lines"] == 22

        # Atualização de status e atribuição
        db.update_task_status(
            task_id="TASK_GEO_001",
            status="LEASED",
            assigned_route="DONOR_POOL",
            lease_expires_at="2026-11-02T12:00:00",
        )
        updated = db.get_task("TASK_GEO_001")
        assert updated["status"] == "LEASED"
        assert updated["assigned_route"] == "DONOR_POOL"

    def test_05_crud_kudos_ledger(self, tmp_path):
        db_path = tmp_path / "test_kudos.db"
        db = DatabaseManager(db_path=db_path)

        db.register_node(
            node_id="NODE_DONOR_02",
            node_name="DOADOR_BETA",
            role="DONOR",
            ram_total_mb=8192,
            vram_total_mb=4096,
            ssd_free_gb=50.0,
            supported_models=["qwen2.5-coder:3b"],
        )

        db.insert_task(
            task_id="TASK_KUDOS_01",
            parent_masterbook="MB_PILOTO",
            task_type="CRUD",
            privacy_level="PUBLIC",
            estimated_lines=30,
            spec_markdown="Spec",
            test_code="def test_ok(): pass",
            target_file_path="src/crud.py",
        )

        tx_id = db.record_kudos(
            node_id="NODE_DONOR_02",
            task_id="TASK_KUDOS_01",
            kudos_awarded=15.5,
            compute_seconds=3.2,
        )
        assert tx_id > 0

        # Verifica saldo atualizado no nó
        node = db.get_node("NODE_DONOR_02")
        assert node["kudos_balance"] == 15.5

        # Verifica lançamento no extrato
        ledger = db.get_kudos_ledger_for_node("NODE_DONOR_02")
        assert len(ledger) == 1
        assert ledger[0]["kudos_awarded"] == 15.5
        assert ledger[0]["compute_seconds"] == 3.2

    def test_06_database_schema_purity(self, tmp_path):
        """Valida que o schema da IDE de programacao contem apenas as 4 tabelas de operacao."""
        db_path = tmp_path / "test_purity.db"
        db = DatabaseManager(db_path=db_path)
        with db.get_connection() as conn:
            tables = [
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            ]
            assert "evidence_manifest" not in tables
            assert "tasks_queue" in tables
            assert "nodes" in tables
            assert "capability_matrix" in tables
            assert "kudos_ledger" in tables

    def test_07_foreign_key_violation_rejected(self, tmp_path):
        db_path = tmp_path / "test_fk.db"
        db = DatabaseManager(db_path=db_path)

        # Tentar registrar métrica para nó inexistente deve disparar IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            db.record_capability(
                node_id="NODE_INEXISTENTE",
                model_name="qwen2.5-coder:3b",
                task_type="EXTRACTION",
                sample_size=10,
                success_rate=0.8,
            )
