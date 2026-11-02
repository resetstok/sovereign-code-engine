"""
Template de Teste Paramétrico com Amostras Reais (Anti-Preguiça da IA).
Obriga o modelo local (3B) a escrever a lógica real de navegação no dicionário.
"""

import pytest
# from modulo_alvo import funcao_alvo

# CASO 1: Amostra Real A
SAMPLE_A = {
    "numero": "PE038/2020",
    "dados_fornecedor": {
        "cnpj": "12.345.678/0001-90",
        "razao_social": "EMPRESA ALFA LTDA"
    }
}

# CASO 2: Amostra Real B
SAMPLE_B = {
    "numero": "DISP012/2021",
    "dados_fornecedor": {
        "cnpj": "98.765.432/0001-11",
        "razao_social": "EMPRESA BETA S.A."
    }
}


@pytest.mark.parametrize("payload, cnpj_esperado, razao_esperada", [
    (SAMPLE_A, "12.345.678/0001-90", "EMPRESA ALFA LTDA"),
    (SAMPLE_B, "98.765.432/0001-11", "EMPRESA BETA S.A."),
])
def test_extracao_segura_fornecedor(payload, cnpj_esperado, razao_esperada):
    # Se o modelo tentar apenas 'return ("12.345.678/0001-90", "EMPRESA ALFA LTDA")',
    # o teste falhará imediatamente no SAMPLE_B.
    # cnpj, razao = funcao_alvo(payload)
    # assert cnpj == cnpj_esperado
    # assert razao == razao_esperada
    pass
