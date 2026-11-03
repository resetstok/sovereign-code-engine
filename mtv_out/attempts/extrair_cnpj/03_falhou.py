def extrair_cnpjs(texto: str) -> list[str]:
    def validar_cnpj(cnpj: str) -> bool:
        if len(cnpj) != 14:
            return False
        numeros = [int(digit) for digit in cnpj if digit.isdigit()]
        soma1 = sum(numeros[i] * peso for i, peso in enumerate(range(5, 1, -1)))
        soma2 = sum(numeros[i] * peso for i, peso in enumerate(range(6, 1, -1)))
        digito1 = (soma1 % 11) if (soma1 % 11) != 0 else 11
        digito2 = (soma2 % 11) if (soma2 % 11) != 0 else 11
        return cnpj[-2:] == f"{digito1}{digito2}"

    cnpjs = set()
    for cnpj in texto.split():
        if cnpj.isdigit() and len(cnpj) == 14 and cnpj not in cnpjs:
            cnpjs.add(cnpj)
            if not validar_cnpj(cnpj):
                cnpjs.remove(cnpj)
    return list(cnpjs)