"""
Testes unitários do Sovereign Code Engine.
"""

from pathlib import Path
import pytest

from code_engine.durable_queue.db import JobsDatabase
from code_engine.durable_queue.guardian import ResourceGuardian
from code_engine.durable_queue.engine import DurableJobEngine
from code_engine.durable_queue.models import JobStatus
from code_engine.ram_liberator import RAMLiberator
from code_engine.orchestrator import CodeEngineOrchestrator


class TestSovereignCodeEngine:

    def test_01_ram_liberator_scan(self):
        ram = RAMLiberator.get_system_ram_mb()
        assert ram["total_mb"] > 0
        assert ram["available_mb"] > 0
        assert 0 <= ram["percent_used"] <= 100

        candidates, potential_mb = RAMLiberator.scan_releasable_processes()
        assert isinstance(candidates, list)
        assert potential_mb >= 0.0

    def test_02_durable_engine_lifecycle(self, tmp_path):
        db_path = tmp_path / "test_jobs.db"
        db = JobsDatabase(db_path=db_path)
        guardian = ResourceGuardian(target_path=tmp_path, min_disk_mb=10.0, min_ram_mb=10.0)
        engine = DurableJobEngine(db=db, guardian=guardian, worker_id="test-worker")

        executed_steps = []

        def dummy_handler(job, ctx):
            ctx.save_checkpoint(1, "STEP_ONE", {"done": True})
            executed_steps.append("STEP_ONE")
            return {"status": "SUCCESS"}

        engine.register_handler("TEST_TASK", dummy_handler)

        job, res = engine.submit_job("TEST_TASK", "ENTITY_1", {"key": "val"})
        assert job.status == JobStatus.PENDING
        assert res["action"] == "CREATED"

        processed = engine.run_once(max_jobs=1)
        assert processed == 1
        assert "STEP_ONE" in executed_steps

        updated_job = db.get_job(job.job_id)
        assert updated_job.status == JobStatus.COMPLETED

    def test_03_code_orchestrator_pipeline(self, tmp_path):
        db_path = tmp_path / "pipeline_jobs.db"
        orchestrator = CodeEngineOrchestrator(db_path=db_path, repo_root=tmp_path, min_ram_mb=10.0)

        job = orchestrator.submit_code_task(
            target_module="src/demo.py",
            instruction="Implemente soma atômica",
            action="CREATE",
            expected_tests=[],
        )
        assert job.status == JobStatus.PENDING

        processed = orchestrator.recover_and_run_once()
        assert processed == 1

        cps = orchestrator.jobs_db.get_checkpoints_for_job(job.job_id)
        step_names = [cp.step_name for cp in cps]
        assert "DECOMPOSICAO_CONCLUIDA" in step_names
        assert "CODIFICACAO_CONCLUIDA" in step_names
        assert "TESTES_APROVADOS" in step_names
