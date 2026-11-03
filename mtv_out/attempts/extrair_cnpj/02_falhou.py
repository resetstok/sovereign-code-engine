def extrair_cnpjs(texto: str) -> list[str]:
    cnpjs = set()
    for i in range(len(texto) - 13):
        cnpj = texto[i:i+14]
        if cnpj.isdigit() and cnpj not in cnpjs:
            cnpj = cnpj.replace('.', '').replace('/', '').replace('-', '')
            if len(cnpj) == 14 and cnpj.isdigit():
                cnpjs.add(cnpj)
    return list(cnpjs)