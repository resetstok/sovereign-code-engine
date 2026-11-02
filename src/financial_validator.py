def calcular_glosa_sobrepreco(valor_cobrado: float, valor_tabela: float) -> dict:
    if valor_cobrado < 0 or valor_tabela <= 0:
        raise ValueError('Valores inválidos. Valor cobrado deve ser maior que o valor tabela.')
    diferenca = valor_cobrado - valor_tabela
    percentual_excesso = diferenca / valor_tabela * 100
    sobrepreco = diferenca > 0
    return {'sobrepreco': sobrepreco, 'diferenca': round(diferenca, 2), 'percentual_excesso': round(percentual_excesso, 2)}

def auditar_lote_empenhos(empenhos: list) -> dict:
    total_auditado = 0
    qtd_irregulares = 0
    total_glosado = 0.0
    ids_irregulares = []
    for empenho in empenhos:
        if 'id' not in empenho or 'cobrado' not in empenho or 'tabela' not in empenho:
            raise ValueError('Cada item deve ter os campos "id", "cobrado" e "tabela".')
        glosa = calcular_glosa_sobrepreco(empenho['cobrado'], empenho['tabela'])
        total_auditado += 1
        if glosa['sobrepreco']:
            qtd_irregulares += 1
            ids_irregulares.append(empenho['id'])
        total_glosado += glosa['diferenca']
    return {'total_auditado': total_auditado, 'qtd_irregulares': qtd_irregulares, 'total_glosado': round(total_glosado, 2), 'ids_irregulares': ids_irregulares}
