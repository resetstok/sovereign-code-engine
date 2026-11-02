"""
Módulo: pci_engine/preflight.py
Medidor de hardware (RAM, VRAM, SSD) e barreira de segurança física.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Dict, Any

import psutil

logger = logging.getLogger("pci_engine.preflight")


class PreflightChecker:
    """Valida barreiras físicas antes de despachar ou rodar inferência."""

    SSD_MIN_FREE_GB_ABORT = 40.0
    RAM_MIN_FREE_MB_LOCAL = 1800.0
    VRAM_MAX_USAGE_MB = 3800.0

    @classmethod
    def check_health(cls, target_path: Path | str = "/") -> Dict[str, Any]:
        disk = psutil.disk_usage(str(target_path))
        ram = psutil.virtual_memory()

        free_ssd_gb = round(disk.free / (1024**3), 2)
        free_ram_mb = round(ram.available / (1024**2), 2)
        total_ram_mb = round(ram.total / (1024**2), 2)

        is_ssd_critical = free_ssd_gb < cls.SSD_MIN_FREE_GB_ABORT
        can_run_local = free_ram_mb >= cls.RAM_MIN_FREE_MB_LOCAL

        gpu_info = cls.check_gpu_vram()

        return {
            "passed": not is_ssd_critical,
            "ssd_free_gb": free_ssd_gb,
            "ssd_critical": is_ssd_critical,
            "ram_free_mb": free_ram_mb,
            "ram_total_mb": total_ram_mb,
            "can_run_local_3b": can_run_local,
            "gpu": gpu_info,
        }

    @classmethod
    def check_gpu_vram(cls) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                name, total, used, free, util = [x.strip() for x in res.stdout.strip().split(",")]
                return {
                    "available": True,
                    "name": name,
                    "vram_total_mb": float(total),
                    "vram_used_mb": float(used),
                    "vram_free_mb": float(free),
                    "gpu_util_pct": float(util),
                    "is_safe": float(used) <= cls.VRAM_MAX_USAGE_MB,
                }
        except Exception as exc:
            logger.debug(f"Falha ao consultar nvidia-smi: {exc}")

        return {
            "available": False,
            "name": "N/A",
            "vram_total_mb": 0.0,
            "vram_used_mb": 0.0,
            "vram_free_mb": 0.0,
            "gpu_util_pct": 0.0,
            "is_safe": False,
        }
