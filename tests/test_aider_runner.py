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
