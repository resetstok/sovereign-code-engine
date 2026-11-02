from pathlib import Path
import pytest
from pci_engine.hard_wallet import HardWalletManager
from pci_engine.context_guard import ContextGuard


def test_hard_wallet_manager_scan_empty(tmp_path: Path):
    status = HardWalletManager.get_wallet_status(search_paths=[tmp_path])
    assert status["wallet_connected"] is False
    assert status["key_file"] is None
    assert status["drives_count"] == 1


def test_hard_wallet_write_and_detect_lifecycle(tmp_path: Path):
    usb_drive = tmp_path / "mock_usb"
    usb_drive.mkdir()

    secret = "SuperChaveInvestigativa@2026!HardWallet"
    key_path = HardWalletManager.write_hard_wallet_key(
        target_drive=usb_drive,
        secret_key_or_password=secret,
        filename=".vault_key",
        use_subdir=True,
    )
    assert key_path.exists()
    assert ".sovereign_keys" in str(key_path)

    # Detecção
    detected = HardWalletManager.detect_hard_wallet_key(search_paths=[usb_drive])
    assert detected is not None
    detected_secret, detected_path = detected
    assert detected_secret == secret
    assert detected_path == key_path

    # Status
    status = HardWalletManager.get_wallet_status(search_paths=[usb_drive])
    assert status["wallet_connected"] is True
    assert status["masked_key"] == "Sup...let"


def test_hard_wallet_integration_with_context_guard(tmp_path: Path):
    usb_drive = tmp_path / "mock_pendrive"
    usb_drive.mkdir()

    secret_key = "ChaveDoMeuPendrive12345!"
    HardWalletManager.write_hard_wallet_key(usb_drive, secret_key)

    # Detecta
    key_info = HardWalletManager.detect_hard_wallet_key([usb_drive])
    assert key_info is not None
    loaded_password = key_info[0]

    raw_evidence = "CPF_ALVO: 999.888.777-66 | CONTRATO: 042/2026-SESAU"

    # Criptografa usando a chave lida do pendrive
    encrypted = ContextGuard.encrypt_with_password(raw_evidence, loaded_password)
    assert encrypted.startswith(b"PCI_VAULT_V1")

    # Decifra usando a mesma chave lida do pendrive
    decrypted = ContextGuard.decrypt_with_password(encrypted, loaded_password)
    assert decrypted.decode("utf-8") == raw_evidence
