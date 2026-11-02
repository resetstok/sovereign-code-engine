"""
Módulo: code_engine/durable_queue/guardian.py
Guardião de Recursos de Hardware (RAM, Disco e integridade).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

import psutil

logger = logging.getLogger("code_engine.durable_queue.guardian")


class ResourceGuardian:
    """Valida capacidade de hardware antes de despachar jobs."""

    def __init__(self, target_path: Path, min_disk_mb: float = 1000.0, min_ram_mb: float = 800.0):
        self.target_path = Path(target_path).resolve()
        self.min_disk_mb = min_disk_mb
        self.min_ram_mb = min_ram_mb

    def preflight_check(self) -> Dict[str, any]:
        disk = psutil.disk_usage(str(self.target_path))
        disk_free_mb = disk.free / (1024 * 1024)

        ram = psutil.virtual_memory()
        ram_avail_mb = ram.available / (1024 * 1024)

        passed = (disk_free_mb >= self.min_disk_mb) and (ram_avail_mb >= self.min_ram_mb)

        return {
            "passed": passed,
            "disk_free_mb": round(disk_free_mb, 2),
            "ram_avail_mb": round(ram_avail_mb, 2),
            "min_disk_required_mb": self.min_disk_mb,
            "min_ram_required_mb": self.min_ram_mb,
        }
