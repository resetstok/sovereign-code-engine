"""
Testes da Etapa 2: Preflight de Hardware, Higiene de RAM e Sanitização Forense com Spool Fernet.
"""

import hashlib
import json
from pathlib import Path
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.preflight import PreflightChecker
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.sanitizer import ForensicSanitizer


class TestPreflightAndSanitizer:

    def test_01_preflight_storage_and_threshold_exception(self, tmp_path):
        # 1. Medição padrão não deve lançar erro se houver espaço
        free_gb = PreflightChecker.check_storage(target_path=tmp_path, min_free_gb=0.1)
        assert free_gb > 0.1

        # 2. Quando o espaço livre for menor que o limite, DEVE lançar SystemError("BLOCKED_EXTERNAL: SSD abaixo de 40GB")
        with pytest.raises(SystemError, match="BLOCKED_EXTERNAL: SSD abaixo de 40GB"):
            PreflightChecker.check_storage(target_path=tmp_path, min_free_gb=9999999.0)

    def test_02_preflight_ram_and_gpu(self):
        ram = PreflightChecker.check_ram()
        assert ram["ram_total_mb"] > 0
        assert ram["ram_available_mb"] > 0
        assert "can_run_local_3b" in ram

        gpu = PreflightChecker.check_gpu()
        assert "available" in gpu
        assert "is_safe" in gpu

    def test_03_wslconfig_creation_and_adjustment(self, tmp_path):
        # Simula pasta de usuário
        mock_home = tmp_path / "mock_user"
        mock_home.mkdir(parents=True)

        # 1. Criação do .wslconfig inexistente
        res = PreflightChecker.check_or_create_wslconfig(target_home=mock_home, memory_limit="3GB")
        assert res["status"] == "CREATED"
        wslconfig_file = mock_home / ".wslconfig"
        assert wslconfig_file.exists()
        content = wslconfig_file.read_text(encoding="utf-8")
        assert "memory=3GB" in content

        # 2. Segunda execução não recria se já configurado
        res2 = PreflightChecker.check_or_create_wslconfig(target_home=mock_home, memory_limit="3GB")
        assert res2["status"] == "OK"

    def test_04_sync_master_node_status_in_database(self, tmp_path):
        db_path = tmp_path / "test_preflight_nodes.db"
        db = DatabaseManager(db_path=db_path)

        sync_res = PreflightChecker.sync_master_node_status(db_manager=db, target_path=tmp_path)
        assert sync_res["node_id"] == "MASTER_NODE"
        assert sync_res["status"] == "ONLINE"

        # Verifica se o registro no jobs.db foi atualizado
        node = db.get_node("MASTER_NODE")
        assert node is not None
        assert node["role"] == "MASTER"
        assert node["ram_total_mb"] > 0
        assert node["status"] == "ONLINE"

    def test_05_ram_liberator_scans_and_hygiene(self, tmp_path):
        candidates, potential_mb = RAMLiberator.scan_releasable_processes()
        assert isinstance(candidates, list)
        assert potential_mb >= 0.0

        # Limpeza de caches temporários
        cleaned_mb = RAMLiberator.clean_temporary_caches(repo_root=tmp_path)
        assert cleaned_mb >= 0.0

        # Execução do pipeline de higiene
        report = RAMLiberator.execute_hygiene_pipeline(auto_terminate=False, repo_root=tmp_path)
        assert "ram_before_mb" in report
        assert "ram_after_mb" in report
        assert "total_freed_mb" in report

    def test_06_forensic_sanitizer_hash_immutability_and_pii_redaction(self, tmp_path):
        sanitizer = ForensicSanitizer()
        db_path = tmp_path / "evidence_audit.db"
        db = DatabaseManager(db_path=db_path)

        # 1. Cria evidência bruta original com múltiplos padrões de PII
        raw_evidence = tmp_path / "edital_raw.json"
        raw_content = (
            "{\n"
            '  "cpf_servidor": "012.345.678-99",\n'
            '  "cnpj_licitante": "12.345.678/0001-90",\n'
            '  "contato": "fiscal.contrato@prefeitura.rj.gov.br",\n'
            '  "ip_origem": "192.168.10.45",\n'
            '  "api_token": "sk-1234567890abcdef1234567890abcdef",\n'
            '  "caminho_local": "C:\\\\Users\\\\ACER\\\\Desktop\\\\relatorio.pdf"\n'
            "}"
        )
        raw_evidence.write_text(raw_content, encoding="utf-8")

        # Hash do arquivo original antes de qualquer processo
        original_hash_before = hashlib.sha256(raw_evidence.read_bytes()).hexdigest()

        # 2. Executa a criação da cópia sanitizada com gravação no banco
        out_dir = tmp_path / "sanitized_derivatives"
        manifest = sanitizer.create_sanitized_derivative(
            original_path=raw_evidence,
            output_dir=out_dir,
            strip_metadata_switch=True,
            db_manager=db,
            evidence_id="EVID_TEST_001",
            source_url="https://pncp.gov.br/editais/2026/038",
        )

        # 3. COMPROVAÇÃO DE IMUTABILIDADE DO ORIGINAL:
        original_hash_after = hashlib.sha256(raw_evidence.read_bytes()).hexdigest()
        assert original_hash_before == original_hash_after
        assert manifest["original_sha256"] == original_hash_before

        # 4. COMPROVAÇÃO DE OCULTAÇÃO DE PII NA DERIVADA:
        derivative_path = Path(manifest["derivative_path"])
        assert derivative_path.exists()
        cleaned_text = derivative_path.read_text(encoding="utf-8")

        assert "012.345.678-99" not in cleaned_text
        assert "12.345.678/0001-90" not in cleaned_text
        assert "fiscal.contrato@prefeitura.rj.gov.br" not in cleaned_text
        assert "192.168.10.45" not in cleaned_text
        assert "sk-1234567890abcdef1234567890abcdef" not in cleaned_text
        assert "C:\\Users\\ACER" not in cleaned_text

        assert "<REDACTED_CPF>" in cleaned_text
        assert "<REDACTED_CNPJ>" in cleaned_text
        assert "<REDACTED_EMAIL>" in cleaned_text
        assert "<REDACTED_IPV4>" in cleaned_text
        assert "<REDACTED_API_KEY>" in cleaned_text
        assert "<REDACTED_USER_PATH>" in cleaned_text

        # 5. COMPROVAÇÃO DE AUDITORIA NO JOBS.DB:
        db_record = db.get_evidence("EVID_TEST_001")
        assert db_record is not None
        assert db_record["original_sha256"] == original_hash_before
        assert db_record["derivative_sha256"] == manifest["derivative_sha256"]
        assert db_record["metadata_stripped"] == 1
        assert "CPF" in db_record["stripped_categories"]
        assert "CNPJ" in db_record["stripped_categories"]

    def test_07_fernet_spool_encryption_and_decryption(self, tmp_path):
        sanitizer = ForensicSanitizer()
        spool_dir = tmp_path / "spool_encrypted"

        # Arquivo de teste
        source_doc = tmp_path / "osint_secret_payload.json"
        secret_data = b'{"dossier": "Investigacao Contrato 005/2020", "score": 99.8}'
        source_doc.write_bytes(secret_data)
        original_hash = hashlib.sha256(secret_data).hexdigest()

        # 1. Criptografia no Spool
        key = ForensicSanitizer.get_or_create_spool_key(spool_dir)
        enc_result = sanitizer.encrypt_file_to_spool(
            file_path=source_doc,
            spool_dir=spool_dir,
            encryption_key=key,
        )

        assert enc_result["status"] == "ENCRYPTED_IN_SPOOL"
        assert enc_result["original_sha256"] == original_hash
        enc_path = Path(enc_result["encrypted_file"])
        assert enc_path.exists()
        # Garante que os bytes no disco são ilegíveis (cifrados)
        assert enc_path.read_bytes() != secret_data

        # 2. Decifragem do Spool
        decrypted_doc = tmp_path / "decrypted_output.json"
        sanitizer.decrypt_file_from_spool(
            encrypted_file_path=enc_path,
            output_file_path=decrypted_doc,
            encryption_key=key,
        )

        assert decrypted_doc.exists()
        restored_bytes = decrypted_doc.read_bytes()
        assert restored_bytes == secret_data
        assert hashlib.sha256(restored_bytes).hexdigest() == original_hash
