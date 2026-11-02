# PCI MASTER SYSTEM (SOVEREIGN CODE ENGINE) — ESPECIFICAÇÃO v2.0
**Estado:** Especificação de Arquitetura Consolidada v2.0 (Blueprints + Schemas + Roteador + Broker Distribuído).  
**Hardware Base do Master:** Windows 11 com WSL2 (RAM travada em 3GB) | 8 GB RAM Física | NVIDIA GTX 1650 (4 GB VRAM) | 400 GB SSD.

---

## 1. Estrutura de Diretórios v2.0

```
013 - Sovereign-Code-Engine/
├── .aider.conf.yml                # Travado: --no-repomap, --message-history-size 3, auto-test ativo
├── pyproject.toml                 # Dependências mínimas: fastapi, uvicorn, psutil, pytest, requests
├── MASTERBOOK.md                  # Este documento (Constituição e Arquitetura v2.0)
├── config/
│   ├── continue_config.yaml       # Autocomplete isolado no VS Code (Qwen 3B)
│   ├── routing_policy.json        # Limites de tamanho, cotas de API e regras de fallback
│   └── storage_quotas.json        # Tetos de SSD, spool criptografado e abortagem (<40GB)
├── data/
│   ├── jobs.db                    # SQLite WAL: Fila, Leases, Kudos, Matriz de Métricas e Custódia
│   └── spool_encrypted/           # Fila local cifrada (Fernet/AES) para resiliência de rede
├── src/
│   └── pci_engine/
│       ├── __init__.py
│       ├── cli.py                 # Entrada unificada e gerador de atalho Desktop
│       ├── preflight.py           # Medidor de hardware (RAM, VRAM, SSD livre) e trava de segurança
│       ├── ram_liberator.py       # Higiene ativa de processos antes de subir o Ollama
│       ├── db.py                  # Camada SQLite WAL e transações atômicas
│       ├── router.py              # Roteador Cognitivo e Árvore de Decisão
│       ├── orchestrator.py        # Decomposição de Masterbook em DAG (fila de microtarefas)
│       ├── broker_api.py          # Servidor FastAPI local (Tailscale) para Pull/Lease dos Doadores
│       ├── sanitizer.py           # Sanitizador de evidências / anonimização PII
│       └── nightly_colibri.py     # Worker da madrugada (llama-server --mmap 1)
├── donor_client/
│   ├── worker_agent.py            # Script que roda no PC doador (faz pull da tarefa, roda IA e devolve diff)
│   └── build_exe.bat              # Comando PyInstaller para gerar o instalador 1-clique do doador
├── tasks/
│   ├── active/                    # Microtarefas geradas (TAREFA_001.md + test_001.py)
│   ├── templates/                 # Templates de fatiamento e testes paramétricos
│   └── fixtures/                  # JSONs/HTMLs anonimizados para evitar hardcoding da IA
└── tests/
    ├── unit/                      # Testes unitários do broker, roteador e sanitizador
    └── benchmark_suite/           # Bateria de 20 testes padrão para medir a taxa de 86%-96% dos nós
```

---

## 2. Entregável 1: O Esquema do Broker SQLite (`jobs.db`)

Operando em `PRAGMA journal_mode=WAL;` e `PRAGMA foreign_keys=ON;`:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1. NÓS E CAPACIDADES MEDIDAS (Master + Doadores)
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    node_name TEXT NOT NULL,
    role TEXT CHECK(role IN ('MASTER', 'DONOR')) NOT NULL,
    ram_total_mb INTEGER NOT NULL,
    vram_total_mb INTEGER NOT NULL,
    ssd_free_gb REAL NOT NULL,
    supported_models TEXT NOT NULL,
    kudos_balance REAL DEFAULT 0.0,
    last_heartbeat DATETIME,
    status TEXT CHECK(status IN ('ONLINE', 'BUSY', 'OFFLINE', 'BLOCKED')) DEFAULT 'OFFLINE'
);

-- 2. MATRIZ DE DESEMPENHO MEDIDA (Meta 86% - 96%)
CREATE TABLE IF NOT EXISTS capability_matrix (
    node_id TEXT REFERENCES nodes(node_id),
    model_name TEXT NOT NULL,
    task_type TEXT CHECK(task_type IN ('EXTRACTION', 'CRUD', 'REFACTOR', 'PARSER_OSINT', 'ARCHITECTURE')) NOT NULL,
    sample_size INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0.0,
    avg_tokens_per_sec REAL DEFAULT 0.0,
    verification_state TEXT CHECK(verification_state IN ('ESTIMATED', 'MEASURED', 'VERIFIED')) DEFAULT 'ESTIMATED',
    benchmark_version TEXT NOT NULL,
    PRIMARY KEY (node_id, model_name, task_type)
);

