# MASTERBOOK: Motor de Cruzamento de Vínculos Investigativos (Nexus Forense)
Especificação de complexidade média-alta fatiada em 3 microtarefas encadeadas (<= 28 linhas por fatia) para execução 100% autônoma pelo modelo local Qwen 2.5 Coder 3B.

### TAREFA TASK_NEXUS_01: Extrator e Normalizador de Identificadores (CPF e Placa)
Arquivo-Alvo: `src/investigative_nexus.py`
Privacidade: `STRICT_LOCAL`
Linhas: 25
Tipo: `EXTRACTION`

Implemente em `src/investigative_nexus.py` a função `extrair_identificadores(texto: str) -> dict` usando o módulo `re`:
- Deve encontrar todos os CPFs no formato `NNN.NNN.NNN-NN` e retornar na chave `"cpfs"` uma lista ordenada sem duplicatas contendo apenas os 11 dígitos numéricos de cada CPF (ex: `"123.456.789-00"` vira `"12345678900"`).
- Deve encontrar todas as placas de veículos nos padrões antigo (`AAA-1234`) ou Mercosul (`AAA1B23`) usando a regex `\b[A-Z]{3}-?(?:\d{4}|\d[A-Z]\d{2})\b` e retornar na chave `"placas"` uma lista ordenada sem duplicatas sem o hífen (ex: `"BRA-2024"` vira `"BRA2024"`, `"MER1C23"` continua `"MER1C23"`).

```python
from src.investigative_nexus import extrair_identificadores

def test_extrair_identificadores():
    amostra = "Alvo CPF 111.222.333-44 visto no veículo placa BRA-2024 e depois na moto MER1C23 junto ao CPF 111.222.333-44."
    res = extrair_identificadores(amostra)
    assert res["cpfs"] == ["11122233344"]
    assert res["placas"] == ["BRA2024", "MER1C23"]
```

### TAREFA TASK_NEXUS_02: Cruzamento de Colisões entre Múltiplos Boletins
Arquivo-Alvo: `src/investigative_nexus.py`
Privacidade: `STRICT_LOCAL`
Linhas: 28
Tipo: `CRUD`

PRESERVE a função `extrair_identificadores(texto: str) -> dict` já existente em `src/investigative_nexus.py` e adicione no mesmo arquivo a função `cruzar_ocorrencias(boletins: list) -> dict`:
- Cada item de `boletins` é um dicionário `{"id": str, "relato": str}`.
- Para cada boletim, use `extrair_identificadores(item["relato"])` para obter os CPFs e placas citados naquele boletim.
- Retorne um dicionário com duas chaves:
  - `"cpfs_reincidentes"`: lista ordenada de CPFs (11 dígitos) que aparecem em **2 ou mais boletins diferentes**.
  - `"placas_reincidentes"`: lista ordenada de placas (sem hífen) que aparecem em **2 ou mais boletins diferentes**.

```python
from src.investigative_nexus import extrair_identificadores, cruzar_ocorrencias

def test_cruzar_ocorrencias():
    bos = [
        {"id": "BO_01", "relato": "Suspeito CPF 111.222.333-44 em carro BRA-2024."},
        {"id": "BO_02", "relato": "Outro evento com CPF 111.222.333-44 e CPF 999.888.777-66 no carro BRA2024."},
        {"id": "BO_03", "relato": "Apenas moto XYZ1A23 sem relação."},
    ]
    cruzamento = cruzar_ocorrencias(bos)
    assert cruzamento["cpfs_reincidentes"] == ["11122233344"]
    assert cruzamento["placas_reincidentes"] == ["BRA2024"]
```

### TAREFA TASK_NEXUS_03: Dossiê de Prioridade e Score de Risco Investigativo
Arquivo-Alvo: `src/investigative_nexus.py`
Privacidade: `STRICT_LOCAL`
Linhas: 26
Tipo: `EXTRACTION`

PRESERVE as funções `extrair_identificadores` e `cruzar_ocorrencias` já existentes em `src/investigative_nexus.py` e adicione a função `avaliar_risco_investigativo(boletins: list) -> dict`:
- Chame `cruzar_ocorrencias(boletins)` para obter `"cpfs_reincidentes"` e `"placas_reincidentes"`.
- Calcule `total_vinculos = len(cpfs_reincidentes) + len(placas_reincidentes)`.
- Defina `"nivel_alerta"` como:
  - `"CRITICO"` se `total_vinculos >= 2`
  - `"MODERADO"` se `total_vinculos == 1`
  - `"BAIXO"` se `total_vinculos == 0`
- Retorne `{"total_vinculos": total_vinculos, "nivel_alerta": nivel_alerta, "cpfs_reincidentes": cpfs_reincidentes, "placas_reincidentes": placas_reincidentes}`.

```python
from src.investigative_nexus import avaliar_risco_investigativo

def test_avaliar_risco():
    bos = [
        {"id": "BO_1", "relato": "CPF 123.456.789-00 na placa OVR-9090"},
        {"id": "BO_2", "relato": "Repetição de CPF 123.456.789-00 e placa OVR9090"},
    ]
    dossie = avaliar_risco_investigativo(bos)
    assert dossie["total_vinculos"] == 2
    assert dossie["nivel_alerta"] == "CRITICO"
    assert dossie["cpfs_reincidentes"] == ["12345678900"]
    assert dossie["placas_reincidentes"] == ["OVR9090"]
```
