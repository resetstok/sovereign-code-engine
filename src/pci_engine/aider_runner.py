"""
AiderRunner Avançado com suporte a Modo Turbo (Execução Completa) vs Modo Seguro (Request Review),
Diffs Coloridos em HTML/Console, terminal pseudo-interativo e injeção de tarefas mastigadas da fila SQLite.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class AiderRunner:
    """
    Controlador avançado do Aider para o Sovereign Code Engine.
    Suporta:
    - Modo 'Request Review' (seguro, sem auto-commit, diffs prévios).
    - Modo 'Turbo' (execução rápida com auto-commit aprovado, lint e testes contínuos).
    - Exibição de diffs side-by-side / coloridos.
    """

    def __init__(self, repo_root: Optional[Path | str] = None, model: Optional[str] = None):
        self.repo_root = Path(repo_root or '.').resolve()
        self.default_model = model or "ollama/qwen2.5-coder:3b"
        self._aider_cmd = self._find_aider_binary()

    def _find_aider_binary(self) -> str:
        found = shutil.which("aider")
        if found:
            return found
        py_scripts = Path(sys.executable).parent / "Scripts" / "aider.exe"
        if py_scripts.exists():
            return str(py_scripts)
        return "aider"

    def is_available(self) -> bool:
        try:
            res = subprocess.run([self._aider_cmd, "--version"], capture_output=True, text=True, timeout=5)
            return res.returncode == 0
        except Exception:
            return False

    def build_command(
        self,
        files: List[str | Path],
        message: str,
        mode: str = "architect",  # 'architect', 'ask', 'code'
        model: Optional[str] = None,
        turbo_mode: bool = False,
        auto_test: bool = True,
        test_cmd: str = "pytest -q --tb=short",
        show_diffs: bool = True,
    ) -> List[str]:
        """
        Monta o comando CLI do Aider:
        - Em Modo Turbo: --auto-commits (comita direto após passar nos testes), --yes-always.
        - Em Modo Seguro (Request Review): --no-auto-commits, --no-dirty-commits.
        """
        cmd = [
            self._aider_cmd,
            "--no-show-model-warnings",
            "--no-git-commit-verify",
            "--map-tokens", "0",
            "--encoding", "utf-8",
            "--exit",
        ]

        if turbo_mode:
            cmd.extend([
                "--auto-commits",
                "--yes-always",
            ])
        else:
            cmd.extend([
                "--no-auto-commits",
                "--no-dirty-commits",
            ])

        if show_diffs:
            cmd.append("--show-diffs")

        if model:
            cmd.extend(["--model", model])
        else:
            cmd.extend(["--model", self.default_model])

        if mode == "architect":
            cmd.append("--architect")
        elif mode == "ask":
            cmd.append("--chat-mode")
            cmd.append("ask")

        if auto_test:
            cmd.extend(["--auto-test", "--test-cmd", test_cmd])

        for f in files:
            cmd.append(str(f))

        if message:
            cmd.extend(["--message", message])

        return cmd

    def run_surgical_code(
        self,
        files: List[str | Path],
        instruction: str,
        model: Optional[str] = None,
        turbo_mode: bool = False,
        timeout: int = 180,
    ) -> Dict[str, Any]:
        """Executa alteração de código com o Aider (respeitando Modo Turbo ou Request Review)."""
        cmd = self.build_command(
            files=files,
            message=instruction,
            mode="code",
            model=model,
            turbo_mode=turbo_mode,
        )
        try:
            custom_env = os.environ.copy()
            custom_env["OLLAMA_API_BASE"] = "http://127.0.0.1:11434"
            custom_env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="ignore",
                env=custom_env,
            )
            return {
                "success": proc.returncode == 0,
                "output": proc.stdout or proc.stderr,
                "mode": "code",
                "turbo": turbo_mode,
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout após {timeout}s", "mode": "code"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "mode": "code"}

    def run_architect_planning(
        self,
        files: List[str | Path],
        prompt: str,
        model: Optional[str] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Executa planejamento via Aider /architect."""
        cmd = self.build_command(files=files, message=prompt, mode="architect", model=model, turbo_mode=False)
        try:
            custom_env = os.environ.copy()
            custom_env["OLLAMA_API_BASE"] = "http://127.0.0.1:11434"
            custom_env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="ignore",
                env=custom_env,
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

    @staticmethod
    def generate_side_by_side_diff(old_text: str, new_text: str, filename: str = "solution.py") -> str:
        """Gera diff unificado e legível para visualização de código."""
        diff_lines = list(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            n=3,
        ))
        return "".join(diff_lines)