-- 3. FILA ATÔMICA DE TAREFAS (DAG + Pull with Lease)
CREATE TABLE IF NOT EXISTS tasks_queue (
    task_id TEXT PRIMARY KEY,
    parent_masterbook TEXT NOT NULL,
    depends_on_task_id TEXT REFERENCES tasks_queue(task_id),
    task_type TEXT NOT NULL,
    privacy_level TEXT CHECK(privacy_level IN ('PUBLIC', 'SANITIZED_DERIVATIVE', 'STRICT_LOCAL')) NOT NULL,
    permission_level INTEGER CHECK(permission_level BETWEEN 1 AND 4) DEFAULT 2,
    estimated_lines INTEGER NOT NULL,
    spec_markdown TEXT NOT NULL,
    test_code TEXT NOT NULL,
    target_file_path TEXT NOT NULL,
    status TEXT CHECK(status IN (
        'PENDING', 'WAITING_DEPS', 'LEASED', 'NIGHTLY_BATCH', 
        'VALIDATING', 'VERIFIED', 'FAILED_RETRY', 'BLOCKED_HUMAN_REVIEW', 'BLOCKED_EXTERNAL'
    )) DEFAULT 'PENDING',
    assigned_route TEXT,
    leased_by_node TEXT REFERENCES nodes(node_id),
    lease_expires_at DATETIME,
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    candidate_patch_diff TEXT,
    last_error_log TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

-- 4. LIVRO-RAZÃO DE KUDOS (Auditoria de Contribuição de Doadores)
CREATE TABLE IF NOT EXISTS kudos_ledger (
    tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT REFERENCES nodes(node_id),
    task_id TEXT REFERENCES tasks_queue(task_id),
    kudos_awarded REAL NOT NULL,
    compute_seconds REAL NOT NULL,
    awarded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 5. CADEIA DE CUSTÓDIA E SANITIZAÇÃO (OSINT / Forense)
CREATE TABLE IF NOT EXISTS evidence_manifest (
    evidence_id TEXT PRIMARY KEY,
    original_sha256 TEXT NOT NULL,
    source_url TEXT NOT NULL,
    acquired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    custody_status TEXT CHECK(custody_status IN ('FULL_BYTES_STORED', 'HASH_ONLY')) NOT NULL,
    derivative_sha256 TEXT,
    metadata_stripped BOOLEAN DEFAULT 0,
    stripped_categories TEXT,
    tool_version TEXT,
    drive_sync_status TEXT CHECK(drive_sync_status IN ('LOCAL_SPOOL', 'UPLOADED_VERIFIED')) DEFAULT 'LOCAL_SPOOL'
);
```

---

## 3. Entregável 2: A Lógica de Roteamento Determinística

```
[TAREFA NA FILA]
       │
       ▼
[attempt_count >= max_attempts?] ──Sim──▶ [BLOCKED_HUMAN_REVIEW]
       │ Não
       ▼
[privacy_level == STRICT_LOCAL?] ──Sim──▶ lines <= 35 & attempt==0? ──Sim──▶ [LOCAL_3B]
       │ Não                                                       └──Não──▶ [NIGHTLY_14B]
       ▼
[lines <= 30 & EXTRACTION/CRUD & attempt==0?] ──Sim──▶ [LOCAL_3B] (se histórico >= 86%)
       │ Não
       ▼
[task_type == REFACTOR ou lines > 120?] ──────Sim──▶ [NIGHTLY_14B]
       │ Não
       ▼
[online_mode & cotas Groq/Cerebras/OpenRouter > 5?] ──Sim──▶ [CLOUD_API:provider]
       │ Não
       ▼
[Doador elegível ONLINE via Tailscale com taxa >= 86%?] ──Sim──▶ [DONOR_POOL]
       │ Não
       ▼
[Fallback Seguro: NIGHTLY_14B]
```

---

## 4. Entregável 3: O Mecanismo de Pull com Lease e Verificação de Kudos

1. **Pull com Lease (`GET /tasks/lease`):**  
   O doador requisita uma tarefa. Se houver tarefa `PENDING` com dependências `VERIFIED`, o status passa para `LEASED`, trava o `leased_by_node` e define `lease_expires_at = now + 45min`.
2. **Recuperação de Órfãos (`reclaim_expired_leases`):**  
   A cada ciclo, se um doador cair ou ultrapassar a janela de lease, o status volta para `PENDING` e `attempt_count` é incrementado.
3. **Sandbox de Validação (`POST /tasks/{task_id}/submit`):**  
   O Master recebe o código do doador, escreve em diretório temporário isolado (`tempfile.TemporaryDirectory`) e executa:
   ```bash
   pytest -q --tb=short --disable-warnings test_alvo.py
   ```
4. **Premiação de Kudos:**  
   Se passar (Código 0):
   $$\text{Kudos} = (\text{estimated\_lines} \times 0.5) + (\text{compute\_seconds} \times 0.05)$$
   Creditado no saldo do nó e registrado de forma imutável na tabela `kudos_ledger`.

---

## 5. Entregável 4: Pipeline de Sanitização e Custódia (`sanitizer.py`)

- Calcula o hash SHA-256 do arquivo original inalterado.
- Cria cópia derivada sanitizada.
- Remove dados sensíveis via Regex determinístico:
  - **CPF:** `\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b`
  - **CNPJ:** `\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b`
  - **E-mail:** `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b`
  - **IPv4:** `\b(?:\d{1,3}\.){3}\d{1,3}\b`
  - **Caminhos de Usuário Windows:** `[C-Z]:\\Users\\[^\\]+`
- Registra manifesto auditável na tabela `evidence_manifest`.

---

## 6. Operação do Sistema Master e Doador

```bash
# No PC Master:
python -m pci_engine.cli --status        # Audita hardware e tarefas
python -m pci_engine.cli --clean-ram     # Libera RAM fechando navegadores ociosos
python -m pci_engine.cli --route         # Despacha a próxima tarefa da DAG
python -m pci_engine.cli --start-broker  # Sobe o servidor FastAPI para a Tailscale (porta 8000)
python -m pci_engine.cli --run-nightly   # Processa a fila noturna da madrugada

# No PC Doador (amigo via Tailscale):
python donor_client/worker_agent.py --master http://100.x.y.z:8000
```
