"""
Módulo: pci_engine/ram_liberator.py
Gerenciador de Higiene e Liberação de Memória RAM para Ambientes Modestos (8 GB).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple

import psutil

logger = logging.getLogger("pci_engine.ram_liberator")

NON_ESSENTIAL_TARGETS = {
    "chrome.exe": "Google Chrome (Abas em segundo plano)",
    "msedge.exe": "Microsoft Edge",
    "brave.exe": "Brave Browser",
    "firefox.exe": "Mozilla Firefox",
    "discord.exe": "Discord",
    "telegram.exe": "Telegram Desktop",
    "whatsapp.exe": "WhatsApp Desktop",
    "spotify.exe": "Spotify",
}


@dataclass
class ProcessGroupSummary:
    name: str
    description: str
    count: int
    total_rss_mb: float
    pids: List[int]


class RAMLiberator:
    """Higienizador de RAM com medição empírica e confirmação explícita."""

    MIN_DESIRED_FREE_MB = 1800.0

    @classmethod
    def get_system_ram_mb(cls) -> Dict[str, float]:
        vm = psutil.virtual_memory()
        return {
            "total_mb": round(vm.total / (1024 * 1024), 2),
            "available_mb": round(vm.available / (1024 * 1024), 2),
            "used_mb": round(vm.used / (1024 * 1024), 2),
            "percent_used": vm.percent,
        }

    @classmethod
    def scan_releasable_processes(cls) -> Tuple[List[ProcessGroupSummary], float]:
        current_pid = os.getpid()
        groups: Dict[str, Dict[str, any]] = {}

        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                p_name = proc.info["name"]
                if not p_name or p_name.lower() not in NON_ESSENTIAL_TARGETS:
                    continue

                pid = proc.info["pid"]
                if pid == current_pid:
                    continue

                mem_info = proc.info["memory_info"]
                if not mem_info:
                    continue

                rss_mb = mem_info.rss / (1024 * 1024)
                target_key = p_name.lower()

                if target_key not in groups:
                    groups[target_key] = {
                        "name": p_name,
                        "description": NON_ESSENTIAL_TARGETS[target_key],
                        "count": 0,
                        "total_rss_mb": 0.0,
                        "pids": [],
                    }

                groups[target_key]["count"] += 1
                groups[target_key]["total_rss_mb"] += rss_mb
                groups[target_key]["pids"].append(pid)

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        summaries = [
            ProcessGroupSummary(
                name=g["name"],
                description=g["description"],
                count=g["count"],
                total_rss_mb=round(g["total_rss_mb"], 2),
                pids=g["pids"],
            )
            for g in groups.values()
        ]

        summaries.sort(key=lambda s: s.total_rss_mb, reverse=True)
        total_potential_mb = sum(s.total_rss_mb for s in summaries)
        return summaries, round(total_potential_mb, 2)

    @classmethod
    def terminate_candidates(cls, candidates: List[ProcessGroupSummary]) -> Tuple[float, List[int]]:
        released_mb = 0.0
        terminated_pids = []

        for group in candidates:
            for pid in group.pids:
                try:
                    p = psutil.Process(pid)
                    mem = p.memory_info().rss / (1024 * 1024)
                    p.terminate()
                    terminated_pids.append(pid)
                    released_mb += mem
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        return round(released_mb, 2), terminated_pids
