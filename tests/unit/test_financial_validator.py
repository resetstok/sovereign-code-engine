# AUTO_SYNTHESIZED_TEST
import pytest
from src.financial_validator import auditar_lote_empenhos

def test_auditar_lote_empenhos():
    assert callable(auditar_lote_empenhos)
    res = auditar_lote_empenhos([{'id': 'ITEM_1', 'cobrado': 150.0, 'tabela': 100.0}, {'id': 'ITEM_2', 'cobrado': 100.0, 'tabela': 100.0}])
    assert isinstance(res, dict)
    assert 'total_auditado' in res
    assert isinstance(res['total_auditado'], int)
    assert res['total_auditado'] == 2
    assert 'qtd_irregulares' in res
    assert isinstance(res['qtd_irregulares'], int)
    assert res['qtd_irregulares'] == 1
    assert 'total_glosado' in res
    assert isinstance(res['total_glosado'], (int, float))
    assert 'ids_irregulares' in res
    assert isinstance(res['ids_irregulares'], list)
