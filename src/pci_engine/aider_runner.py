"""
Módulo: pci_engine/aider_runner.py
Sub-Executor Supervisionado do Aider para o Sovereign Code Engine.
Permite executar o Aider CLI em modo subprocesso controlado:
1. Modo Architect / Ask: Planejamento puro sem alteração de arquivos no disco.
2. Modo Code com Staging: Geração cirúrgica com verificação do Pytest.
3. Respeito rigoroso aos portões de segurança: auto-commits desligados e isolamento de diff.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("pci_engine.aider_runner")


class AiderRunner:
    """Gerenciador e executor supervisionado do Aider CLI."""

    def __init__(self, repo_root: Optional[Path | str] = None) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path(".").resolve()
        self._aider_cmd = self._find_aider_binary()

    @staticmethod
    def _find_aider_binary() -> str:
        """Localiza o binário do Aider no PATH ou no ambiente Python."""
        found = shutil.which("aider")
        if found:
            return found
        # Fallback para o script do Python ativo
        python_scripts = Path(sys.executable).parent / "Scripts" / "aider.exe"
        if python_scripts.exists():
            return str(python_scripts)
        return "aider"

    def is_available(self) -> bool:
        """Verifica se o Aider está instalado e executável no sistema."""
        try:
            res = subprocess.run([self._aider_cmd, "--version"], capture_output=True, text=True, timeout=5)
            return res.returncode == 0
        except Exception:
            return False

    def build_command(
        self,
        files: List[str | Path],
        message: str,
        mode: str = "architect",  # 'architect', 'ask' ou 'code'
        model: Optional[str] = None,
        auto_test: bool = True,
        test_cmd: str = "pytest -q --tb=short",
    ) -> List[str]:
        """
        Monta o comando CLI do Aider com as flags de contenção de contexto e segurança:
        - --no-auto-commits: O Aider NÃO commita no Git sem aprovação do operador.
        - --no-dirty-commits: Não toca em arquivos não commitados.
        - map-tokens 0: Evita estourar a VRAM de 4GB / RAM de 8GB.
        """
        cmd = [
            self._aider_cmd,
            "--no-auto-commits",
            "--no-dirty-commits",
            "--map-tokens", "0",
            "--encoding", "utf-8",
        ]

        if model:
            cmd.extend(["--model", model])
        else:
            cmd.extend(["--model", "ollama/huihui_ai/qwen2.5-coder-abliterate:3b"])

        if mode == "architect":
            cmd.append("--architect")
        elif mode == "ask":
            cmd.append("--chat-mode")
            cmd.append("ask")

        if auto_test:
            cmd.extend(["--auto-test", "--test-cmd", test_cmd])

        # Adiciona arquivos explicitamente (adição cirúrgica /add)
        for f in files:
            cmd.append(str(f))

        # Adiciona o comando/prompt
        if message:
            cmd.extend(["--message", message])

        return cmd

    def run_architect_planning(
        self,
        files: List[str | Path],
        prompt: str,
        model: Optional[str] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Executa o modo /architect do Aider para obter a decomposição sem alterar código."""
        cmd = self.build_command(files=files, message=prompt, mode="architect", model=model)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="ignore",
            )
            return {
                "success": proc.returncode == 0,
                "output": proc.stdout or proc.stderr,
                "mode": "architect",
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout após {timeout}s", "mode": "architect"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "mode": "architect"}

    def run_surgical_code(
        self,
        files: List[str | Path],
        instruction: str,
        model: Optional[str] = None,
        timeout: int = 180,
    ) -> Dict[str, Any]:
        """Executa alteração cirúrgica com o Aider sem auto-commit."""
        cmd = self.build_command(files=files, message=instruction, mode="code", model=model)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="ignore",
            )
            return {
                "success": proc.returncode == 0,
                "output": proc.stdout or proc.stderr,
                "mode": "code",
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout após {timeout}s", "mode": "code"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "mode": "code"}
