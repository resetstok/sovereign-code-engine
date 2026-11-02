from typing import List, Dict
from typing import List, Dict

def verificar_limite_aditivo(valor_inicial: float, total_aditivos: float, reforma_edificio: bool=False) -> dict:
    if valor_inicial <= 0 or total_aditivos < 0:
        raise ValueError('Valor inicial e total aditivos devem ser maiores que zero.')
    limite_legal_pct = 50.0 if reforma_edificio else 25.0
    percentual_aditado = round(total_aditivos / valor_inicial * 100.0, 2)
    excedeu_limite = percentual_aditado > limite_legal_pct
    teto_reais = valor_inicial * (limite_legal_pct / 100.0)
    excesso_reais = round(max(0.0, total_aditivos - teto_reais), 2)
    return {'excedeu_limite': excedeu_limite, 'percentual_aditado': percentual_aditado, 'limite_legal_pct': limite_legal_pct, 'excesso_reais': excesso_reais}

def detectar_fracionamento_dispensa(despesas: List[Dict[str, str | float]], teto_dispensa: float=50000.0) -> dict:
    natureza_despesas = {}
    for despesa in despesas:
        natureza = despesa['natureza']
        valor = despesa['valor']
        if natureza not in natureza_despesas:
            natureza_despesas[natureza] = {'count': 0, 'total_value': 0.0}
        natureza_despesas[natureza]['count'] += 1
        natureza_despesas[natureza]['total_value'] += valor
    naturezas_irregulares = []
    for natureza, data in natureza_despesas.items():
        if data['count'] >= 2 and data['total_value'] > teto_dispensa:
            naturezas_irregulares.append(natureza)
    total_fracionado = sum((data['total_value'] for data in natureza_despesas.values() if data['count'] >= 2))
    return {'naturezas_irregulares': sorted(naturezas_irregulares), 'total_fracionado': round(total_fracionado, 2)}

def consolidar_parecer_sesau(valor_inicial: float, total_aditivos: float, despesas: list) -> dict:
    adit = verificar_limite_aditivo(valor_inicial, total_aditivos)
    frac = detectar_fracionamento_dispensa(despesas)
    excedeu_aditivo = adit['excedeu_limite']
    houve_fracionamento = len(frac['naturezas_irregulares']) > 0
    dano_potencial_erario = round(adit['excesso_reais'] + frac['total_fracionado'], 2)
    risco_global = 'ALTO' if excedeu_aditivo and houve_fracionamento else 'MEDIO' if excedeu_aditivo or houve_fracionamento else 'BAIXO'
    return {'risco_global': risco_global, 'excedeu_aditivo': excedeu_aditivo, 'houve_fracionamento': houve_fracionamento, 'dano_potencial_erario': dano_potencial_erario}
