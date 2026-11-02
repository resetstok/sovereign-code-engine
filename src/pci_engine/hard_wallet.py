"""
Módulo de Gerenciamento da Hard Wallet (Pendrive USB / Chaveiro Físico Criptográfico).
Permite salvar e carregar chaves criptográficas AES-256 / senhas do operador diretamente
de unidades removíveis (Pendrive / Hard Wallet física).
"""

from __future__ import annotations

import ctypes
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class HardWalletManager:
    """
    Gerenciador de Hard Wallet e Chaveiros Criptográficos Removíveis.
    Procura chaves em dispositivos USB (DRIVE_REMOVABLE = 2 no Windows API)
    e suporta chaves gravadas em arquivos '.vault_key' ou '.operator.key'.
    """

    KEY_FILE_NAMES = (
        ".vault_key",
        ".operator.key",
        "sovereign_operator.key",
        "pci_operator.key",
    )
    DEFAULT_KEY_SUBDIR = ".sovereign_keys"

    @classmethod
    def list_available_removable_drives(cls) -> List[Path]:
        """
        Retorna lista de pontos de montagem correspondentes a unidades removíveis (USB / Pendrives).
        Utiliza GetDriveTypeW (código 2 = DRIVE_REMOVABLE) no Windows, com fallback seguro para psutil.
        """
        drives: List[Path] = []
        if os.name == "nt":
            for i in range(26):
                letter = chr(65 + i)
                drive_path = f"{letter}:\\"
                try:
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                    # 2 = DRIVE_REMOVABLE
                    if drive_type == 2:
                        p = Path(drive_path)
                        if p.exists():
                            drives.append(p)
                except Exception:
                    continue
        else:
            # Linux / macOS fallback: /media, /run/media, /Volumes
            for candidate in (Path("/media"), Path("/run/media"), Path("/Volumes")):
                if candidate.exists():
                    for sub in candidate.iterdir():
                        if sub.is_dir():
                            drives.append(sub)

        return drives

    @classmethod
    def detect_hard_wallet_key(cls, search_paths: Optional[List[Path]] = None) -> Optional[Tuple[str, Path]]:
        """
        Varre unidades removíveis em busca de chave de hard wallet.
        Retorna a tupla (chave_ou_senha_str, caminho_do_arquivo) se encontrada, ou None.
        """
        paths_to_scan = search_paths if search_paths is not None else cls.list_available_removable_drives()

        for drive in paths_to_scan:
            # 1. Procura na raiz do pendrive
            for fname in cls.KEY_FILE_NAMES:
                candidate = drive / fname
                if candidate.exists() and candidate.is_file():
                    content = cls._read_key_content(candidate)
                    if content:
                        return (content, candidate)

            # 2. Procura dentro de subpasta dedicada (.sovereign_keys)
            key_dir = drive / cls.DEFAULT_KEY_SUBDIR
            if key_dir.exists() and key_dir.is_dir():
                for fname in cls.KEY_FILE_NAMES:
                    candidate = key_dir / fname
                    if candidate.exists() and candidate.is_file():
                        content = cls._read_key_content(candidate)
                        if content:
                            return (content, candidate)

        return None

    @classmethod
    def write_hard_wallet_key(
        cls,
        target_drive: Path | str,
        secret_key_or_password: str,
        filename: str = ".vault_key",
        use_subdir: bool = True,
    ) -> Path:
        """
        Grava a chave/senha mestre no pendrive especificado, criando a Hard Wallet física.
        """
        drive_path = Path(target_drive)
        if not drive_path.exists():
            raise FileNotFoundError(f"Unidade de destino '{drive_path}' não está acessível.")

        destination_dir = (drive_path / cls.DEFAULT_KEY_SUBDIR) if use_subdir else drive_path
        destination_dir.mkdir(parents=True, exist_ok=True)

        target_file = destination_dir / filename
        clean_secret = str(secret_key_or_password).strip()

        with open(target_file, "w", encoding="utf-8") as f:
            f.write(clean_secret + "\n")

        # No Windows, tenta ocultar o arquivo/pasta se nome começar com ponto
        if os.name == "nt" and filename.startswith("."):
            try:
                ctypes.windll.kernel32.SetFileAttributesW(str(target_file), 0x02)  # FILE_ATTRIBUTE_HIDDEN
            except Exception:
                pass

        return target_file

    @classmethod
    def get_wallet_status(cls, search_paths: Optional[List[Path]] = None) -> Dict[str, Any]:
        """
        Retorna diagnóstico completo do status da Hard Wallet (pendrives detectados e chave ativa).
        """
        drives = search_paths if search_paths is not None else cls.list_available_removable_drives()
        key_info = cls.detect_hard_wallet_key(search_paths=drives)

        return {
            "drives_count": len(drives),
            "drives": [str(d) for d in drives],
            "wallet_connected": key_info is not None,
            "key_file": str(key_info[1]) if key_info else None,
            "masked_key": f"{key_info[0][:3]}...{key_info[0][-3:]}" if key_info and len(key_info[0]) >= 6 else (
                "***" if key_info else None
            ),
        }

    @staticmethod
    def _read_key_content(file_path: Path) -> Optional[str]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            return None
        return None
