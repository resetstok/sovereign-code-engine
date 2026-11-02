# MASTERBOOK: Auditoria de Contratos e Fracionamento de Despesas (SESAU)
Especificação de engenharia real para auditoria de aditivos contratuais (Art. 65 Lei 8.666 / Art. 125 Lei 14.133) e detecção de fracionamento indevido de dispensa de licitação.

### TAREFA TASK_SESAU_01: Verificador de Limite Legal de Aditivos Contratuais (25% / 50%)
Arquivo-Alvo: `src/sesau_contract_auditor.py`
Privacidade: `STRICT_LOCAL`
Linhas: 25
Tipo: `EXTRACTION`

Implemente em `src/sesau_contract_auditor.py` a função `verificar_limite_aditivo(valor_inicial: float, total_aditivos: float, reforma_edificio: bool = False) -> dict`:
- Se `valor_inicial <= 0` ou `total_aditivos < 0`, levante `ValueError`.
- Defina `limite_legal_pct = 50.0 if reforma_edificio else 25.0`.
- Calcule `percentual_aditado = round((total_aditivos / valor_inicial) * 100.0, 2)`.
- Defina `excedeu_limite = percentual_aditado > limite_legal_pct`.
- Calcule `teto_reais = valor_inicial * (limite_legal_pct / 100.0)` e `excesso_reais = round(max(0.0, total_aditivos - teto_reais), 2)`.
- Retorne `{"excedeu_limite": excedeu_limite, "percentual_aditado": percentual_aditado, "limite_legal_pct": limite_legal_pct, "excesso_reais": excesso_reais}`.

```python
import pytest
from src.sesau_contract_auditor import verificar_limite_aditivo

def test_verificar_limite_aditivo():
    res1 = verificar_limite_aditivo(100000.0, 35000.0, reforma_edificio=False)
    assert res1 == {"excedeu_limite": True, "percentual_aditado": 35.0, "limite_legal_pct": 25.0, "excesso_reais": 10000.0}
    res2 = verificar_limite_aditivo(100000.0, 40000.0, reforma_edificio=True)
    assert res2["excedeu_limite"] is False
    assert res2["excesso_reais"] == 0.0
    with pytest.raises(ValueError):
        verificar_limite_aditivo(0.0, 100.0)
```

### TAREFA TASK_SESAU_02: Detector de Fracionamento Indevido de Dispensa de Licitação
Arquivo-Alvo: `src/sesau_contract_auditor.py`
Privacidade: `STRICT_LOCAL`
Linhas: 28
Tipo: `CRUD`

PRESERVE `verificar_limite_aditivo` em `src/sesau_contract_auditor.py` e adicione a função `detectar_fracionamento_dispensa(despesas: list, teto_dispensa: float = 50000.0) -> dict`:
- Cada item de `despesas` é um dict `{"empenho": str, "natureza": str, "valor": float}`.
- Agrupe as despesas por `"natureza"`, contando quantas despesas existem em cada natureza e somando `"valor"`.
- Uma `"natureza"` possui fracionamento indevido se tiver **2 ou mais despesas** E a soma dos valores daquela natureza for **estritamente maior que `teto_dispensa`**.
- Retorne `{"naturezas_irregulares": sorted(lista_naturezas_irregulares), "total_fracionado": round(soma_total_das_naturezas_irregulares, 2)}`.

```python
from src.sesau_contract_auditor import detectar_fracionamento_dispensa

def test_detectar_fracionamento():
    amostra = [
        {"empenho": "NE01", "natureza": "MATERIAL_MEDICO", "valor": 30000.0},
        {"empenho": "NE02", "natureza": "MATERIAL_MEDICO", "valor": 28000.0},
        {"empenho": "NE03", "natureza": "EQUIPAMENTO_UNICO", "valor": 60000.0},
    ]
    res = detectar_fracionamento_dispensa(amostra, teto_dispensa=50000.0)
    assert res["naturezas_irregulares"] == ["MATERIAL_MEDICO"]
    assert res["total_fracionado"] == 58000.0
```

### TAREFA TASK_SESAU_03: Consolidador de Parecer Técnico de Auditoria SESAU
Arquivo-Alvo: `src/sesau_contract_auditor.py`
Privacidade: `STRICT_LOCAL`
Linhas: 26
Tipo: `EXTRACTION`

PRESERVE as funções anteriores em `src/sesau_contract_auditor.py` e adicione `consolidar_parecer_sesau(valor_inicial: float, total_aditivos: float, despesas: list) -> dict`:
- Chame `adit = verificar_limite_aditivo(valor_inicial, total_aditivos)` e `frac = detectar_fracionamento_dispensa(despesas)`.
- Defina `excedeu_aditivo = adit["excedeu_limite"]` e `houve_fracionamento = len(frac["naturezas_irregulares"]) > 0`.
- Calcule `dano_potencial_erario = round(adit["excesso_reais"] + frac["total_fracionado"], 2)`.
- Defina `risco_global = "ALTO"` se (`excedeu_aditivo` e `houve_fracionamento`), `"MEDIO"` se apenas um dos dois for True, ou `"BAIXO"` se ambos forem False.
- Retorne `{"risco_global": risco_global, "excedeu_aditivo": excedeu_aditivo, "houve_fracionamento": houve_fracionamento, "dano_potencial_erario": dano_potencial_erario}`.

```python
from src.sesau_contract_auditor import consolidar_parecer_sesau

def test_consolidar_parecer_sesau():
    despesas = [
        {"empenho": "NE1", "natureza": "MEDICAMENTOS", "valor": 32000.0},
        {"empenho": "NE2", "natureza": "MEDICAMENTOS", "valor": 25000.0},
    ]
    parecer = consolidar_parecer_sesau(100000.0, 30000.0, despesas)
    assert parecer["risco_global"] == "ALTO"
    assert parecer["excedeu_aditivo"] is True
    assert parecer["houve_fracionamento"] is True
    assert parecer["dano_potencial_erario"] == 62000.0
```
