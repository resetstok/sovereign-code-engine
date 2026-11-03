"""
Módulo: pci_engine/drive_sync.py
Sincronização Segura de Spool Criptografado com o Google Drive via Rclone (Etapa 6).
Transfere pacotes forenses criptografados da pasta data/spool_encrypted/ para a nuvem
apenas quando houver conectividade com a internet, conferindo o hash remoto antes
de expurgar o spool local para garantir a integridade da cadeia de custódia.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pci_engine.database import DatabaseManager
from pci_engine.runtime_utils import get_real_rclone_binary, get_subprocess_creation_flags

logger = logging.getLogger("pci_engine.drive_sync")

DEFAULT_SPOOL_DIR = Path("data/spool_encrypted").resolve()
DEFAULT_REMOTE = os.getenv("PCI_RCLONE_REMOTE", "_DRIVE_RESETALL:PCI_SPOOL")


class DriveSyncManager:
    """
    Gerenciador de sincronização de dados criptografados para o Google Drive.
    Utiliza rclone para transferência confiável com verificação criptográfica de hash.
    """

    def __init__(
        self,
        spool_dir: Path | str = DEFAULT_SPOOL_DIR,
        remote_dest: str = DEFAULT_REMOTE,
        db_path: Optional[Path | str] = "data/jobs.db",
    ):
        self.spool_dir = Path(spool_dir).resolve()
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.remote_dest = remote_dest
        self.db_path = Path(db_path).resolve() if db_path else None
        self.db = DatabaseManager(db_path=self.db_path) if self.db_path and self.db_path.exists() else None
        self.rclone_bin = get_real_rclone_binary()

    @staticmethod
    def check_internet(host: str = "8.8.8.8", port: int = 53, timeout: float = 3.0) -> bool:
        """
        Verifica se há conectividade real com a internet.
        Tenta socket TCP DNS direto; caso falhe por restrição de firewall, tenta HTTP.
        """
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            pass

        try:
            req = urllib.request.Request("https://www.google.com", headers={"User-Agent": "PCI-Engine-Probe"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def is_rclone_available() -> bool:
        """Verifica se o binário rclone está instalado e acessível no PATH ou Chocolatey."""
        bin_path = get_real_rclone_binary()
        return shutil.which(bin_path) is not None or Path(bin_path).is_file()

    def list_pending_packages(self) -> List[Path]:
        """Lista todos os pacotes criptografados pendentes no spool local (incluindo subpastas de nós doadores)."""
        if not self.spool_dir.exists():
            return []
        packages = []
        for p in self.spool_dir.rglob("*"):
            if p.is_file() and not p.name.endswith(".tmp") and not p.name.startswith("."):
                packages.append(p)
        return sorted(packages, key=lambda x: x.stat().st_mtime)

    def _get_remote_target(self, file_path: Path) -> str:
        """Determina o caminho de destino no Google Drive preservando a subpasta do nó doador."""
        try:
            rel_path = file_path.resolve().relative_to(self.spool_dir.resolve())
            return f"{self.remote_dest.rstrip('/')}/{rel_path.as_posix()}"
        except ValueError:
            return f"{self.remote_dest.rstrip('/')}/{file_path.name}"

    @staticmethod
    def calculate_file_hashes(file_path: Path) -> Dict[str, str]:
        """Calcula hashes SHA-256 e MD5 do arquivo para validação com o Google Drive."""
        sha256 = hashlib.sha256()
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
                md5.update(chunk)
        return {
            "sha256": sha256.hexdigest(),
            "md5": md5.hexdigest(),
            "size_bytes": file_path.stat().st_size,
        }

    def upload_package(self, file_path: Path) -> Tuple[bool, str]:
        """
        Envia um único pacote criptografado para o Google Drive via 'rclone copyto'.
        Retorna (sucesso, mensagem).
        """
        remote_target = self._get_remote_target(file_path)
        cmd = [getattr(self, "rclone_bin", get_real_rclone_binary()), "copyto", str(file_path), remote_target]
        flags = get_subprocess_creation_flags()

        try:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=flags,
            )
            if proc.returncode == 0:
                return True, "Upload concluído"
            return False, f"Falha no rclone copyto: {proc.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT no upload via rclone (>120s)"
        except Exception as exc:
            return False, f"Exceção durante upload: {exc}"

    def verify_remote_integrity(self, file_path: Path) -> Tuple[bool, str]:
        """
        Verifica a integridade do pacote no Google Drive antes de apagar o arquivo local.
        Compara tamanho e hash MD5 remoto via 'rclone md5sum' ou 'rclone size'.
        """
        remote_target = self._get_remote_target(file_path)
        local_info = self.calculate_file_hashes(file_path)

        # 1. Verifica existência e tamanho remoto via rclone size
        r_bin = getattr(self, "rclone_bin", get_real_rclone_binary())
        flags = get_subprocess_creation_flags()
        cmd_size = [r_bin, "size", remote_target, "--json"]
        try:
            proc_size = subprocess.run(
                cmd_size,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=flags,
            )
            if proc_size.returncode != 0:
                return False, f"Arquivo não encontrado no remoto: {proc_size.stderr.strip()}"

            data_size = json.loads(proc_size.stdout.strip())
            remote_bytes = data_size.get("bytes", -1)
            if remote_bytes != local_info["size_bytes"]:
                return False, f"Tamanho divergente: local={local_info['size_bytes']} vs remoto={remote_bytes}"

            # 2. Confere hash MD5 se suportado pelo remoto
            cmd_hash = [r_bin, "md5sum", remote_target]
            proc_hash = subprocess.run(
                cmd_hash,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=flags,
            )
            if proc_hash.returncode == 0 and proc_hash.stdout.strip():
                remote_md5 = proc_hash.stdout.split()[0].strip().lower()
                if remote_md5 and remote_md5 != local_info["md5"].lower():
                    return False, f"Hash MD5 divergente: local={local_info['md5']} vs remoto={remote_md5}"

            return True, "Integridade confirmada"
        except Exception as exc:
            return False, f"Erro na checagem remota: {exc}"

    def sync_spool(
        self,
        purge_local_on_success: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Ciclo principal de sincronização:
        1. Verifica internet.
        2. Verifica disponibilidade do rclone.
        3. Para cada arquivo no spool:
           - Envia para o remoto.
           - Valida hash e tamanho remoto.
           - Se verificado com 100% de precisão, remove o arquivo local para liberar espaço.
        """
        if not self.check_internet():
            logger.info("Internet indisponível. Mantendo pacotes no spool local seguro.")
            return {"status": "OFFLINE", "synced_count": 0, "message": "Sem conexão com a internet."}

        if not self.is_rclone_available():
            logger.error("Rclone não encontrado no PATH. Instale o rclone para sincronizar com a nuvem.")
            return {"status": "NO_RCLONE", "synced_count": 0, "message": "Binário rclone ausente."}

        packages = self.list_pending_packages()
        if not packages:
            logger.info("Spool criptografado vazio. Nada a sincronizar.")
            return {"status": "NO_PACKAGES", "synced_count": 0, "message": "Nenhum pacote pendente."}

        logger.info(f"Iniciando sincronização de {len(packages)} pacotes para {self.remote_dest}...")
        synced_count = 0
        freed_bytes = 0
        details = []

        for pkg in packages:
            file_size = pkg.stat().st_size
            local_hashes = self.calculate_file_hashes(pkg)

            if dry_run:
                logger.info(f"[DRY-RUN] Simulação de upload para {pkg.name} ({file_size} bytes)")
                details.append({"file": pkg.name, "status": "DRY_RUN", "sha256": local_hashes["sha256"]})
                synced_count += 1
                continue

            upload_ok, upload_msg = self.upload_package(pkg)
            if not upload_ok:
                logger.warning(f"Falha ao enviar {pkg.name}: {upload_msg}")
                details.append({"file": pkg.name, "status": "UPLOAD_FAILED", "detail": upload_msg})
                continue

            # Confere hash remoto antes de limpar
            verify_ok, verify_msg = self.verify_remote_integrity(pkg)
            if not verify_ok:
                logger.error(f"Integridade remota rejeitada para {pkg.name}: {verify_msg}. Arquivo local preservado.")
                details.append({"file": pkg.name, "status": "VERIFY_FAILED", "detail": verify_msg})
                continue

            # Integridade 100% confirmada -> pode expurgar o arquivo local
            if purge_local_on_success:
                try:
                    pkg.unlink()
                    freed_bytes += file_size
                    logger.info(f"Pacote {pkg.name} sincronizado, verificado e removido do spool local.")
                    parent_dir = pkg.parent
                    if parent_dir != self.spool_dir:
                        try:
                            if not any(parent_dir.iterdir()):
                                parent_dir.rmdir()
                        except Exception:
                            pass
                except Exception as exc:
                    logger.warning(f"Erro ao remover arquivo local {pkg.name}: {exc}")

            synced_count += 1
            details.append({
                "file": pkg.name,
                "status": "SYNCED_AND_VERIFIED",
                "sha256": local_hashes["sha256"],
                "size_bytes": file_size,
            })

            # Registra no evidence_manifest do jobs.db caso o banco esteja ativo
            if self.db:
                try:
                    self.db.record_evidence(
                        task_id=f"SPOOL_SYNC_{pkg.stem}",
                        file_path=self._get_remote_target(pkg),
                        sha256_hash=local_hashes["sha256"],
                        signature_status="VERIFIED_REMOTE_CLOUD",
                    )
                except Exception as exc:
                    logger.debug(f"Não foi possível registrar evidência no banco: {exc}")

        logger.info(f"Sincronização concluída: {synced_count}/{len(packages)} pacotes transferidos com sucesso.")
        return {
            "status": "COMPLETED",
            "synced_count": synced_count,
            "total_packages": len(packages),
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
            "remote_dest": self.remote_dest,
            "details": details,
        }


def main():
    parser = argparse.ArgumentParser(description="PCI Engine — Sincronizador Rclone para Google Drive")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="Destino remoto rclone (ex: _DRIVE_RESETALL:PCI_SPOOL)")
    parser.add_argument("--spool", default=str(DEFAULT_SPOOL_DIR), help="Diretório local do spool criptografado")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula a sincronização sem alterar arquivos")
    parser.add_argument("--no-purge", action="store_true", help="Mantém arquivos locais mesmo após verificação remota")
    args = parser.parse_args()

    manager = DriveSyncManager(spool_dir=args.spool, remote_dest=args.remote)
    res = manager.sync_spool(purge_local_on_success=not args.no_purge, dry_run=args.dry_run)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
