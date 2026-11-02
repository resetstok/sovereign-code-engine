"""
Testes unitários para o módulo AiderRunner (Sub-Executor Supervisionado).
"""

from pathlib import Path
from pci_engine.aider_runner import AiderRunner


def test_aider_runner_initialization():
    runner = AiderRunner()
    assert runner.repo_root is not None
    assert runner._aider_cmd is not None


def test_aider_build_command_security_flags():
    runner = AiderRunner()
    cmd = runner.build_command(
        files=["src/module.py"],
        message="Criar funcao de teste",
        mode="architect",
        model="ollama/qwen2.5-coder:3b",
    )
    # Garante que as flags de segurança contra auto-commit e mapa de tokens estão ativas
    assert "--no-auto-commits" in cmd
    assert "--no-dirty-commits" in cmd
    assert "--map-tokens" in cmd
    assert "0" in cmd
    assert "--architect" in cmd
    assert "src/module.py" in cmd
    assert "--message" in cmd
    assert "Criar funcao de teste" in cmd


def test_aider_is_available():
    runner = AiderRunner()
    # Verifica se detecta o aider já instalado no ambiente
    assert runner.is_available() is True
from pathlib import Path
from pci_engine.aider_runner import AiderRunner


def test_aider_turbo_mode_flags():
    runner = AiderRunner()
    cmd = runner.build_command(
        files=["src/main.py"],
        message="Add login function",
        mode="code",
        turbo_mode=True,
    )
    assert "--auto-commits" in cmd
    assert "--yes-always" in cmd
    assert "--no-auto-commits" not in cmd


def test_aider_side_by_side_diff_generation():
    old = "def hello():\n    return 'old'\n"
    new = "def hello():\n    return 'new'\n"
    diff = AiderRunner.generate_side_by_side_diff(old, new, "test.py")
    assert "-    return 'old'" in diff
    assert "+    return 'new'" in diff
