# extrair_cnpj

Escreva a funcao `extrair_cnpjs(texto: str) -> list[str]`.

Ela recebe um texto livre e devolve os CNPJs VALIDOS encontrados nele.

Regras:
- aceita o formato com pontuacao (`00.000.000/0001-91`) e o formato com 14 digitos corridos;
- devolve cada CNPJ so com os 14 digitos (sem pontos, barra ou traco);
- sem repeticoes, na ordem em que aparecem pela primeira vez no texto;
- ignora CNPJs com digitos verificadores errados e sequencias de 14 digitos iguais (ex.: `11111111111111`);
- nao confunda com CPF (11 digitos) nem com numeros maiores ou menores que 14 digitos;
- texto sem CNPJ valido devolve lista vazia.

Digitos verificadores: o 1o usa os pesos 5,4,3,2,9,8,7,6,5,4,3,2 sobre os 12 primeiros digitos;
o 2o usa os pesos 6,5,4,3,2,9,8,7,6,5,4,3,2 sobre os 13 primeiros. Em cada um, some os produtos,
tire o resto da divisao por 11; se o resto for menor que 2 o digito e 0, senao e 11 menos o resto.
