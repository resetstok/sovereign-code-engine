import re

_PADRAO = re.compile(r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)")


def _dv(digitos: str, pesos: list[int]) -> str:
    resto = sum(int(d) * p for d, p in zip(digitos, pesos)) % 11
    return "0" if resto < 2 else str(11 - resto)


def _valido(c: str) -> bool:
    if len(c) != 14 or len(set(c)) == 1:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _dv(c[:12], p1)
    d2 = _dv(c[:12] + d1, [6] + p1)
    return c[12:] == d1 + d2


def extrair_cnpjs(texto: str) -> list[str]:
    vistos: list[str] = []
    for bruto in _PADRAO.findall(texto):
        c = re.sub(r"\D", "", bruto)
        if _valido(c) and c not in vistos:
            vistos.append(c)
    return vistos
