"""
Testes Unitários para Cofre de Dados Sensíveis de Nós Doadores e Sincronização por Pasta no Drive.
Valida:
1. Criptografia PBKDF2-HMAC-SHA256 + Fernet AES derivada da senha escolhida pelo operador.
2. Preservação integral de CPFs, CNPJs, e-mails e chaves para investigações offline / locais.
3. Empacotamento em data/spool_encrypted/<nome_do_doador>/ trancado por senha.
4. Restauração perfeita com senha correta e rejeição estrita com senha incorreta.
5. Upload do DriveSyncManager preservando a hierarquia de pastas do doador.
6. Endpoint /api/v1/investigative/submit do Broker API.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from pci_engine.broker_api import app, DEFAULT_DONOR_TOKEN
from pci_engine.context_guard import ContextGuard
from pci_engine.database import DatabaseManager
from pci_engine.drive_sync import DriveSyncManager
from pci_engine.sanitizer import ForensicSanitizer


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_vault_jobs.db"
    db = DatabaseManager(db_path=db_file)
    db.register_node(
        node_id="DONOR_NODE_SPECIAL_007",
        node_name="PC_DOADOR_ALPHA",
        role="DONOR",
        ram_total_mb=16384,
        vram_total_mb=8192,
        ssd_free_gb=120.0,
        supported_models=["qwen2.5-coder:7b"],
        status="ONLINE",
    )
    return db


def test_password_based_encryption_and_decryption_lifecycle():
    """Valida a criptografia com senha escolhida pelo operador e recuperação 100% intacta."""
    raw_sensitive_data = (
        "ALVO_INVESTIGADO: Carlos da Silva\n"
        "CPF: 123.456.789-00\n"
        "CNPJ: 12.345.678/0001-90\n"
        "EMAIL: alvo.investigado@sesau.gov.br\n"
        "CHAVE_PIX: 123e4567-e89b-12d3-a456-426614174000\n"
    )
    user_password = "MinhaSenhaSuperSecreta@2026!"

    # 1. Criptografa com a senha do usuário
    encrypted_bytes = ContextGuard.encrypt_with_password(raw_sensitive_data, user_password)
    assert encrypted_bytes.startswith(b"PCI_VAULT_V1")
    assert len(encrypted_bytes) > len(raw_sensitive_data)
    # Garante que os dados originais não aparecem em texto puro no payload cifrado
    assert b"123.456.789-00" not in encrypted_bytes
    assert b"carlos" not in encrypted_bytes.lower()

    # 2. Decifra com a senha correta
    decrypted_bytes = ContextGuard.decrypt_with_password(encrypted_bytes, user_password)
    restored_text = decrypted_bytes.decode("utf-8")
    assert restored_text == raw_sensitive_data
    assert "123.456.789-00" in restored_text
    assert "alvo.investigado@sesau.gov.br" in restored_text

    # 3. Tenta decifrar com senha incorreta -> Deve falhar com ValueError
    with pytest.raises(ValueError, match="Senha incorreta"):
        ContextGuard.decrypt_with_password(encrypted_bytes, "SenhaTotalmenteErrada123")


def test_pack_sensitive_donor_extraction_creates_donor_folder(tmp_path: Path, test_db: DatabaseManager):
    """Verifica que os dados extraídos pelo doador são salvos em data/spool_encrypted/<nome_doador>/."""
    donor_node_name = "PC_DOADOR_ALPHA"
    raw_extraction = {
        "investigador": "Agente_01",
        "cpf_alvo": "987.654.321-11",
        "cnpj_alvo": "98.765.432/0001-01",
        "contrato_num": "CONTRATO_SESAU_2026_999",
        "valor_empenhado": 4500000.00,
    }
    user_password = "SenhaDoOperadorPCI#2026"

    vault = ContextGuard.pack_sensitive_donor_extraction(
        node_name=donor_node_name,
        raw_data=raw_extraction,
        password=user_password,
        repo_root=tmp_path,
        db_manager=test_db,
        evidence_id="EVID_DONOR_ALPHA_001",
    )

    assert vault["status"] == "ENCRYPTED_IN_DONOR_SPOOL"
    assert vault["node_name"] == donor_node_name
    assert vault["protected_by_operator_password"] is True

    spool_file = Path(vault["spool_file"])
    assert spool_file.exists()
    assert spool_file.parent.name == donor_node_name

    # Confere que o cofre está protegido e pode ser aberto com a senha
    encrypted_bytes = spool_file.read_bytes()
    decrypted = ContextGuard.decrypt_with_password(encrypted_bytes, user_password)
    data_restored = json.loads(decrypted.decode("utf-8"))
    assert data_restored["cpf_alvo"] == "987.654.321-11"
    assert data_restored["contrato_num"] == "CONTRATO_SESAU_2026_999"

    # Confere o registro na cadeia de custódia (jobs.db)
    ev = test_db.get_evidence("EVID_DONOR_ALPHA_001")
    assert ev is not None
    assert ev["original_sha256"] == vault["original_sha256"]
    assert "PC_DOADOR_ALPHA" in ev["source_url"]
    assert ev["metadata_stripped"] == 0  # 100% PRESERVADO


def test_drive_sync_uploads_preserving_donor_subfolder(tmp_path: Path):
    """Verifica se o DriveSyncManager envia os arquivos respeitando a subpasta do nó doador no Drive."""
    spool_root = tmp_path / "data" / "spool_encrypted"
    donor_folder = spool_root / "PC_DOADOR_BETA"
    donor_folder.mkdir(parents=True, exist_ok=True)

    pkg = donor_folder / "extracao_investigativa_001.enc"
    pkg.write_bytes(b"PAYLOAD_CIFRADO_COM_SENHA_DO_OPERADOR")

    sync_mgr = DriveSyncManager(
        spool_dir=spool_root,
        remote_dest="_DRIVE_RESETALL:PCI_SPOOL",
        db_path=None,
    )

    pending = sync_mgr.list_pending_packages()
    assert len(pending) == 1
    assert pending[0].resolve() == pkg.resolve()

    # Confere se a rota remota aponta para _DRIVE_RESETALL:PCI_SPOOL/PC_DOADOR_BETA/extracao_investigativa_001.enc
    remote_target = sync_mgr._get_remote_target(pkg)
    assert remote_target == "_DRIVE_RESETALL:PCI_SPOOL/PC_DOADOR_BETA/extracao_investigativa_001.enc"

    # Simula ciclo de sincronização com sucesso
    uploaded_targets = []

    def mock_upload(p: Path):
        uploaded_targets.append(sync_mgr._get_remote_target(p))
        return True, "OK"

    sync_mgr.upload_package = mock_upload
    sync_mgr.verify_remote_integrity = lambda p: (True, "Integridade confirmada")
    sync_mgr.check_internet = lambda: True

    res = sync_mgr.sync_spool(purge_local_on_success=True)
    assert res["status"] == "COMPLETED"
    assert res["synced_count"] == 1
    assert len(uploaded_targets) == 1
    assert uploaded_targets[0] == "_DRIVE_RESETALL:PCI_SPOOL/PC_DOADOR_BETA/extracao_investigativa_001.enc"

    # Confere que o arquivo local foi removido após confirmação
    assert not pkg.exists()


def test_broker_api_investigative_submit_endpoint(tmp_path: Path, monkeypatch):
    """Testa o endpoint /api/v1/investigative/submit recebendo dados do doador e trancando no cofre."""
    db_file = tmp_path / "broker_investigative.db"
    db = DatabaseManager(db_path=db_file)
    db.register_node(
        node_id="DONOR_NODE_GAMMA",
        node_name="PC_DOADOR_GAMMA",
        role="DONOR",
        ram_total_mb=32768,
        vram_total_mb=12288,
        ssd_free_gb=500.0,
        supported_models=["qwen2.5-coder:14b"],
        status="ONLINE",
    )

    # Injeta db no módulo do broker
    import pci_engine.broker_api as broker_mod
    monkeypatch.setattr(broker_mod, "DB_PATH", db_file)
    monkeypatch.setattr(broker_mod, "REPO_ROOT", tmp_path)

    client = TestClient(app)
    auth_headers = {"X-PCI-Donor-Token": DEFAULT_DONOR_TOKEN}

    sensitive_content = (
        "RELATÓRIO POLICIAL / AUDITORIA DE CONTRATOS:\n"
        "EMPRESA ALVO: Fornecedora Alpha Ltda (CNPJ 11.222.333/0001-44)\n"
        "SÓCIO: João da Silva (CPF 111.222.333-44)\n"
        "TELEFONE: (67) 99888-7766\n"
        "CONTRATO SESAU: 042/2026 - R$ 12.800.000,00\n"
    )

    payload = {
        "node_id": "DONOR_NODE_GAMMA",
        "task_id": "TASK_INV_SESAU_042",
        "extracted_content": sensitive_content,
        "password": "SenhaOperadorExclusiva2026",
        "compute_seconds": 3.8,
    }

    resp = client.post("/api/v1/investigative/submit", json=payload, headers=auth_headers)
    assert resp.status_code == 200, f"Erro: {resp.text}"
    data = resp.json()

    assert data["status"] == "INVESTIGATIVE_EXTRACTION_LOCKED"
    assert data["node_name"] == "PC_DOADOR_GAMMA"
    assert "PC_DOADOR_GAMMA" in data["relative_spool_path"]
    assert data["kudos_awarded"] > 0

    # Confere saldo de Kudos creditado ao nó
    node = db.get_node("DONOR_NODE_GAMMA")
    assert node["kudos_balance"] >= data["kudos_awarded"]

    # Confere que o arquivo gerado pode ser aberto com a senha
    spool_file = tmp_path / "data" / "spool_encrypted" / data["relative_spool_path"]
    assert spool_file.exists()
    restored = ContextGuard.decrypt_with_password(spool_file.read_bytes(), "SenhaOperadorExclusiva2026")
    assert "CPF 111.222.333-44" in restored.decode("utf-8")
