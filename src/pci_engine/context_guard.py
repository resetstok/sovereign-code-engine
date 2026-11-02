"""
Módulo de Proteção de Contexto e Segredos de Código (DevSecOps).
Substitui o antigo sanitizador forense por controles estritos de Engenharia de Software:
1. Impede vazamento de .env, chaves privadas, senhas e tokens de API antes de chamadas externas.
2. Calcula o consumo de tokens/bytes do payload ("Contexto a Enviar").
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Set

# Padrões de segredos que NUNCA devem sair para APIs externas
SECRET_PATTERNS = [
    (re.compile(r'(?i)(api[_-]?key|secret|token|password|auth|bearer)\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{8,})["\']?'), "[REDACTED_SECRET]"),
    (re.compile(r'AIza[0-9A-Za-z-_]{35}'), "[REDACTED_GOOGLE_API_KEY]"),
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), "[REDACTED_API_KEY]"),
    (re.compile(r'gsk_[a-zA-Z0-9]{20,}'), "[REDACTED_GROQ_KEY]"),
    (re.compile(r'ghp_[a-zA-Z0-9]{20,}'), "[REDACTED_GITHUB_TOKEN]"),
]

BLOCKED_FILES: Set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service_account.json",
}


class ContextGuard:
    """Guarda de contexto para envio seguro a LLMs e mitigação de vazamento."""

    @classmethod
    def inspect_and_sanitize(cls, code: str) -> Dict[str, Any]:
        """Verifica se há segredos e retorna texto higienizado + telemetria de tamanho."""
        sanitized = code
        findings: List[str] = []

        for pattern, replacement in SECRET_PATTERNS:
            matches = pattern.findall(sanitized)
            if matches:
                findings.append(f"Detectado padrão suspeito ({len(matches)} ocorrência(s))")
                sanitized = pattern.sub(replacement, sanitized)

        char_count = len(sanitized)
        estimated_tokens = char_count // 4

        return {
            "sanitized_content": sanitized,
            "has_secrets": len(findings) > 0,
            "findings": findings,
            "char_count": char_count,
            "estimated_tokens": estimated_tokens,
            "safe_for_cloud": len(findings) == 0,
        }

    @classmethod
    def is_file_allowed(cls, file_path: str | Path) -> bool:
        """Verifica se o arquivo é proibido de entrar no contexto (ex: .env)."""
        name = Path(file_path).name.lower()
        if name in BLOCKED_FILES or name.endswith(".pem") or name.endswith(".key"):
            return False
        return True
