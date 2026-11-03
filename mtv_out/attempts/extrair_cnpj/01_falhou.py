import re

def extrair_cnpjs(texto: str) -> list[str]:
    cnpjs = set()
    for match in re.finditer(r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b|\b\d{14}\b', texto):
        cnpj = match.group()
        if cnpj.isdigit() and len(cnpj) == 14 and is_valid_cnpj(cnpj):
            cnpjs.add(cnpj)
    return list(cnpjs)

def is_valid_cnpj(cnpj: str) -> bool:
    if len(cnpj) != 14:
        return False
    if cnpj == cnpj[0] * 14:
        return False
    digitos = [int(d) for d in cnpj]
    pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma = sum(d * p for d, p in zip(digitos[:-2], pesos))
    resto = soma % 11
    if resto < 2:
        digito1 = 0
    else:
        digito1 = 11 - resto
    digitos.append(digito1)
    pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma = sum(d * p for d, p in zip(digitos, pesos))
    resto = soma % 11
    if resto < 2:
        digito2 = 0
    else:
        digito2 = 11 - resto
    digitos.append(digito2)
    return digitos == digitos[-14:]