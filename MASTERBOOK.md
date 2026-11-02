# MASTERBOOK: PROTOCOLO DE DESENVOLVIMENTO CONTIDO
**Projeto:** Sovereign Code Engine  
**Hardware de Referência:** Windows 11 | 8 GB RAM Física | GPU NVIDIA GTX 1650 (4 GB VRAM) | 400 GB SSD

---

## 1. As 5 Leis da Engenharia de Contenção Estrita

1. **A IA Local é um Digitador Confinado de Memória Curta:**
   O modelo de 3B parâmetros nunca deve ler mais de 1 ou 2 arquivos por vez. Ele nunca toma decisões arquiteturais.
2. **TDD Obrigatório Antes do Código:**
   Nenhum código de produção é gerado antes do teste unitário existir com asserções paramétricas em dados reais.
3. **Barreira Mecânica Intransponível:**
   O código só entra no repositório Git se o Pytest retornar código 0 (sucesso). Se falhar, é revertido.
4. **Isolamento Total de Fase:**
   Contêineres Docker, bancos de dados pesados e pipelines ficam DESLIGADOS durante a fase de escrita de código no Aider para não consumir os 8 GB de RAM.
5. **Concorrência Estrita $N = 1$:**
   Apenas uma tarefa de inferência de código roda por vez, garantindo 0 byte de vazamento da VRAM para a RAM do sistema.

---

## 2. A Cadeia de Custódia em 4 Elos

| Elo | Ferramenta | Responsabilidade |
| :--- | :--- | :--- |
| **1. O Arquiteto** | Você ou Gemini/Groq Free | Cria o arquivo `TAREFA.md`, a assinatura com tipos e o teste `test_alvo.py`. |
| **2. O Motor de Execução** | Aider CLI (`aider.exe`) | Lê a tarefa, chama o Ollama Qwen-3B, aplica a alteração no arquivo. |
| **3. O Auditor** | Pytest (`--test-cmd`) | Roda o teste isolado. Em caso de erro, devolve o traceback curto para o Aider corrigir. |
| **4. O Guardião** | Git & GitHub | Faz o commit atômico automático quando o teste passar e envia para o GitHub. |

---

## 3. Comandos Diários de Operação

```bash
# 1. Higienizar a memória antes de começar
python -m code_engine.cli --clean-ram

# 2. Verificar a saúde da GPU e Ollama
python -m code_engine.cli --status

# 3. Disparar o Aider para resolver uma tarefa contida
python -m code_engine.cli --aider src/meu_modulo.py tests/unit/test_meu_modulo.py

# 4. Enviar os commits aprovados para o GitHub
git push origin main
```
