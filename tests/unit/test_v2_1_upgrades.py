"""
Testes das atualizações v2.1: MicroHarness, Invariantes Runtime, Git Worktree e Gramáticas GBNF.
"""

from pathlib import Path
import pytest

from pci_engine.micro_harness import MicroHarness, SYSTEM_PROMPT
from pci_engine.router import PCIRouterAndBroker
from pci_engine.orchestrator import (
    MasterbookOrchestrator,
    MasterbookDecompositionSchema,
    MicrotaskSpec,
)


class TestV21Upgrades:

    def test_01_micro_harness_invariants_and_compact_prompt(self, tmp_path):
        # 1. Valida tamanho do system prompt (< 1.000 tokens / ~2.000 chars)
        assert len(SYSTEM_PROMPT.split()) < 200
        assert len(SYSTEM_PROMPT) < 1500

        harness = MicroHarness(repo_root=tmp_path)

        # Cria arquivo inicial no repositório
        sample_file = tmp_path / "modulo.py"
        sample_file.write_text("def antigo(): return 1\n", encoding="utf-8")

        # Invariante 1: Tentar editar SEM LER ANTES deve ser bloqueado no código
        edit_result = harness.tool_edit("modulo.py", "return 1", "return 2")
        assert "INVARIANT_VIOLATION" in edit_result
        assert "Proibido editar" in edit_result

        # Invariante 2: Tentar sobrescrever arquivo existente SEM LER ANTES deve ser bloqueado
        write_result = harness.tool_write("modulo.py", "def novo(): return 3\n")
        assert "INVARIANT_VIOLATION" in write_result

        # Agora lê o arquivo
        read_content = harness.tool_read("modulo.py")
        assert "def antigo" in read_content

        # Agora a edição e escrita devem passar com sucesso
        edit_ok = harness.tool_edit("modulo.py", "return 1", "return 2")
        assert "OK: Edição aplicada" in edit_ok
        assert "return 2" in sample_file.read_text(encoding="utf-8")

    def test_02_micro_harness_shell_log_truncation(self, tmp_path):
        harness = MicroHarness(repo_root=tmp_path)
        # Executa comando e verifica truncamento e exit_code
        res = harness.tool_bash("python -c \"print('Linha ' * 50)\"")
        assert "[EXIT_CODE: 0]" in res
        assert len(res) <= 700

    def test_03_git_worktree_sandbox_lifecycle(self, tmp_path):
        # Cria um router apontando para o repositório atual que é um repositório git real
        repo_root = Path("C:/Lab/013 - Sovereign-Code-Engine").resolve()
        db_path = tmp_path / "test_wt_jobs.db"
        router = PCIRouterAndBroker(db_path=db_path, repo_root=repo_root)

        candidate_code = "def subtrair(a, b): return a - b\n"
        test_code = "from src.dummy_calc import subtrair\ndef test_sub(): assert subtrair(10, 4) == 6\n"

        success, stdout, stderr = router._run_in_worktree_or_fallback(
            task_id="WT_TEST_001",
            target_file_path="src/dummy_calc.py",
            candidate_code=candidate_code,
            test_code=test_code,
        )

        assert success is True
        # Garante que a pasta temporária de worktree foi apagada (cleanup)
        sandbox_dir = repo_root / "data" / "sandboxes" / "sandbox_WT_TEST_001"
        assert not sandbox_dir.exists()

    def test_04_gbnf_pydantic_schema_validation(self):
        # Validação do Schema Pydantic exigido para forçar gramática GBNF
        json_schema = MasterbookDecompositionSchema.model_json_schema()
        assert "properties" in json_schema
        assert "tasks" in json_schema["properties"]

        # Validação de payload gerado
        sample_payload = """
        {
            "parent_masterbook": "MB_GEO",
            "tasks": [
                {
                    "task_id": "TASK_001",
                    "depends_on_task_id": null,
                    "task_type": "EXTRACTION",
                    "privacy_level": "PUBLIC",
                    "permission_level": 2,
                    "estimated_lines": 20,
                    "target_file_path": "src/geo.py",
                    "spec_markdown": "Extrai lat/long",
                    "test_code": "def test_geo(): assert True"
                }
            ]
        }
        """
        parsed = MasterbookDecompositionSchema.model_validate_json(sample_payload)
        assert parsed.parent_masterbook == "MB_GEO"
        assert len(parsed.tasks) == 1
        assert parsed.tasks[0].task_id == "TASK_001"
