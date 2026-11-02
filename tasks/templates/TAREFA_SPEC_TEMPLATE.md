# TEMPLATE DE ESPECIFICAÇÃO DE MICRO-TAREFA (CONTENÇÃO ESTRITA)

### TAREFA [ID_SEQUENCIAL]: [Nome Descritivo da Função]

- **Arquivo-Alvo:** `src/[modulo]/[arquivo].py`
- **Ação:** `CREATE` ou `MODIFY`
- **Escopo Máximo:** 30 linhas de código Python.
- **Arquivo de Teste Correspondente:** `tests/unit/test_[alvo].py`

---

### 1. Assinatura Obrigatória (com Tipagem)
```python
def nome_da_funcao(param_a: dict, param_b: str) -> list[dict]:
    """
    Docstring concisa explicando o objetivo da função.
    """
```

---

### 2. O que a Função DEVE Fazer:
1. Receber o payload JSON bruto de entrada.
2. Navegar no dicionário com tratamento seguro de chaves inexistentes (`.get()`).
3. Extrair os valores especificados.
4. Retornar a estrutura de dados tipada.

---

### 3. O que a Função NÃO DEVE Fazer (Anti-Padrões):
- NÃO instanciar conexões de rede ou bancos de dados diretamente no corpo da função.
- NÃO retornar strings estáticas "hardcoded" para tentar burlar os testes.
- NÃO importar bibliotecas pesadas fora do escopo padrão.

---

### 4. Comando de Disparo no Aider:
```bash
python -m code_engine.cli --aider src/[modulo]/[arquivo].py tests/unit/test_[alvo].py
```
