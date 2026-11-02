"""
Módulo: install_desktop_app.py
Instalador do Atalho Oficial do PCI Master System v2.0 na Área de Trabalho do Windows.
Cria o atalho 'PCI Master System v2.0.lnk' com ícone Dark/Cyber personalizado,
diretório de trabalho configurado e inicialização silenciosa via launch_pci.bat.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def get_windows_desktop() -> Path:
    """Localiza a pasta da Área de Trabalho do usuário no Windows."""
    # 1. Tenta USERPROFILE / Desktop
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        desktop = Path(userprofile) / "Desktop"
        if desktop.exists():
            return desktop

    # 2. Fallback via expanduser
    expanded = Path(os.path.expanduser("~/Desktop"))
    if expanded.exists():
        return expanded

    # 3. Fallback para caminho direto padrão do Windows
    direct = Path("C:/Users/ACER/Desktop")
    if direct.exists():
        return direct

    # Se ainda assim não existir, cria o diretório Desktop no home do usuário
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop


def create_desktop_shortcut(
    repo_root: Path | str = "C:/Lab/013 - Sovereign-Code-Engine",
    shortcut_name: str = "PCI Master System v2.0.lnk",
) -> Path:
    """
    Cria ou atualiza o atalho .lnk na Área de Trabalho do Windows.
    Aponta para launch_pci.bat (ou PCI_Master_System.exe se compilado).
    """
    repo_path = Path(repo_root).resolve()
    desktop_dir = get_windows_desktop()
    shortcut_path = desktop_dir / shortcut_name

    # 1. Assegura que o ícone existe
    icon_path = repo_path / "assets" / "pci_icon.ico"
    if not icon_path.is_file():
        from assets.generate_icon import generate_pci_icon
        icon_path = generate_pci_icon(repo_path / "assets")

    # 2. Define o alvo de execução
    compiled_exe = repo_path / "dist" / "PCI_Master_System" / "PCI_Master_System.exe"
    launcher_bat = repo_path / "launch_pci.bat"

    if compiled_exe.is_file():
        target_path = compiled_exe
        description = "PCI Master System v2.0 (Sovereign Code Engine Standalone)"
    else:
        target_path = launcher_bat
        description = "PCI Master System v2.0 (Sovereign Code Engine Silent Launcher)"

    # 3. Executa o script PowerShell para criar o .lnk via WScript.Shell nativo do Windows
    ps_command = f"""
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut('{str(shortcut_path)}')
    $Shortcut.TargetPath = '{str(target_path)}'
    $Shortcut.WorkingDirectory = '{str(repo_path)}'
    $Shortcut.IconLocation = '{str(icon_path)},0'
    $Shortcut.Description = '{description}'
    $Shortcut.WindowStyle = 7  # 7 = Minimized / Silent execution
    $Shortcut.Save()
    """

    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_command],
        capture_output=True,
        text=True,
    )

    if res.returncode != 0:
        raise RuntimeError(f"Falha ao criar atalho no Windows: {res.stderr}")

    if not shortcut_path.exists():
        raise FileNotFoundError(f"Atalho não foi criado em {shortcut_path}")

    return shortcut_path


def main():
    print("=" * 70)
    print("INSTALADOR DE ATALHO DESKTOP - PCI MASTER SYSTEM v2.0")
    print("=" * 70)

    repo_dir = Path(__file__).resolve().parent
    try:
        shortcut = create_desktop_shortcut(repo_root=repo_dir)
        print(f"[+] SUCESSO: Atalho criado na Área de Trabalho:")
        print(f"    Caminho: {shortcut}")
        print(f"    Tamanho: {shortcut.stat().st_size} bytes")
        print(f"    Diretório de Trabalho: {repo_dir}")
        print("=" * 70)
    except Exception as exc:
        print(f"[-] ERRO na instalação do atalho: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
