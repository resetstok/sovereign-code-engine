# Sovereign Code Engine (PCI Code Engine)

> **Ambiente Autônomo e Auto-Hospedado de Programação com IA para Hardware Contido**  
> Otimizado estritamente para: **Windows 11 | 8 GB RAM Física | NVIDIA GTX 1650 (4 GB VRAM) | 400 GB SSD**.

---

## 💡 A Filosofia

Quando os créditos de IA em nuvem acabam, a programação não pode parar. No entanto, tentar rodar modelos gigantes ou agentes complexos (como OpenHands ou Cline em modo autônomo total) em um PC de 8 GB de RAM resulta em travamento imediato do Windows, vazamento de memória e alucinações de código.

O **Sovereign Code Engine** resolve isso aplicando a **Engenharia de Contenção Estrita**:
1. **Ollama com Qwen2.5-Coder 3B:** Aloca 2.1 GB de VRAM na GTX 1650 (cabe 100% na placa de vídeo, com zero vazamento para a RAM).
2. **Higiene Ativa de RAM:** Detecta e encerra abas ociosas de navegadores, garantindo mais de 2.5 GB livres no sistema.
3. **Aider CLI com TDD Autônomo:** Executa o loop `Código -> Pytest -> Autocorreção -> Git Commit`.
4. **Fila SQLite com Checkpoints:** Persistência atômica em `jobs.db` com concorrência $N = 1$ e recuperação de quedas de energia.
5. **Git & GitHub Integrados:** Todo código aprovado é commitado de forma atômica e enviado para o GitHub.

---

## 📂 Estrutura do Repositório

```
013 - Sovereign-Code-Engine/
├── .aider.conf.yml           # Configuração do Aider travada para Ollama 3B + TDD
├── pyproject.toml            # Especificação do pacote Python
├── MASTERBOOK.md             # Protocolo detalhado de engenharia e fatiamento
├── config/
│   └── continue_config.yaml  # Configuração pronta para o Continue.dev no VS Code
├── src/
│   └── code_engine/
│       ├── cli.py            # CLI unificado (--status, --clean-ram, --run-next, --aider)
│       ├── ram_liberator.py  # Higiene e liberação de memória RAM
│       ├── orchestrator.py   # Orquestrador atômico DECOMPOR -> CODIFICAR -> TESTAR -> COMMITAR
│       └── durable_queue/    # Fila SQLite com checkpoints e recuperação de órfãos
├── tasks/
│   ├── templates/            # Modelos de especificação atômica e testes paramétricos
│   └── fixtures/             # Dados reais para evitar a preguiça da IA
└── tests/
    └── unit/                 # Gabinete de testes automatizados com Pytest
```

---

## 🚀 Como Usar no Dia a Dia

### 1. Higienizar a Memória RAM
Antes de codar, garanta que o Windows tenha memória livre suficiente:
```bash
python -m code_engine.cli --clean-ram
```

### 2. Verificar Status Geral
Audita RAM, VRAM, modelos do Ollama e estado do Git:
```bash
python -m code_engine.cli --status
```

### 3. Executar uma Tarefa com Aider e Testes Automáticos
```bash
python -m code_engine.cli --aider src/modulo_alvo.py tests/unit/test_alvo.py
```

### 4. Enviar Código Validado para o GitHub
```bash
git push origin main
```

---

## 🛡️ Licença
MIT License - Criado para desenvolvimento resiliente, soberano e auto-hospedado.
