"""
Módulo: pci_engine/sanitizer.py
Pipeline de Sanitização e Cadeia de Custódia Forense (Entregável 3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("pci_engine.sanitizer")


class ForensicSanitizer:
    """
    Preserva o arquivo original byte a byte e gera derivadas anonimizadas
    para tarefas de código/OSINT que precisem sair do Master PC.
    """

    PATTERNS = {
        "CPF": re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
        "CNPJ": re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
        "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"),
        "IPV4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "USER_PATH": re.compile(r"[C-Z]:\\Users\\[^\\]+", re.IGNORECASE),
    }

    @staticmethod
    def sha256_file(filepath: Path | str) -> str:
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
    ) -> Dict[str, Any]:
        original_path = Path(original_path).resolve()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        orig_hash = self.sha256_file(original_path)
        derivative_path = output_dir / f"deriv_{original_path.name}"
        shutil.copy2(original_path, derivative_path)

        stripped_cats = []

        if strip_metadata_switch and derivative_path.suffix.lower() in (
            ".json",
            ".txt",
            ".csv",
            ".md",
            ".html",
            ".py",
            ".xml",
        ):
            content = derivative_path.read_text(encoding="utf-8", errors="ignore")
            for label, pattern in self.PATTERNS.items():
                if pattern.search(content):
                    content = pattern.sub(f"<REDACTED_{label}>", content)
                    stripped_cats.append(label)
            derivative_path.write_text(content, encoding="utf-8")

        deriv_hash = self.sha256_file(derivative_path)

        return {
            "original_sha256": orig_hash,
            "derivative_sha256": deriv_hash,
            "derivative_path": str(derivative_path),
            "metadata_stripped": strip_metadata_switch,
            "stripped_categories": ",".join(stripped_cats),
            "tool_version": "PCI-Sanitizer-v2.0",
        }
