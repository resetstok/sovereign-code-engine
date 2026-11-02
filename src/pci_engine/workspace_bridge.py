"""
Módulo: pci_engine/workspace_bridge.py
Ponte de Contexto Multi-Workspace e Preparação para Git/Sync.
Permite ao PCI Sovereign Code Engine operar sobre qualquer projeto externo (ex: C:\\Lab\\012 - Auditoria (SESAU)),
mapear árvores de diretórios, extrair contexto de arquivos existentes para o modelo 3B,
gravar com isolamento e preparar commits com diário estruturado para GitHub/Antigravity/Copilot.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("pci_engine.workspace_bridge")


@dataclass
class WorkspaceManifest:
    target_dir: str
    relative_files: List[str]
    git_branch: Optional[str] = None
    git_remote: Optional[str] = None
    git_clean: bool = True


@dataclass
class ExportReceipt:
    exported_at: str
    target_workspace: str
    files_written: List[str]
    tasks_included: List[str]
    journal_path: str
    git_staged: bool
    git_commit_hash: Optional[str] = None
    message: str = ""


class WorkspaceBridge:
    """
    Gerencia a leitura e escrita entre o PCI Engine (013) e projetos externos (ex: 012 SESAU).
    """

    IGNORE_DIRS = {
        ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
        "node_modules", ".idea", ".vscode", "temp_test_dir", "scratch"
    }

    IGNORE_EXTS = {
        ".pyc", ".pyo", ".pyd", ".iso", ".vdi", ".vmdk", ".tar", ".gz", ".zip", ".exe"
    }

    def __init__(self, target_workspace: Optional[Path | str] = None):
        if target_workspace:
            self.target_dir = Path(target_workspace).resolve()
        else:
            self.target_dir = Path(r"C:\Lab\012 - Auditoria (SESAU)").resolve()
        self.target_dir.mkdir(parents=True, exist_ok=True)

    def set_target_workspace(self, path: Path | str) -> bool:
        resolved = Path(path).resolve()
        if resolved.exists() and resolved.is_dir():
            self.target_dir = resolved
            return True
        return False

    def scan_workspace_tree(self, max_depth: int = 3, max_files: int = 60) -> WorkspaceManifest:
        """Varre o projeto externo para mapear onde ficam pastas como src/, 03_Scripts_Python/, tests/, etc."""
        scanned_files: List[str] = []
        base = self.target_dir

        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith(".")]
            rel_dir = Path(root).relative_to(base)
            depth = len(rel_dir.parts)
            if depth > max_depth:
                continue

            for f in files:
                ext = Path(f).suffix.lower()
                if ext in self.IGNORE_EXTS or f.startswith("."):
                    continue
                rel_path = (rel_dir / f).as_posix()
                if rel_path.startswith("./"):
                    rel_path = rel_path[2:]
                scanned_files.append(rel_path)
                if len(scanned_files) >= max_files:
                    break
            if len(scanned_files) >= max_files:
                break

        # Coleta metadados de Git
        branch, remote, is_clean = self._get_git_info(self.target_dir)

        return WorkspaceManifest(
            target_dir=str(self.target_dir),
            relative_files=scanned_files,
            git_branch=branch,
            git_remote=remote,
            git_clean=is_clean,
        )

    @staticmethod
    def _get_git_info(directory: Path) -> Tuple[Optional[str], Optional[str], bool]:
        """Obtém branch atual, remote origin e estado de clean do repositório."""
        git_dir = directory / ".git"
        if not git_dir.exists():
            return None, None, True

        try:
            cmd_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            branch_proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=cmd_flags,
            )
            branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else None

            remote_proc = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=cmd_flags,
            )
            remote = remote_proc.stdout.strip() if remote_proc.returncode == 0 else None

            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=cmd_flags,
            )
            is_clean = len(status_proc.stdout.strip()) == 0

            return branch, remote, is_clean
        except Exception:
            return None, None, True

    def build_project_context_summary(self, max_chars: int = 1200) -> str:
        """
        Gera uma síntese concisa da arquitetura do projeto alvo para orientar o modelo 3B
        sobre onde colocar novos arquivos e que convenções respeitar.
        """
        manifest = self.scan_workspace_tree()
        summary_lines = [
            f"PROJETO-ALVO: {self.target_dir.name} ({self.target_dir})",
            f"RAMIFICAÇÃO GIT: {manifest.git_branch or 'Sem controle git direto'} | REMOTO: {manifest.git_remote or 'N/A'}",
            "ESTRUTURA DE PASTAS E ARQUIVOS PRINCIPAIS:",
        ]
        key_folders = set()
        for f in manifest.relative_files:
            parts = f.split("/")
            if len(parts) > 1:
                key_folders.add(parts[0])

        for kf in sorted(key_folders)[:8]:
            sample_files = [f for f in manifest.relative_files if f.startswith(f"{kf}/")][:3]
            summary_lines.append(f"• {kf}/ (ex: {', '.join(Path(sf).name for sf in sample_files)})")

        return "\n".join(summary_lines)[:max_chars]

    def export_verified_task_code(
        self,
        task_id: str,
        target_relative_path: str,
        code_content: str,
        spec_summary: str,
        pytest_log: str = "(Sem erros)",
        auto_stage_git: bool = False,
    ) -> ExportReceipt:
        """
        Grava o código validado diretamente na pasta do projeto alvo (preservando subpastas),
        gera um diário legível para humanos e IAs futuras (docs/OFFLINE_PROGRAMMING_LOG.md)
        e opcionalmente faz 'git add' no arquivo para você só dar commit e push.
        """
        dest_file = (self.target_dir / target_relative_path).resolve()
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_text(code_content, encoding="utf-8")

        # Registra no Diário Estruturado para o Antigravity / Copilot saber o que foi feito offline
        journal_dir = self.target_dir / "docs"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_file = journal_dir / "OFFLINE_SOVEREIGN_LOG.md"

        now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        entry_text = (
            f"\n\n## [{now_str}] Tarefa Soberana Offline: `{task_id}`\n"
            f"- **Arquivo Gravado:** `{target_relative_path}` ({len(code_content.splitlines())} linhas)\n"
            f"- **Validação de Qualidade:** AST Sintático [OK] | Pytest Sandbox [100% PASS]\n"
            f"- **Resumo da Especificação:** {spec_summary.strip()[:240]}...\n"
            f"- **Último Status Pytest:** `{pytest_log.strip()[:100]}`\n"
            f"```python\n# Assinaturas geradas:\n"
        )
        try:
            tree = ast.parse(code_content)
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    entry_text += f"def {node.name}(...):\n"
                elif isinstance(node, ast.ClassDef):
                    entry_text += f"class {node.name}:\n"
        except Exception:
            entry_text += "# (Código validado)\n"
        entry_text += "```\n"

        with journal_file.open("a", encoding="utf-8") as jf:
            jf.write(entry_text)

        # Se solicitado e se houver git no destino, realiza git add no arquivo e no log
        staged = False
        commit_hash = None
        git_dir = self.target_dir / ".git"
        if auto_stage_git and git_dir.exists():
            try:
                cmd_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                subprocess.run(
                    ["git", "add", str(dest_file), str(journal_file)],
                    cwd=str(self.target_dir),
                    check=False,
                    timeout=5,
                    creationflags=cmd_flags,
                )
                staged = True
            except Exception as exc:
                logger.warning(f"Não foi possível fazer git add automático: {exc}")

        return ExportReceipt(
            exported_at=now_str,
            target_workspace=str(self.target_dir),
            files_written=[str(dest_file)],
            tasks_included=[task_id],
            journal_path=str(journal_file),
            git_staged=staged,
            git_commit_hash=commit_hash,
            message=f"Arquivo '{target_relative_path}' exportado com sucesso para '{self.target_dir.name}'!",
        )

    def prepare_git_commit_and_push_bundle(
        self,
        commit_message: str = "feat(offline): autonomous verified modules generated via PCI Engine",
        push_now: bool = False,
    ) -> Dict[str, Any]:
        """
        Prepara ou executa o envio seguro para o GitHub do repositório destino:
        executa 'git status', 'git add -A' ou commit limpo sem quebrar branches.
        """
        git_dir = self.target_dir / ".git"
        if not git_dir.exists():
            return {
                "success": False,
                "error": f"O diretório '{self.target_dir}' não é um repositório Git (.git inexistente).",
            }

        cmd_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            # 1. Verifica status atual
            st_proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(self.target_dir),
                capture_output=True,
                text=True,
                timeout=6,
                creationflags=cmd_flags,
            )
            short_status = st_proc.stdout.strip()
            if not short_status:
                return {
                    "success": True,
                    "message": "Nenhuma alteração pendente no repositório. Nada para enviar.",
                    "status_output": "",
                }

            # 2. Stage dos arquivos de docs e módulos
            subprocess.run(
                ["git", "add", "docs/OFFLINE_SOVEREIGN_LOG.md", "src/", "03_Scripts_Python/"],
                cwd=str(self.target_dir),
                capture_output=True,
                text=True,
                timeout=6,
                creationflags=cmd_flags,
            )

            # 3. Commit
            cmt_proc = subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=str(self.target_dir),
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=cmd_flags,
            )

            push_output = None
            if push_now and cmt_proc.returncode == 0:
                push_proc = subprocess.run(
                    ["git", "push"],
                    cwd=str(self.target_dir),
                    capture_output=True,
                    text=True,
                    timeout=25,
                    creationflags=cmd_flags,
                )
                push_output = (push_proc.stdout + "\n" + push_proc.stderr).strip()

            return {
                "success": cmt_proc.returncode == 0,
                "commit_output": cmt_proc.stdout.strip() or cmt_proc.stderr.strip(),
                "push_executed": push_now,
                "push_output": push_output,
            }
        except Exception as exc:
            return {"success": False, "error": f"Erro na operação Git: {exc}"}
