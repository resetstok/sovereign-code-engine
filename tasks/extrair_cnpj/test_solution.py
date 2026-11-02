from solution import extrair_cnpjs

VALIDO_A = "11222333000181"   # 11.222.333/0001-81
VALIDO_B = "00000000000191"   # 00.000.000/0001-91


def test_formato_com_pontuacao():
    assert extrair_cnpjs("Empresa 11.222.333/0001-81 contratada") == [VALIDO_A]


def test_formato_corrido():
    assert extrair_cnpjs("cnpj: 00000000000191.") == [VALIDO_B]


def test_ordem_e_sem_repeticao():
    texto = "A 00.000.000/0001-91, B 11222333000181, de novo A 00.000.000/0001-91"
    assert extrair_cnpjs(texto) == [VALIDO_B, VALIDO_A]


def test_ignora_digito_verificador_errado():
    assert extrair_cnpjs("11.222.333/0001-82") == []


def test_ignora_sequencia_repetida():
    assert extrair_cnpjs("11111111111111") == []


def test_ignora_cpf_e_numeros_de_tamanho_diferente():
    assert extrair_cnpjs("CPF 123.456.789-09 e codigo 1122233300018") == []


def test_sem_cnpj():
    assert extrair_cnpjs("nada por aqui") == []
    assert extrair_cnpjs("") == []
