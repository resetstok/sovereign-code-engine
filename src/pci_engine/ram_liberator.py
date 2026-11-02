"""
Módulo: pci_engine/ram_liberator.py
Gerenciador de Higiene e Liberação de Memória RAM para Ambientes Modestos (8 GB).
Identifica processos ociosos secundários, limpa caches temporários de build/testes
e calcula empiricamente o total de megabytes liberados no host.
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import psutil

logger = logging.getLogger("pci_engine.ram_liberator")

DEFAULT_TARGET_PROCESSES = {
    "chrome.exe": "Google Chrome (Abas secundárias)",
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
    """Higienizador de RAM e caches de disco com medição empírica."""

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
    def scan_releasable_processes(
        cls, target_process_map: Optional[Dict[str, str]] = None
    ) -> Tuple[List[ProcessGroupSummary], float]:
        """
        Escaneia processos ativos do sistema operacional comparando com a lista
        de processos não-essenciais permitidos para liberação.
        """
        targets = target_process_map or DEFAULT_TARGET_PROCESSES
        current_pid = os.getpid()
        groups: Dict[str, Dict[str, any]] = {}

        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                p_name = proc.info["name"]
                if not p_name or p_name.lower() not in targets:
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
                        "description": targets[target_key],
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
        """Encerra os processos candidatos selecionados e retorna (MB_liberados, lista_pids)."""
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

    @classmethod
    def clean_temporary_caches(cls, repo_root: Optional[Path | str] = None) -> float:
        """
        Executa limpeza de coletores de lixo do Python e remove diretórios temporários
        de testes (.pytest_cache, __pycache__, sandboxes órfãs) para liberar RAM e disco.
        Retorna estimativa em MB de espaço liberado.
        """
        # Força coleta de lixo no processo ativo
        gc.collect()

        cleaned_bytes = 0
        if repo_root:
            root_path = Path(repo_root).resolve()
            cache_patterns = [".pytest_cache", "__pycache__", "data/sandboxes"]
            for pat in cache_patterns:
                target = root_path / pat
                if target.exists() and target.is_dir():
                    try:
                        for entry in target.rglob("*"):
                            if entry.is_file():
                                cleaned_bytes += entry.stat().st_size
                        shutil.rmtree(target, ignore_errors=True)
                    except Exception as exc:
                        logger.debug(f"Não foi possível remover cache {target}: {exc}")

        return round(cleaned_bytes / (1024 * 1024), 2)

    @classmethod
    def execute_hygiene_pipeline(
        cls,
        auto_terminate: bool = False,
        repo_root: Optional[Path | str] = None,
    ) -> Dict[str, Any]:
        """Executa a rotina combinada de auditoria, limpeza de caches e encerramento opcional."""
        ram_before = cls.get_system_ram_mb()
        caches_freed_mb = cls.clean_temporary_caches(repo_root=repo_root)

        candidates, potential_mb = cls.scan_releasable_processes()
        released_process_mb = 0.0
        terminated_count = 0

        if auto_terminate and candidates:
            released_process_mb, terminated_pids = cls.terminate_candidates(candidates)
            terminated_count = len(terminated_pids)

        ram_after = cls.get_system_ram_mb()
        total_freed_mb = round(released_process_mb + caches_freed_mb, 2)

        return {
            "ram_before_mb": ram_before["available_mb"],
            "ram_after_mb": ram_after["available_mb"],
            "total_freed_mb": total_freed_mb,
            "caches_freed_mb": caches_freed_mb,
            "terminated_processes_count": terminated_count,
            "potential_remaining_mb": potential_mb if not auto_terminate else 0.0,
            "is_above_threshold": ram_after["available_mb"] >= cls.MIN_DESIRED_FREE_MB,
        }
