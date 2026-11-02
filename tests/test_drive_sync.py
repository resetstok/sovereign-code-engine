"""
Testes Unitários para a Sincronização com o Google Drive (src/pci_engine/drive_sync.py - Etapa 6).
Verifica a detecção de internet, listagem de spool criptografado, cálculo de hashes SHA-256/MD5,
transferência via rclone, validação de integridade remota e expurgo seguro sem perda de dados.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pci_engine.drive_sync import DriveSyncManager


@pytest.fixture
def spool_env(tmp_path: Path):
    spool_dir = tmp_path / "spool_encrypted"
    spool_dir.mkdir(parents=True, exist_ok=True)
    return spool_dir


@pytest.fixture
def sync_mgr(spool_env: Path, tmp_path: Path) -> DriveSyncManager:
    return DriveSyncManager(
        spool_dir=spool_env,
        remote_dest="_DRIVE_RESETALL:PCI_SPOOL",
        db_path=None,
    )


def test_internet_and_rclone_detection(sync_mgr: DriveSyncManager):
    """Verifica detecção de conectividade e presença do rclone."""
    # Rclone está instalado no sistema
    assert sync_mgr.is_rclone_available() is True
    # Checagem de internet não gera exceções não tratadas
    net_ok = sync_mgr.check_internet()
    assert isinstance(net_ok, bool)


def test_list_pending_packages_and_hash_calculation(sync_mgr: DriveSyncManager, spool_env: Path):
    """Testa listagem de pacotes locais e cálculo exato de SHA-256 e MD5."""
    # Inicialmente vazio
    assert len(sync_mgr.list_pending_packages()) == 0

    # Cria dois pacotes simulados
    pkg1 = spool_env / "audit_20261102_001.spool"
    pkg1.write_bytes(b"DADOS_FORENSES_CRIPTOGRAFADOS_001")

    pkg2 = spool_env / "audit_20261102_002.enc"
    pkg2.write_bytes(b"DADOS_FORENSES_CRIPTOGRAFADOS_002")

    packages = sync_mgr.list_pending_packages()
    assert len(packages) == 2
    assert packages[0].name == "audit_20261102_001.spool"

    # Confere cálculo de hash
    hashes = sync_mgr.calculate_file_hashes(pkg1)
    assert "sha256" in hashes
    assert "md5" in hashes
    assert hashes["size_bytes"] == len(b"DADOS_FORENSES_CRIPTOGRAFADOS_001")


def test_sync_offline_preserves_local_files(sync_mgr: DriveSyncManager, spool_env: Path, monkeypatch):
    """Garante que quando não há internet, nenhum arquivo é deletado ou corrompido."""
    pkg = spool_env / "offline_test.spool"
    pkg.write_text("conteudo confidencial", encoding="utf-8")

    # Força offline
    monkeypatch.setattr(sync_mgr, "check_internet", lambda: False)

    res = sync_mgr.sync_spool()
    assert res["status"] == "OFFLINE"
    assert res["synced_count"] == 0
    # Arquivo local continua intacto no spool
    assert pkg.exists() is True


def test_sync_successful_upload_and_purge(sync_mgr: DriveSyncManager, spool_env: Path, monkeypatch):
    """Testa o ciclo de sucesso: upload -> verificação remota de hash -> expurgo do arquivo local."""
    pkg = spool_env / "package_to_sync.spool"
    pkg.write_bytes(b"PAYLOAD_CRIPTOGRAFADO_PCI")

    # Mocks de internet, upload e verificação com sucesso
    monkeypatch.setattr(sync_mgr, "check_internet", lambda: True)
    monkeypatch.setattr(sync_mgr, "upload_package", lambda p: (True, "OK"))
    monkeypatch.setattr(sync_mgr, "verify_remote_integrity", lambda p: (True, "Hash verificado"))

    res = sync_mgr.sync_spool(purge_local_on_success=True)
    assert res["status"] == "COMPLETED"
    assert res["synced_count"] == 1
    assert res["details"][0]["status"] == "SYNCED_AND_VERIFIED"

    # O arquivo local deve ter sido removido com segurança após confirmação remota
    assert pkg.exists() is False


def test_sync_verification_failed_preserves_local(sync_mgr: DriveSyncManager, spool_env: Path, monkeypatch):
    """Garante a regra ZERO LOSS: se a verificação do hash remoto falhar, o arquivo local NÃO é apagado."""
    pkg = spool_env / "corrupted_transmission.spool"
    pkg.write_bytes(b"PAYLOAD_CRUCIAL")

    monkeypatch.setattr(sync_mgr, "check_internet", lambda: True)
    monkeypatch.setattr(sync_mgr, "upload_package", lambda p: (True, "Upload OK"))
    # Simula falha na checagem de integridade remota
    monkeypatch.setattr(sync_mgr, "verify_remote_integrity", lambda p: (False, "Hash MD5 divergente"))

    res = sync_mgr.sync_spool(purge_local_on_success=True)
    assert res["status"] == "COMPLETED"
    assert res["synced_count"] == 0
    assert res["details"][0]["status"] == "VERIFY_FAILED"

    # Arquivo local deve continuar existindo para retransmissão
    assert pkg.exists() is True


def test_sync_dry_run_simulation(sync_mgr: DriveSyncManager, spool_env: Path, monkeypatch):
    """Testa modo dry-run sem alterar o disco."""
    pkg = spool_env / "dry_run.spool"
    pkg.write_bytes(b"DRY_RUN_DATA")

    monkeypatch.setattr(sync_mgr, "check_internet", lambda: True)

    res = sync_mgr.sync_spool(dry_run=True)
    assert res["status"] == "COMPLETED"
    assert res["synced_count"] == 1
    assert res["details"][0]["status"] == "DRY_RUN"
    assert pkg.exists() is True
