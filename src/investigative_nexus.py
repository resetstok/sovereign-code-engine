import re

def extrair_identificadores(texto: str) -> dict:
    cpfs = re.findall('\\b\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}\\b', texto)
    cpfs = sorted(set((''.join(filter(str.isdigit, cpf)) for cpf in cpfs)))
    placas = re.findall('\\b[A-Z]{3}-?(?:\\d{4}|\\d[A-Z]\\d{2})\\b', texto)
    placas = sorted(set((placa.replace('-', '') for placa in placas)))
    return {'cpfs': cpfs, 'placas': placas}

def cruzar_ocorrencias(boletins: list) -> dict:
    cpfs = {}
    placas = {}
    for boletim in boletins:
        identificadores = extrair_identificadores(boletim['relato'])
        for cpf in identificadores['cpfs']:
            if cpf in cpfs:
                cpfs[cpf] += 1
            else:
                cpfs[cpf] = 1
        for placa in identificadores['placas']:
            if placa in placas:
                placas[placa] += 1
            else:
                placas[placa] = 1
    cpfs_reincidentes = [cpf for cpf, count in cpfs.items() if count >= 2]
    placas_reincidentes = [placa for placa, count in placas.items() if count >= 2]
    return {'cpfs_reincidentes': sorted(cpfs_reincidentes), 'placas_reincidentes': sorted(placas_reincidentes)}

def avaliar_risco_investigativo(boletins: list) -> dict:
    ocorrencias = cruzar_ocorrencias(boletins)
    total_vinculos = len(ocorrencias['cpfs_reincidentes']) + len(ocorrencias['placas_reincidentes'])
    nivel_alerta = 'CRITICO' if total_vinculos >= 2 else 'MODERADO' if total_vinculos == 1 else 'BAIXO'
    return {'total_vinculos': total_vinculos, 'nivel_alerta': nivel_alerta, 'cpfs_reincidentes': ocorrencias['cpfs_reincidentes'], 'placas_reincidentes': ocorrencias['placas_reincidentes']}
