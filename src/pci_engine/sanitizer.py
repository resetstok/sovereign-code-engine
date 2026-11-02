"""
Módulo: pci_engine/sanitizer.py
Pipeline de Sanitização e Cadeia de Custódia Forense (PCI Forensic Sanitizer).
Garante hash SHA-256 imutável de evidências brutas, gera derivadas com anonimização
determinística de PII, registra no jobs.db e oferece criptografia em spool local (Fernet).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet

from pci_engine.database import DatabaseManager

logger = logging.getLogger("pci_engine.sanitizer")


class ForensicSanitizer:
    """
    Preserva a cadeia de custódia forense calculando hashes SHA-256 originais
    e gerando cópias derivadas higienizadas para consumo por modelos de IA e nuvem.
    """

    PATTERNS = {
        "CPF": re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
        "CNPJ": re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
        "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"),
        "IPV4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "API_KEY": re.compile(r"\b(?:sk-[a-zA-Z0-9_-]{20,}|AIza[0-9A-Za-z-_]{35}|ghp_[a-zA-Z0-9]{36}|xox[baprs]-[0-9a-zA-Z]{10,})\b"),
        "USER_PATH": re.compile(r"[C-Z]:\\+Users\\+[^\s\"\'<>]+", re.IGNORECASE),
    }

    @staticmethod
    def sha256_file(filepath: Path | str) -> str:
        """Calcula o hash SHA-256 byte a byte em blocos de 64KB sem sobrecarregar a memória."""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def create_sanitized_derivative(
        self,
        original_path: Path | str,
        output_dir: Path | str,
        strip_metadata_switch: bool = True,
        db_manager: Optional[DatabaseManager] = None,
        evidence_id: Optional[str] = None,
        source_url: str = "file://local_evidence",
        custody_status: str = "FULL_BYTES_STORED",
    ) -> Dict[str, Any]:
        """
        1. Calcula o hash imutável do arquivo original.
        2. Gera uma cópia derivada em output_dir.
        3. Se strip_metadata_switch for True, higieniza CPF, CNPJ, Email, IPv4, API Keys e Paths.
        4. Calcula o novo hash derivado.
        5. Se db_manager for fornecido, insere o registro na tabela evidence_manifest.
        """
        original_path = Path(original_path).resolve()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if not original_path.exists():
            raise FileNotFoundError(f"Arquivo original não encontrado: {original_path}")

        # 1. Hash original imutável
        orig_hash = self.sha256_file(original_path)

        # 2. Cópia derivada
        derivative_name = f"deriv_{original_path.name}"
        derivative_path = output_dir / derivative_name
        shutil.copy2(original_path, derivative_path)

        stripped_cats: List[str] = []

        # 3. Sanitização de PII e metadados se o interruptor estiver ativo
        text_extensions = {".json", ".txt", ".csv", ".md", ".html", ".py", ".xml", ".log"}
        if strip_metadata_switch and derivative_path.suffix.lower() in text_extensions:
            content = derivative_path.read_text(encoding="utf-8", errors="ignore")
            for label, pattern in self.PATTERNS.items():
                if pattern.search(content):
                    content = pattern.sub(f"<REDACTED_{label}>", content)
                    stripped_cats.append(label)
            derivative_path.write_text(content, encoding="utf-8")

        # 4. Hash da cópia derivada
        deriv_hash = self.sha256_file(derivative_path)

        # 5. Registro automático no jobs.db se db_manager estiver configurado
        ev_id = evidence_id or f"EVID_{uuid.uuid4().hex[:10].upper()}"
        stripped_str = ",".join(stripped_cats) if stripped_cats else "NONE"

        if db_manager:
            db_manager.register_evidence(
                evidence_id=ev_id,
                original_sha256=orig_hash,
                source_url=source_url,
                custody_status=custody_status,
                derivative_sha256=deriv_hash,
                metadata_stripped=strip_metadata_switch,
                stripped_categories=stripped_str,
                tool_version="PCI-Sanitizer-v2.0",
                drive_sync_status="LOCAL_SPOOL",
            )

        return {
            "evidence_id": ev_id,
            "original_sha256": orig_hash,
            "derivative_sha256": deriv_hash,
            "original_path": str(original_path),
            "derivative_path": str(derivative_path),
            "metadata_stripped": strip_metadata_switch,
            "stripped_categories": stripped_str,
            "tool_version": "PCI-Sanitizer-v2.0",
        }

    # -------------------------------------------------------------------------
    # Criptografia em Spool Local (Fernet / AES-128-CBC)
    # -------------------------------------------------------------------------
    @staticmethod
    def get_or_create_spool_key(spool_dir: Path | str) -> bytes:
        """Obtém ou gera chave Fernet para criptografia da fila local em spool."""
        spool_path = Path(spool_dir).resolve()
        spool_path.mkdir(parents=True, exist_ok=True)
        key_file = spool_path / ".spool_key"

        if key_file.exists():
            return key_file.read_bytes()

        new_key = Fernet.generate_key()
        key_file.write_bytes(new_key)
        return new_key

    def encrypt_file_to_spool(
        self,
        file_path: Path | str,
        spool_dir: Path | str = "data/spool_encrypted",
        encryption_key: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Criptografa o arquivo utilizando Fernet e armazena na pasta spool_encrypted
        para quando a sincronização de nuvem/Google Drive estiver temporariamente indisponível.
        """
        src_path = Path(file_path).resolve()
        if not src_path.exists():
            raise FileNotFoundError(f"Arquivo a cifrar não existe: {src_path}")

        spool_path = Path(spool_dir).resolve()
        spool_path.mkdir(parents=True, exist_ok=True)

        key = encryption_key or self.get_or_create_spool_key(spool_path)
        fernet = Fernet(key)

        original_bytes = src_path.read_bytes()
        orig_hash = hashlib.sha256(original_bytes).hexdigest()

        encrypted_data = fernet.encrypt(original_bytes)
        target_encrypted_file = spool_path / f"{src_path.name}.enc"
        target_encrypted_file.write_bytes(encrypted_data)

        return {
            "status": "ENCRYPTED_IN_SPOOL",
            "original_file": str(src_path),
            "encrypted_file": str(target_encrypted_file),
            "original_sha256": orig_hash,
            "encrypted_bytes_size": len(encrypted_data),
        }

    def decrypt_file_from_spool(
        self,
        encrypted_file_path: Path | str,
        output_file_path: Path | str,
        encryption_key: bytes,
    ) -> Path:
        """Decifra arquivo do spool criptografado e restaura os bytes originais."""
        enc_path = Path(encrypted_file_path).resolve()
        out_path = Path(output_file_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        fernet = Fernet(encryption_key)
        encrypted_bytes = enc_path.read_bytes()
        decrypted_bytes = fernet.decrypt(encrypted_bytes)

        out_path.write_bytes(decrypted_bytes)
        return out_path
