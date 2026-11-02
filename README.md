# Sovereign Code Engine (PCI Master System v2.2)

> **Ambiente Autônomo e Auto-Hospedado de Programação Agêntica com IA para Hardware Contido**  
> Otimizado estritamente para: **Windows 11 | 8 GB RAM Física | NVIDIA GTX 1650 (4 GB VRAM) | SSD NVMe**.

---

## 💡 A Filosofia

Quando os créditos de IA em nuvem acabam ou o trabalho exige soberania total, a programação não pode parar. No entanto, tentar rodar modelos gigantes ou agentes inchados em um PC de 8 GB de RAM resulta em travamento imediato do Windows, OOM e alucinações de código.

O **Sovereign Code Engine** resolve isso aplicando a **Engenharia de Contenção Estrita e Decomposição Cirúrgica**:
1. **Ollama com Qwen2.5-Coder 3B:** Aloca 2.1 GB de VRAM na GTX 1650 (cabe 100% na GPU, com zero vazamento para a RAM).
2. **Higiene Ativa de RAM:** Diagnóstico sob demanda de processos e liberação segura de memória antes de compilações.
3. **Aider Sub-Executor Supervisionado:** Travado com `--no-auto-commits`, `--map-tokens 0` e modos cirúrgicos `/architect` (planejamento) e `/code` (execução).
4. **Motor MTV (Micro-Tarefas Verificadas):** Estratégia *best-of-N* com oráculo Pytest (apenas código com Exit Code 0 é aceito e integrado).
5. **Cofre Auditável & Hard Wallet USB (Pendrive):** Criptografia PBKDF2 + AES-256 Fernet. Permite armazenar a chave mestra em um pendrive físico, impedindo acesso indevido se a unidade for removida.
6. **Rede de Doadores via VPN Tailscale:** Permite que máquinas secundárias (casa/empresa) processem lotes pesados sem expor portas à internet.
7. **Ponte Multi-Workspace & Git:** Exporta código validado diretamente para projetos como `012 - Auditoria (SESAU)` e mantém o log forense imutável.

---

## 📂 Estrutura do Repositório

```
013 - Sovereign-Code-Engine/
├── .aider.conf.yml           # Configuração de segurança do Aider travada para Ollama 3B
├── pyproject.toml            # Configuração do pacote Python e pytest
├── MASTERBOOK.md             # Protocolo detalhado de engenharia e fatiamento
├── LICENSE                   # Licença oficial MIT
├── launch_pci.bat            # Script de inicialização da GUI desktop
├── src/
│   ├── pci_engine/           # Núcleo v2.2 do Sovereign Code Engine
│   │   ├── gui_app.py        # Interface gráfica desktop CustomTkinter estilo Dev-Tech
│   │   ├── mtv_core.py       # Motor Best-of-N com oráculo Pytest
│   │   ├── aider_runner.py   # Sub-executor Aider com travas de segurança
│   │   ├── hard_wallet.py    # Gerenciador de Hard Wallet / Chaveiro USB físico
│   │   ├── context_guard.py  # Proteção de contexto, toggle de PII e cofre cifrado
│   │   ├── broker_api.py     # API FastAPI com autenticação por token dinâmico
│   │   ├── database.py       # Fila SQLite com 5 tabelas e evidências imutáveis
│   │   ├── drive_sync.py     # Sincronização segura com Google Drive
│   │   └── workspace_bridge.py # Ponte de exportação multi-projeto
│   └── code_engine/          # CLI e utilitários de linha de comando
├── tasks/                    # Tarefas ativas, templates e fixtures
└── tests/                    # Suíte completa de 149 testes automatizados
```

---

## 🚀 Como Usar no Dia a Dia

### 1. Iniciar a Interface Gráfica da IDE Soberana
Basta dar duplo clique em `launch_pci.bat` ou executar:
```bash
python -m pci_engine.gui_app
```

### 2. Fluxo da IDE na Aba Masterbook & Fila
1. **`1. ◈ Decompor Masterbook`**: Fatie os requisitos em passos atômicos no SQLite WAL.
2. **`2. ⟡ Execução Autônoma`**: Execute tarefas locais com validação imediata no Pytest.
3. **`3. ⚝ Executar MTV (Best-of-N)`**: Rode o motor Best-of-N com oráculo e seleção do melhor candidato.

### 3. Aider Sub-Executor Supervisionado
Na aba **Aider CLI & Architect**:
- **Planejar (`/architect`)**: Decomponha funcionalidades complexas sem alterar arquivos.
- **Executar (`/code`)**: Aplique alterações cirúrgicas sem commits automáticos indesejados.

### 4. Hard Wallet USB e Cofre Forense
Na aba **Custódia Forense & OSINT**:
- **`💾 Gravar no Pendrive`**: Conecte um pendrive USB e grave sua chave mestra física.
- **`🔌 Ler Hard Wallet (USB)`**: Conecte o pendrive para carregar a chave e decifrar arquivos trancados em `data/spool_encrypted/`.

### 5. Executar a Suíte de Testes
```bash
pytest -q
```

---

## 🛡️ Licença
MIT License - Criado para desenvolvimento resiliente, soberano, seguro e auto-hospedado.
