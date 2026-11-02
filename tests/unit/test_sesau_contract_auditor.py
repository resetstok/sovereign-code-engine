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