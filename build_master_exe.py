"""
Módulo: build_master_exe.py
Script de Empacotamento do PCI Master System v2.0 com PyInstaller.
Gera o executável desktop standalone (.exe) sem console preto (--noconsole)
com ícone Dark/Cyber embutido e assets do CustomTkinter coletados.
"""

import os
import subprocess
import sys
from pathlib import Path


def build_executable():
    root_dir = Path(__file__).resolve().parent
    icon_path = root_dir / "assets" / "pci_icon.ico"
    entrypoint = root_dir / "src" / "pci_engine" / "gui_app.py"

    if not icon_path.exists():
        print("[!] Ícone não encontrado. Gerando novo ícone...")
        from assets.generate_icon import generate_pci_icon
        generate_pci_icon(root_dir / "assets")

    print(f"[*] Iniciando compilação do executável PCI Master System v2.0...")
    print(f"[*] Entrypoint: {entrypoint}")
    print(f"[*] Ícone: {icon_path}")

    # Separador de dados para PyInstaller no Windows é ';'
    sep = ";" if os.name == "nt" else ":"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconsole",
        "--onedir",
        "--name", "PCI_Master_System",
        f"--icon={str(icon_path)}",
        f"--paths={str(root_dir / 'src')}",
        f"--add-data={str(root_dir / 'config')}{sep}config",
        f"--add-data={str(root_dir / 'assets')}{sep}assets",
        "--collect-all", "customtkinter",
        "--hidden-import", "pci_engine",
        "--hidden-import", "pci_engine.mtv_core",
        "--hidden-import", "pci_engine.hard_wallet",
        "--hidden-import", "pci_engine.aider_runner",
        "--hidden-import", "pci_engine.context_guard",
        "--hidden-import", "pci_engine.sanitizer",
        "--hidden-import", "pci_engine.drive_sync",
        "--hidden-import", "pci_engine.broker_api",
        "--hidden-import", "pci_engine.broker_security",
        "--hidden-import", "pci_engine.router",
        "--hidden-import", "pci_engine.orchestrator",
        "--hidden-import", "pci_engine.database",
        "--hidden-import", "pci_engine.preflight",
        "--hidden-import", "cryptography",
        "--hidden-import", "satellite_map",
        "--clean",
        "-y",
        str(entrypoint),
    ]

    print(f"[*] Executando PyInstaller...")
    res = subprocess.run(cmd, cwd=str(root_dir), text=True)
    if res.returncode == 0:
        dist_exe = root_dir / "dist" / "PCI_Master_System" / "PCI_Master_System.exe"
        print(f"\n[+] Compilação concluída com sucesso!")
        print(f"[+] Executável gerado em: {dist_exe}")
        return dist_exe
    else:
        print(f"\n[-] Falha na compilação do executável (Código: {res.returncode}).")
        return None


if __name__ == "__main__":
    build_executable()
