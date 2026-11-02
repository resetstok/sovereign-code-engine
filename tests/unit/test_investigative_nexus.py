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