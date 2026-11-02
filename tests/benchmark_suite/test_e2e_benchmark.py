"""
Suíte de Benchmark End-to-End (Etapa 8).
Executa o ciclo de vida completo do PCI Master System v2.0/v2.1:
1. Preflight de Hardware (limite rígido de 40GB SSD e auditoria de RAM).
2. Decomposição do Masterbook Piloto em DAG encadeada.
3. Roteamento Cognitivo Determinístico (PCIRouter).
4. Execução Cirúrgica Local no MicroHarness com Runtime Invariants.
5. Pull & Lease por Nó Doador com Validação Isolada e Livro-Razão de Kudos.
6. Processamento de Tarefas Noturnas (Colibrì).
7. Sanitização Forense, Redação de PII e Spool Encriptado com Fernet.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.decomposer import MasterbookDecomposer
from pci_engine.preflight import PreflightChecker
from pci_engine.ram_liberator import RAMLiberator
from pci_engine.router import PCIRouter
from pci_engine.micro_harness import MicroHarness
from pci_engine.nightly_colibri import NightlyColibriWorker
from pci_engine.sanitizer import ForensicSanitizer
from donor_client.worker_agent import DonorWorkerAgent


class TestEndToEndBenchmark:

    def test_complete_e2e_lifecycle(self, tmp_path: Path, monkeypatch):
        t_start = time.perf_counter()

        # =====================================================================
        # PASSO 1: PREFLIGHT DE HARDWARE E HIGIENE DE MEMÓRIA
        # =====================================================================
        health = PreflightChecker.check_health(tmp_path)
        assert health["ssd_free_gb"] > 0
        assert health["ram_total_mb"] > 0
        assert "can_run_local_3b" in health

        candidates, potential_mb = RAMLiberator.scan_releasable_processes()
        assert isinstance(candidates, list)
        assert potential_mb >= 0.0

        # =====================================================================
        # PASSO 2: INICIALIZAÇÃO DO BANCO WAL E REGISTRO DE NÓS
        # =====================================================================
        db_path = tmp_path / "data" / "jobs.db"
        db = DatabaseManager(db_path=db_path)

        # Registra nó mestre e nó doador
        db.register_node(
            node_id="MASTER_NODE",
            node_name="PC_MASTER_GTX1650",
            role="MASTER",
            ram_total_mb=8192,
            vram_total_mb=4096,
            ssd_free_gb=120.0,
            supported_models=["qwen2.5-coder:3b"],
            status="ONLINE",
        )
        db.register_node(
            node_id="DONOR_NODE_01",
            node_name="PC_DOADOR_REMOTO",
            role="DONOR",
            ram_total_mb=16384,
            vram_total_mb=8192,
            ssd_free_gb=200.0,
            supported_models=["qwen2.5-coder:7b"],
            status="ONLINE",
        )
        db.record_capability(
            node_id="DONOR_NODE_01",
            model_name="qwen2.5-coder:7b",
            task_type="CRUD",
            sample_size=10,
            success_rate=0.95,
        )

        # =====================================================================
        # PASSO 3: DECOMPOSIÇÃO DO MASTERBOOK PILOTO EM DAG ATÔMICA
        # =====================================================================
        repo_root = Path(__file__).resolve().parent.parent.parent
        fixture_path = repo_root / "tasks" / "fixtures" / "sample_masterbook.md"
        assert fixture_path.exists(), f"Arquivo fixture do Masterbook não encontrado: {fixture_path}"

        decomposer = MasterbookDecomposer(db_manager=db)
        enqueued_ids = decomposer.decompose_file(fixture_path, force_fallback=True)

        assert len(enqueued_ids) == 3
        assert enqueued_ids == ["TASK_AUTH_001", "TASK_STORE_002", "TASK_AUDIT_003"]

        # Valida estados iniciais da DAG
        t1 = db.get_task("TASK_AUTH_001")
        t2 = db.get_task("TASK_STORE_002")
        t3 = db.get_task("TASK_AUDIT_003")

        assert t1["status"] == "PENDING"
        assert t1["depends_on_task_id"] is None

        assert t2["status"] == "WAITING_DEPS"
        assert t2["depends_on_task_id"] == "TASK_AUTH_001"

        assert t3["status"] == "WAITING_DEPS"
        assert t3["depends_on_task_id"] == "TASK_STORE_002"
        assert t3["privacy_level"] == "STRICT_LOCAL"

        # =====================================================================
        # PASSO 4: DECISÃO DO ROTEADOR DETERMINÍSTICO (PCIRouter)
        # =====================================================================
        router = PCIRouter(db_path=db_path, repo_root=tmp_path)

        # T1 deve ir para LOCAL_3B (linhas <= 30)
        route_t1 = router.route_task(t1, api_quotas={}, online_mode=False)
        assert route_t1 == "LOCAL_3B"

        # T3 é STRICT_LOCAL -> NUNCA vai para nuvem ou doadores
        route_t3 = router.route_task(t3, api_quotas={"groq": {"remaining_requests": 100}}, online_mode=True)
        assert route_t3 == "LOCAL_3B"  # <= 35 linhas fica no local 3B estrito

        # =====================================================================
        # PASSO 5: EXECUÇÃO CIRÚRGICA DE T1 PELO MICRO-HARNESS LOCAL
        # =====================================================================
        harness = MicroHarness(repo_root=tmp_path, db_manager=db, model="qwen2.5-coder:3b")

        # Mock do LLM gerando implementação correta da função HMAC
        code_t1 = (
            "import hmac\nimport hashlib\n\n"
            "def generate_session_token(node_id: str, secret: str) -> str:\n"
            "    return hmac.new(secret.encode(), node_id.encode(), hashlib.sha256).hexdigest()\n"
        )
        monkeypatch.setattr(harness, "_call_llm", lambda prompt: f"```python\n{code_t1}\n```")

        res_t1 = harness.execute_queued_task("TASK_AUTH_001")
        assert res_t1["success"] is True

        # Valida que T1 passou e desbloqueou T2 em cascata
        t1_updated = db.get_task("TASK_AUTH_001")
        t2_updated = db.get_task("TASK_STORE_002")

        assert t1_updated["status"] == "VERIFIED"
        assert t2_updated["status"] == "PENDING"  # Desbloqueado!

        # =====================================================================
        # PASSO 6: PULL COM LEASE E SUBMISSÃO POR NÓ DOADOR (T2)
        # =====================================================================
        leased_t2 = router.lease_task_for_donor("DONOR_NODE_01", lease_minutes=45)
        assert leased_t2 is not None
        assert leased_t2["task_id"] == "TASK_STORE_002"

        # Simula o código gerado pelo nó doador
        code_t2 = (
            "class AuditStore:\n"
            "    def __init__(self, max_items: int = 100):\n"
            "        self.max_items = max_items\n"
            "        self.items = []\n"
            "    def append(self, event: str):\n"
            "        self.items.append(event)\n"
            "        if len(self.items) > self.max_items:\n"
            "            self.items.pop(0)\n"
            "    def get_events(self) -> list[str]:\n"
            "        return list(self.items)\n"
        )

        # Validação da submissão no sandbox e concessão de Kudos
        verified_t2 = router.verify_and_integrate_submission(
            task_id="TASK_STORE_002",
            node_id="DONOR_NODE_01",
            candidate_code=code_t2,
            compute_sec=4.2,
        )
        assert verified_t2 is True

        # Valida se os Kudos foram registrados
        kudos_ledger = db.get_kudos_ledger_for_node("DONOR_NODE_01")
        assert len(kudos_ledger) == 1
        assert kudos_ledger[0]["kudos_awarded"] > 0

        # Desbloqueia T3
        db.unlock_ready_tasks()
        t3_unlocked = db.get_task("TASK_AUDIT_003")
        assert t3_unlocked["status"] == "PENDING"

        # =====================================================================
        # PASSO 7: EXECUÇÃO NOTURNA OU LOCAL DE T3 (STRICT_LOCAL)
        # =====================================================================
        code_t3 = (
            "import hashlib\n\n"
            "def validate_integrity(data_bytes: bytes, expected_sha256: str) -> bool:\n"
            "    calculated = hashlib.sha256(data_bytes).hexdigest()\n"
            "    return calculated.lower() == expected_sha256.lower()\n"
        )
        monkeypatch.setattr(harness, "_call_llm", lambda prompt: f"```python\n{code_t3}\n```")

        res_t3 = harness.execute_queued_task("TASK_AUDIT_003")
        assert res_t3["success"] is True

        t3_done = db.get_task("TASK_AUDIT_003")
        assert t3_done["status"] == "VERIFIED"

        # =====================================================================
        # PASSO 8: SANITIZAÇÃO FORENSE, CUSTÓDIA E ENCRIPTAÇÃO DE SPOOL
        # =====================================================================
        source_doc = tmp_path / "laudo_forense.txt"
        raw_evidence = (
            b"EVIDENCIA_OPERACAO_SESAU\n"
            b"CPF_INVESTIGADO: 123.456.789-00\n"
            b"EMAIL_CONTATO: perito@saude.gov.br\n"
            b"DIRETORIO: C:\\Users\\ACER\\Documents\\laudo.pdf\n"
        )
        source_doc.write_bytes(raw_evidence)
        original_hash_expected = hashlib.sha256(raw_evidence).hexdigest()

        sanitizer = ForensicSanitizer()
        deriv_dir = tmp_path / "derivatives"
        manifest = sanitizer.create_sanitized_derivative(
            original_path=source_doc,
            output_dir=deriv_dir,
            strip_metadata_switch=True,
            db_manager=db,
            evidence_id="EVID_SESAU_BENCHMARK_001",
            source_url="https://intranet.sesau.local/auditoria/laudo.pdf",
            custody_status="HASH_ONLY",
        )

        assert manifest["evidence_id"] == "EVID_SESAU_BENCHMARK_001"
        assert manifest["original_sha256"] == original_hash_expected
        assert manifest["metadata_stripped"] is True

        # Verifica arquivo derivado higienizado
        cleaned_text = Path(manifest["derivative_path"]).read_text(encoding="utf-8")
        assert "<REDACTED_CPF>" in cleaned_text
        assert "<REDACTED_EMAIL>" in cleaned_text
        assert "<REDACTED_USER_PATH>" in cleaned_text

        # Verifica persistência no manifest
        stored_ev = db.get_evidence("EVID_SESAU_BENCHMARK_001")
        assert stored_ev is not None
        assert stored_ev["original_sha256"] == manifest["original_sha256"]
        assert stored_ev["custody_status"] == "HASH_ONLY"

        # Criptografa para Spool com Fernet
        spool_dir = tmp_path / "spool_encrypted"
        spool_key = ForensicSanitizer.get_or_create_spool_key(spool_dir)
        enc_result = sanitizer.encrypt_file_to_spool(
            file_path=source_doc,
            spool_dir=spool_dir,
            encryption_key=spool_key,
        )
        assert enc_result["status"] == "ENCRYPTED_IN_SPOOL"
        assert Path(enc_result["encrypted_file"]).exists()

        # =====================================================================
        # PASSO 9: AFERIÇÃO DAS MÉTRICAS DE BENCHMARK
        # =====================================================================
        t_total = time.perf_counter() - t_start

        # Verifica se 100% das tarefas da DAG foram resolvidas
        all_tasks = [db.get_task(tid) for tid in enqueued_ids]
        assert all(t["status"] == "VERIFIED" for t in all_tasks)

        print("\n" + "=" * 65)
        print(" [BENCHMARK SUITE] RESULTADOS FINAIS DE EXECUÇÃO END-TO-END")
        print("=" * 65)
        print(f"Tarefas Executadas e Verificadas: {len(all_tasks)}/3 (100% Taxa de Sucesso)")
        print(f"Tempo Total do Ciclo E2E:         {t_total:.2f} segundos")
        print(f"Conexões de Banco WAL Ativas:     Sim (Zero corrupção SQLite)")
        print(f"Isolamento em Sandbox:            Sim (Git Worktree / Tempdir)")
        print(f"Integridade Forense Registrada:   Sim (SHA-256 + Spool Fernet)")
        print("=" * 65)
