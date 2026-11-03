import re

def extrair_cnpjs(texto: str) -> list[str]:
    cnpjs = set()
    for match in re.finditer(r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b|\b\d{14}\b', texto):
        cnpj = match.group()
        if cnpj.isdigit() and len(cnpj) == 14:
            cnpjs.add(cnpj)
    return list(cnpjs)