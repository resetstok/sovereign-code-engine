"""
Módulo: pci_engine/context_guard.py
Proteção de Contexto, Gestão de Segredos e Custódia de Spool Cifrado (DevSecOps + OSINT Policial).
Oferece controle explícito com ou sem filtro por meio de chave/botão do operador:
1. MODO INVESTIGATIVO / 100% PRESERVADO (apply_redaction=False):
   Mantém CPFs, CNPJs, Placas, Chaves, Tokens e E-mails 100% intactos para processamento
   local, cruzamento de dados policiais e envio aos nós doadores privados (Tailscale da sua casa/empresa).
2. MODO REDAÇÃO NUVEM (apply_redaction=True):
   Mascarada identificadores sensíveis para [REDACTED_...] antes de despachar a APIs públicas externas.
3. COFRE TRANCADO COM CRIPTOGRAFIA (Spool Cifrado para Google Drive):
   Tranca dados sensíveis em data/spool_encrypted/ com criptografia militar (Fernet AES)
   e registra o hash SHA-256 no jobs.db (evidence_manifest) para auditoria e sincronização com o Drive.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from cryptography.fernet import Fernet

logger = logging.getLogger("pci_engine.context_guard")

# Padrões de identificadores e segredos (DevSecOps + OSINT)
SECRET_PATTERNS = [
    (re.compile(r'(?i)(api[_-]?key|secret|token|password|auth|bearer)\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{8,})["\']?'), "[REDACTED_SECRET]"),
    (re.compile(r'AIza[0-9A-Za-z-_]{35}'), "[REDACTED_GOOGLE_API_KEY]"),
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), "[REDACTED_API_KEY]"),
    (re.compile(r'gsk_[a-zA-Z0-9]{20,}'), "[REDACTED_GROQ_KEY]"),
    (re.compile(r'ghp_[a-zA-Z0-9]{20,}'), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b'), "[REDACTED_CPF]"),
    (re.compile(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b'), "[REDACTED_CNPJ]"),
    (re.compile(r'\b[A-Z]{3}[-\s]?(?:\d{4}|\d[A-Z]\d{2})\b'), "[REDACTED_PLACA]"),
]

BLOCKED_FILES_DEFAULT: Set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service_account.json",
}


class ContextGuard:
    """Guarda de contexto e custódia segura com seletor operacional (com ou sem filtro)."""

    KEY_FILE = Path("config/spool_fernet.key")

    @classmethod
    def get_or_create_spool_key(cls, repo_root: Optional[Path | str] = None) -> bytes:
        """Obtém ou cria a chave Fernet AES para trancar dados sensíveis no spool."""
        root = Path(repo_root) if repo_root else Path(".")
        key_path = (root / cls.KEY_FILE).resolve()
        if key_path.exists():
            return key_path.read_bytes().strip()
        
        key = Fernet.generate_key()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        return key

    @classmethod
    def inspect_and_sanitize(
        cls,
        code: str,
        apply_redaction: bool = True,
        spool_encrypted: bool = False,
        repo_root: Optional[Path | str] = None,
        db_manager: Optional[Any] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Inspeciona código/texto com controle estrito:
        - apply_redaction=True: Máscara segredos com [REDACTED_...].
        - apply_redaction=False: Preserva 100% dos dados para investigação/OSINT e nós doadores próprios.
        - spool_encrypted=True: Gera cópia trancada com chave Fernet em data/spool_encrypted/ para o Drive.
        """
        findings: List[str] = []
        sanitized = code

        for pattern, replacement in SECRET_PATTERNS:
            matches = pattern.findall(code)
            if matches:
                findings.append(f"Detectado padrão ({len(matches)} ocorrência(s))")
                if apply_redaction:
                    sanitized = pattern.sub(replacement, sanitized)

        char_count = len(sanitized)
        estimated_tokens = char_count // 4

        spool_file_path: Optional[str] = None
        if spool_encrypted:
            spool_file_path = cls.encrypt_to_spool_vault(
                raw_text=code,
                repo_root=repo_root,
                db_manager=db_manager,
                evidence_id=f"SPOOL_{task_id or uuid.uuid4().hex[:8].upper()}",
            )

        return {
            "sanitized_content": sanitized if apply_redaction else code,
            "has_secrets": len(findings) > 0,
            "findings": findings,
            "char_count": char_count,
            "estimated_tokens": estimated_tokens,
            "safe_for_cloud": len(findings) == 0 or apply_redaction,
            "redaction_applied": apply_redaction,
            "spool_file": spool_file_path,
        }

    @classmethod
    def encrypt_to_spool_vault(
        cls,
        raw_text: str,
        repo_root: Optional[Path | str] = None,
        db_manager: Optional[Any] = None,
        evidence_id: Optional[str] = None,
    ) -> str:
        """Tranca dados sensíveis com criptografia militar Fernet AES na pasta data/spool_encrypted/."""
        root = Path(repo_root) if repo_root else Path(".")
        spool_dir = (root / "data" / "spool_encrypted").resolve()
        spool_dir.mkdir(parents=True, exist_ok=True)

        key = cls.get_or_create_spool_key(repo_root=root)
        f = Fernet(key)

        data_bytes = raw_text.encode("utf-8")
        raw_sha256 = hashlib.sha256(data_bytes).hexdigest()
        encrypted_bytes = f.encrypt(data_bytes)
        enc_sha256 = hashlib.sha256(encrypted_bytes).hexdigest()

        eid = evidence_id or f"SPOOL_{uuid.uuid4().hex[:8].upper()}"
        out_file = spool_dir / f"{eid}_{raw_sha256[:12]}.enc"
        out_file.write_bytes(encrypted_bytes)

        if db_manager and hasattr(db_manager, "register_evidence"):
            try:
                db_manager.register_evidence(
                    evidence_id=eid,
                    original_sha256=raw_sha256,
                    source_url=f"spool://{out_file.name}",
                    custody_status="FULL_BYTES_STORED",
                    derivative_sha256=enc_sha256,
                    metadata_stripped=False,
                    stripped_categories="PRESERVED_ENCRYPTED_FERNET_AES",
                    tool_version="ContextGuard-Vault-v2.3",
                    drive_sync_status="LOCAL_SPOOL",
                )
            except Exception as exc:
                logger.warning(f"Erro ao registrar manifest de evidência no banco: {exc}")

        return str(out_file)

    @classmethod
    def is_file_allowed(cls, file_path: str | Path, allow_sensitive_investigation: bool = False) -> bool:
        """
        Verifica se o arquivo pode ser lido.
        Se allow_sensitive_investigation=True, permite leitura para trabalho investigativo/auditoria.
        """
        if allow_sensitive_investigation:
            return True
        name = Path(file_path).name.lower()
        if name in BLOCKED_FILES_DEFAULT or name.endswith(".pem") or name.endswith(".key"):
            return False
        return True
