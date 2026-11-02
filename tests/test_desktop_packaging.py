"""
Testes unitários e de integração para empacotamento, ícone e atalho desktop.
Valida geração de ICO/PNG, criação de atalho Windows e script de inicialização silenciosa.
"""

import os
import subprocess
from pathlib import Path
from PIL import Image

import pytest
from assets.generate_icon import generate_pci_icon
from install_desktop_app import get_windows_desktop, create_desktop_shortcut


def test_pci_icon_generation(tmp_path: Path):
    """Verifica que o gerador de ícones cria um ICO válido multi-resolução e PNG."""
    ico_file = generate_pci_icon(output_dir=tmp_path)
    assert ico_file.is_file()
    assert ico_file.name == "pci_icon.ico"
    assert ico_file.stat().st_size > 1000

    png_file = tmp_path / "pci_icon.png"
    assert png_file.is_file()

    with Image.open(ico_file) as img:
        assert img.format == "ICO"
        assert img.size[0] > 0

    with Image.open(png_file) as img:
        assert img.format == "PNG"
        assert img.size == (256, 256)


def test_launch_pci_bat_exists_and_configured():
    """Valida que o arquivo launch_pci.bat existe na raiz do repositório e contém o launcher silencioso."""
    repo_root = Path("C:/Lab/013 - Sovereign-Code-Engine")
    bat_file = repo_root / "launch_pci.bat"
    assert bat_file.is_file(), "launch_pci.bat deve existir na raiz do repositório"

    content = bat_file.read_text(encoding="utf-8")
    assert "pythonw" in content, "launch_pci.bat deve usar pythonw para execução silenciosa"
    assert "gui_app.py" in content, "launch_pci.bat deve apontar para o gui_app.py"


def test_create_desktop_shortcut_in_temp_directory(tmp_path: Path, monkeypatch):
    """Valida a criação do atalho .lnk via PowerShell WScript.Shell em pasta simulada."""
    # Simula pasta Desktop apontando para tmp_path
    monkeypatch.setattr("install_desktop_app.get_windows_desktop", lambda: tmp_path)

    repo_root = Path("C:/Lab/013 - Sovereign-Code-Engine")
    shortcut_path = create_desktop_shortcut(
        repo_root=repo_root,
        shortcut_name="Test_PCI_Shortcut.lnk",
    )

    assert shortcut_path.is_file()
    assert shortcut_path.name == "Test_PCI_Shortcut.lnk"
    assert shortcut_path.stat().st_size > 500
