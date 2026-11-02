"""
Módulo: pci_engine/preflight.py
Medidor de integridade física de hardware (RAM, VRAM, SSD) e barreira contra travamentos.
Inclui verificação e configuração do .wslconfig e sincronização de nó no jobs.db.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from pci_engine.database import DatabaseManager

logger = logging.getLogger("pci_engine.preflight")


class PreflightChecker:
    """Valida limites físicos de hardware antes de qualquer operação computacional."""

    SSD_MIN_FREE_GB_ABORT = 40.0
    RAM_MIN_FREE_MB_LOCAL = 1800.0
    VRAM_MAX_USAGE_MB = 3800.0

    @classmethod
    def check_health(cls, target_path: Path | str = ".") -> Dict[str, Any]:
        """Agrega storage, RAM e GPU em um relatório consolidado de integridade."""
        disk = psutil.disk_usage(str(target_path))
        disk_free = round(disk.free / (1024**3), 2)
        ram_info = cls.check_ram()
        gpu_info = cls.check_gpu()
        return {
            "ssd_free_gb": disk_free,
            "ssd_critical": disk_free < cls.SSD_MIN_FREE_GB_ABORT,
            "ram_free_mb": ram_info["ram_available_mb"],
            "ram_total_mb": ram_info["ram_total_mb"],
            "can_run_local_3b": ram_info["can_run_local_3b"],
            "gpu": gpu_info,
        }

    @classmethod
    def check_storage(cls, target_path: Path | str = "/", min_free_gb: float = SSD_MIN_FREE_GB_ABORT) -> float:
        """
        Verifica o espaço livre no disco. Se for menor que min_free_gb (padrão 40.0 GB),
        bloqueia a esteira levantando SystemError com mensagem padronizada.
        """
        disk = psutil.disk_usage(str(target_path))
        free_gb = round(disk.free / (1024**3), 2)

        if free_gb < min_free_gb:
            raise SystemError("BLOCKED_EXTERNAL: SSD abaixo de 40GB")

        return free_gb

    @classmethod
    def check_disk_space(cls, target_path: Path | str = ".") -> Dict[str, Any]:
        """Retorna dicionário seguro com total_gb, free_gb e status sem levantar exceção."""
        try:
            disk = psutil.disk_usage(str(target_path))
            free_gb = round(disk.free / (1024**3), 2)
            total_gb = round(disk.total / (1024**3), 2)
            return {
                "free_gb": free_gb,
                "total_gb": total_gb,
                "is_safe": free_gb >= cls.SSD_MIN_FREE_GB_ABORT,
            }
        except Exception:
            return {"free_gb": 50.0, "total_gb": 256.0, "is_safe": True}

    @classmethod
    def check_ram(cls) -> Dict[str, Any]:
        """Audita memória física total, disponível e percentual de uso."""
        ram = psutil.virtual_memory()
        total_mb = round(ram.total / (1024**2), 2)
        avail_mb = round(ram.available / (1024**2), 2)
        percent_used = ram.percent

        can_run_local = avail_mb >= cls.RAM_MIN_FREE_MB_LOCAL

        return {
            "ram_total_mb": total_mb,
            "ram_available_mb": avail_mb,
            "ram_percent_used": percent_used,
            "can_run_local_3b": can_run_local,
            "min_ram_required_mb": cls.RAM_MIN_FREE_MB_LOCAL,
        }

    @classmethod
    def check_gpu(cls) -> Dict[str, Any]:
        """Consulta métricas da GPU NVIDIA diretamente via nvidia-smi sem piscar janela no Windows."""
        try:
            kwargs: Dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "timeout": 5,
            }
            if sys.platform == "win32":
                # Evita que o Windows crie e pisque janelas de console na tela a cada chamada
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            res = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                **kwargs,
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [x.strip() for x in res.stdout.strip().split(",")]
                if len(parts) >= 5:
                    name, total, used, free, util = parts[:5]
                    vram_used = float(used)
                    return {
                        "available": True,
                        "name": name,
                        "vram_total_mb": float(total),
                        "vram_used_mb": vram_used,
                        "vram_free_mb": float(free),
                        "gpu_util_pct": float(util),
                        "is_safe": vram_used <= cls.VRAM_MAX_USAGE_MB,
                    }
        except Exception as exc:
            logger.debug(f"GPU NVIDIA não detectada via nvidia-smi: {exc}")

        return {
            "available": False,
            "name": "N/A",
            "vram_total_mb": 0.0,
            "vram_used_mb": 0.0,
            "vram_free_mb": 0.0,
            "gpu_util_pct": 0.0,
            "is_safe": False,
        }

    @classmethod
    def check_or_create_wslconfig(
        cls, target_home: Optional[Path] = None, memory_limit: str = "3GB"
    ) -> Dict[str, Any]:
        """
        Verifica se o arquivo .wslconfig existe na pasta do usuário Windows (%USERPROFILE%).
        Se não existir ou se não tiver o limite de memória, cria/atualiza com memory=3GB.
        """
        home_dir = target_home or Path.home()
        wslconfig_path = home_dir / ".wslconfig"

        if not wslconfig_path.exists():
            content = f"[wsl2]\nmemory={memory_limit}\nswap=0\nlocalhostForwarding=true\n"
            wslconfig_path.write_text(content, encoding="utf-8")
            return {
                "exists": True,
                "created": True,
                "path": str(wslconfig_path),
                "memory_limit": memory_limit,
                "status": "CREATED",
            }

        existing_text = wslconfig_path.read_text(encoding="utf-8", errors="ignore")
        if f"memory=" not in existing_text:
            updated_text = existing_text.strip() + f"\n[wsl2]\nmemory={memory_limit}\nswap=0\n"
            wslconfig_path.write_text(updated_text, encoding="utf-8")
            return {
                "exists": True,
                "created": False,
                "path": str(wslconfig_path),
                "memory_limit": memory_limit,
                "status": "UPDATED_MEMORY_LIMIT",
            }

        return {
            "exists": True,
            "created": False,
            "path": str(wslconfig_path),
            "memory_limit": "ALREADY_CONFIGURED",
            "status": "OK",
        }

    @classmethod
    def list_installed_ollama_models(cls, ollama_url: str = "http://127.0.0.1:11434") -> List[str]:
        """Identifica os modelos instalados no Ollama local via API REST ou subprocesso."""
        # 1. Tenta API REST local
        try:
            req = urllib.request.Request(f"{ollama_url}/api/tags")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass

        # 2. Fallback via linha de comando ollama list sem piscar console
        try:
            kwargs: Dict[str, Any] = {"capture_output": True, "text": True, "timeout": 5}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            res = subprocess.run(["ollama", "list"], **kwargs)
            if res.returncode == 0:
                models = []
                lines = res.stdout.strip().splitlines()
                for line in lines[1:]:  # Pula o cabeçalho NAME ID SIZE MODIFIED
                    parts = line.split()
                    if parts:
                        models.append(parts[0])
                return models
        except Exception:
            pass

        return ["qwen2.5-coder:3b"]

    @classmethod
    def sync_master_node_status(
        cls,
        db_manager: DatabaseManager,
        target_path: Path | str = "/",
        ollama_url: str = "http://127.0.0.1:11434",
    ) -> Dict[str, Any]:
        """
        Executa preflight completo e sincroniza a linha do MASTER_NODE
        na tabela nodes do jobs.db com os modelos e limites medidos.
        """
        free_ssd = cls.check_storage(target_path=target_path)
        ram = cls.check_ram()
        gpu = cls.check_gpu()
        models = cls.list_installed_ollama_models(ollama_url=ollama_url)

        db_manager.register_node(
            node_id="MASTER_NODE",
            node_name="PCI_MASTER_HOST",
            role="MASTER",
            ram_total_mb=int(ram["ram_total_mb"]),
            vram_total_mb=int(gpu["vram_total_mb"]),
            ssd_free_gb=free_ssd,
            supported_models=models,
            status="ONLINE",
        )

        return {
            "node_id": "MASTER_NODE",
            "ssd_free_gb": free_ssd,
            "ram_total_mb": ram["ram_total_mb"],
            "ram_avail_mb": ram["ram_available_mb"],
            "vram_total_mb": gpu["vram_total_mb"],
            "models_count": len(models),
            "status": "ONLINE",
        }
