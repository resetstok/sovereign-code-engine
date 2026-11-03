"""
Módulo: runtime_utils.py
Utilitários de runtime para execução soberana em ambientes de desenvolvimento e PyInstaller (.EXE congelado).
Garante que subprocessos chamem o interpretador Python real (e não o executável da GUI)
e que janelas de console indesejadas fiquem permanentemente suprimidas no Windows.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def get_subprocess_creation_flags() -> int:
    """
    Retorna as flags necessárias para executar subprocessos sem criar janelas
    pretas no Windows (CREATE_NO_WINDOW = 0x08000000).
    """
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def get_python_runtime() -> str:
    """
    Retorna o caminho absoluto para o interpretador Python real no host.

    CRÍTICO PARA PYINSTALLER:
    Quando a aplicação está empacotada em dist/PCI_Master_System/PCI_Master_System.exe,
    sys.executable aponta para PCI_Master_System.exe. Se um subprocesso chamar
    sys.executable, ele abrirá UMA NOVA INSTÂNCIA DA GUI repetidamente em vez de rodar o Python!
    Esta função garante que o executável real do Python seja retornado em qualquer cenário.
    """
    # 1. Se estiver rodando como script Python direto (não-congelado)
    if not getattr(sys, "frozen", False):
        exe_lower = sys.executable.lower()
        if "python" in exe_lower and not exe_lower.endswith("pci_master_system.exe"):
            return sys.executable

    # 2. Caminhos prioritários de ambientes virtuais locais no ambiente Windows
    candidates = [
        r"C:\Lab\012 - Auditoria (SESAU)\.venv\Scripts\python.exe",
        r"C:\Lab\013 - Sovereign-Code-Engine\.venv\Scripts\python.exe",
    ]

    # Verifica se há VIRTUAL_ENV nas variáveis de ambiente
    venv_env = os.environ.get("VIRTUAL_ENV")
    if venv_env:
        candidates.insert(0, str(Path(venv_env) / "Scripts" / "python.exe"))

    # Verifica candidates
    for cand in candidates:
        if Path(cand).is_file():
            return str(Path(cand).resolve())

    # 3. Busca 'python' ou 'py' no PATH do sistema
    which_py = shutil.which("python") or shutil.which("python3") or shutil.which("py")
    if which_py:
        # Garante que não é o próprio executável congelado se tiver sido renomeado
        if not which_py.lower().endswith("pci_master_system.exe"):
            return which_py

    # 4. Fallback padrão
    return "python"


def get_real_rclone_binary() -> str:
    """
    Retorna o executável nativo do rclone.exe.
    No Windows com Chocolatey, 'C:\\ProgramData\\chocolatey\\bin\\rclone.exe' é apenas
    um shim de 47KB que abre uma janela de console preta vazia.
    Esta função localiza o binário nativo de ~85MB dentro de 'tools/rclone-...'
    para execução 100% silenciosa sem janelas de terminal piscando.
    """
    choco_lib = Path(r"C:\ProgramData\chocolatey\lib")
    if choco_lib.exists():
        for rclone_match in choco_lib.glob("**/rclone.exe"):
            # O binário nativo tem mais de 20 MB; o shim tem < 100 KB
            try:
                if rclone_match.is_file() and rclone_match.stat().st_size > 5_000_000:
                    return str(rclone_match.resolve())
            except OSError:
                continue

    # Fallback para o executável no PATH
    which_rclone = shutil.which("rclone")
    if which_rclone:
        return which_rclone

    return "rclone"
