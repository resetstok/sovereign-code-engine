"""
Conjunto de Tarefas Padronizadas para a Bateria de Benchmark PCI Engine (Etapa 6).
Define casos de teste reais e objetivos para as 4 categorias fundamentais:
- EXTRACTION: Extração de padrões, logs e regex.
- CRUD: Estruturas de dados em memória e operações de registro.
- REFACTOR: Otimização de complexidade algorítmica e tipagem estrita.
- PARSER_OSINT: Validação e extração forense de documentos (CPF, CNPJ, IPs).
"""

from __future__ import annotations

from typing import Any, Dict, List

BENCHMARK_TASKS: Dict[str, List[Dict[str, Any]]] = {
    "EXTRACTION": [
        {
            "task_id": "BENCH_EXT_01",
            "name": "Extração de IPs e Métodos HTTP de Logs",
            "spec_markdown": (
                "Crie uma função `extract_log_entries(log_text: str) -> list[dict]` que recebe linhas de log "
                "no formato: `IP - - [DATA] \"METODO /ROTA HTTP/1.1\" STATUS` e extrai uma lista de dicionários "
                "com as chaves: `ip`, `method`, `status` (como inteiro)."
            ),
            "test_code": (
                "from solution import extract_log_entries\n\n"
                "def test_extract_log_entries():\n"
                "    sample = '192.168.1.10 - - [01/Jan/2026] \"GET /api/v1/nodes HTTP/1.1\" 200\\n' \\\n"
                "             '10.0.0.5 - - [01/Jan/2026] \"POST /api/v1/tasks HTTP/1.1\" 201'\n"
                "    entries = extract_log_entries(sample)\n"
                "    assert len(entries) == 2\n"
                "    assert entries[0] == {'ip': '192.168.1.10', 'method': 'GET', 'status': 200}\n"
                "    assert entries[1] == {'ip': '10.0.0.5', 'method': 'POST', 'status': 201}\n"
            ),
            "reference_solution": (
                "import re\n"
                "def extract_log_entries(log_text: str):\n"
                "    pattern = re.compile(r'(\\d+\\.\\d+\\.\\d+\\.\\d+).*?\"(\\w+)\\s+.*?\"\\s+(\\d+)')\n"
                "    res = []\n"
                "    for line in log_text.strip().splitlines():\n"
                "        m = pattern.search(line)\n"
                "        if m:\n"
                "            res.append({'ip': m.group(1), 'method': m.group(2), 'status': int(m.group(3))})\n"
                "    return res\n"
            ),
        },
        {
            "task_id": "BENCH_EXT_02",
            "name": "Extração de Pares Chave-Valor com Normalização",
            "spec_markdown": (
                "Crie uma função `parse_kv_config(raw_config: str) -> dict[str, str]` que lê texto linha a linha, "
                "ignora comentários iniciando com `#` e retorna um dicionário normalizado com chaves minúsculas "
                "e valores limpos (strip)."
            ),
            "test_code": (
                "from solution import parse_kv_config\n\n"
                "def test_parse_kv_config():\n"
                "    raw = '# Configuração\\nTIMEOUT = 30\\nPORT=8080\\n# Comentario\\nHOST = 127.0.0.1\\n'\n"
                "    cfg = parse_kv_config(raw)\n"
                "    assert cfg == {'timeout': '30', 'port': '8080', 'host': '127.0.0.1'}\n"
            ),
            "reference_solution": (
                "def parse_kv_config(raw_config: str) -> dict:\n"
                "    res = {}\n"
                "    for line in raw_config.splitlines():\n"
                "        line = line.strip()\n"
                "        if not line or line.startswith('#') or '=' not in line:\n"
                "            continue\n"
                "        k, v = line.split('=', 1)\n"
                "        res[k.strip().lower()] = v.strip()\n"
                "    return res\n"
            ),
        },
    ],
    "CRUD": [
        {
            "task_id": "BENCH_CRUD_01",
            "name": "Armazenamento em Memória com Limite de Capacidade",
            "spec_markdown": (
                "Implemente a classe `BoundedStore` com os métodos:\n"
                "- `__init__(self, max_items: int = 5)`\n"
                "- `set(self, key: str, value: any) -> None` (se exceder `max_items`, descarta o item mais antigo inserido)\n"
                "- `get(self, key: str, default: any = None) -> any`\n"
                "- `delete(self, key: str) -> bool`\n"
                "- `count(self) -> int`"
            ),
            "test_code": (
                "from solution import BoundedStore\n\n"
                "def test_bounded_store():\n"
                "    store = BoundedStore(max_items=3)\n"
                "    store.set('a', 1)\n"
                "    store.set('b', 2)\n"
                "    store.set('c', 3)\n"
                "    assert store.count() == 3\n"
                "    assert store.get('a') == 1\n"
                "    store.set('d', 4)  # descarta 'a'\n"
                "    assert store.count() == 3\n"
                "    assert store.get('a') is None\n"
                "    assert store.get('d') == 4\n"
                "    assert store.delete('b') is True\n"
                "    assert store.count() == 2\n"
            ),
            "reference_solution": (
                "from collections import OrderedDict\n"
                "class BoundedStore:\n"
                "    def __init__(self, max_items: int = 5):\n"
                "        self.max_items = max_items\n"
                "        self.data = OrderedDict()\n"
                "    def set(self, key: str, value):\n"
                "        if key in self.data:\n"
                "            self.data.move_to_end(key)\n"
                "        self.data[key] = value\n"
                "        if len(self.data) > self.max_items:\n"
                "            self.data.popitem(last=False)\n"
                "    def get(self, key: str, default=None):\n"
                "        return self.data.get(key, default)\n"
                "    def delete(self, key: str) -> bool:\n"
                "        if key in self.data:\n"
                "            del self.data[key]\n"
                "            return True\n"
                "        return False\n"
                "    def count(self) -> int:\n"
                "        return len(self.data)\n"
            ),
        },
        {
            "task_id": "BENCH_CRUD_02",
            "name": "Livro-Razão Contábil de Créditos e Débitos",
            "spec_markdown": (
                "Implemente a classe `AccountLedger` com:\n"
                "- `record_transaction(self, tx_id: str, amount: float, tx_type: str) -> None` (`CREDIT` ou `DEBIT`)\n"
                "- `get_balance(self) -> float`\n"
                "- `list_transactions(self) -> list[dict]`"
            ),
            "test_code": (
                "from solution import AccountLedger\n\n"
                "def test_account_ledger():\n"
                "    ledger = AccountLedger()\n"
                "    ledger.record_transaction('tx1', 100.0, 'CREDIT')\n"
                "    ledger.record_transaction('tx2', 30.0, 'DEBIT')\n"
                "    assert ledger.get_balance() == 70.0\n"
                "    assert len(ledger.list_transactions()) == 2\n"
            ),
            "reference_solution": (
                "class AccountLedger:\n"
                "    def __init__(self):\n"
                "        self.txs = []\n"
                "        self.balance = 0.0\n"
                "    def record_transaction(self, tx_id: str, amount: float, tx_type: str):\n"
                "        if tx_type == 'CREDIT':\n"
                "            self.balance += amount\n"
                "        else:\n"
                "            self.balance -= amount\n"
                "        self.txs.append({'tx_id': tx_id, 'amount': amount, 'type': tx_type})\n"
                "    def get_balance(self) -> float:\n"
                "        return round(self.balance, 2)\n"
                "    def list_transactions(self):\n"
                "        return list(self.txs)\n"
            ),
        },
    ],
    "REFACTOR": [
        {
            "task_id": "BENCH_REF_01",
            "name": "Otimização de Busca O(N) para O(1) com Indexação",
            "spec_markdown": (
                "Refatore a busca de usuários por ID em uma lista para um índice hash rápido:\n"
                "Implemente a classe `UserRegistry` com:\n"
                "- `load_users(self, users: list[dict]) -> None` (armazena indexado por 'id')\n"
                "- `find_by_id(self, user_id: int) -> dict | None`\n"
                "- `find_by_email(self, email: str) -> dict | None`"
            ),
            "test_code": (
                "from solution import UserRegistry\n\n"
                "def test_user_registry():\n"
                "    users = [{'id': 1, 'name': 'Alice', 'email': 'a@pci.local'},\n"
                "             {'id': 2, 'name': 'Bob', 'email': 'b@pci.local'}]\n"
                "    reg = UserRegistry()\n"
                "    reg.load_users(users)\n"
                "    assert reg.find_by_id(1)['name'] == 'Alice'\n"
                "    assert reg.find_by_email('b@pci.local')['name'] == 'Bob'\n"
                "    assert reg.find_by_id(99) is None\n"
            ),
            "reference_solution": (
                "class UserRegistry:\n"
                "    def __init__(self):\n"
                "        self.by_id = {}\n"
                "        self.by_email = {}\n"
                "    def load_users(self, users):\n"
                "        for u in users:\n"
                "            self.by_id[u['id']] = u\n"
                "            self.by_email[u['email'].lower()] = u\n"
                "    def find_by_id(self, user_id: int):\n"
                "        return self.by_id.get(user_id)\n"
                "    def find_by_email(self, email: str):\n"
                "        return self.by_email.get(email.lower())\n"
            ),
        },
        {
            "task_id": "BENCH_REF_02",
            "name": "Validação Funcional e Redução de Complexidade Ciclomática",
            "spec_markdown": (
                "Implemente `validate_payload(data: dict, required_schema: dict) -> tuple[bool, list[str]]`\n"
                "Onde `required_schema` mapeia `campo: tipo_python`. Retorna `(True, [])` se válido ou "
                "`(False, ['campo: erro', ...])` contendo todos os erros encontrados."
            ),
            "test_code": (
                "from solution import validate_payload\n\n"
                "def test_validate_payload():\n"
                "    schema = {'name': str, 'age': int, 'active': bool}\n"
                "    valid = {'name': 'Charlie', 'age': 30, 'active': True}\n"
                "    ok, errs = validate_payload(valid, schema)\n"
                "    assert ok is True\n"
                "    assert len(errs) == 0\n\n"
                "    invalid = {'name': 'Charlie', 'age': 'trinta'}\n"
                "    ok, errs = validate_payload(invalid, schema)\n"
                "    assert ok is False\n"
                "    assert len(errs) == 2  # age tipo invalido e active ausente\n"
            ),
            "reference_solution": (
                "def validate_payload(data: dict, required_schema: dict):\n"
                "    errs = []\n"
                "    for field, expected_type in required_schema.items():\n"
                "        if field not in data:\n"
                "            errs.append(f'{field}: ausente')\n"
                "        elif not isinstance(data[field], expected_type):\n"
                "            errs.append(f'{field}: tipo invalido')\n"
                "    return len(errs) == 0, errs\n"
            ),
        },
    ],
    "PARSER_OSINT": [
        {
            "task_id": "BENCH_OSINT_01",
            "name": "Validador e Sanitizador Forense de CPF",
            "spec_markdown": (
                "Crie uma função `validate_and_mask_cpf(raw_cpf: str) -> tuple[bool, str]` que:\n"
                "1. Remove caracteres não-dígitos.\n"
                "2. Valida se possui 11 dígitos e calcula os dois dígitos verificadores do CPF.\n"
                "3. Se válido, retorna `(True, '***.NNN.NNN-**')` mascarando os 3 primeiros e os 2 últimos dígitos.\n"
                "4. Se inválido, retorna `(False, '')`."
            ),
            "test_code": (
                "from solution import validate_and_mask_cpf\n\n"
                "def test_validate_and_mask_cpf():\n"
                "    # CPF valido de teste de algoritmo: 111.444.777-35\n"
                "    ok, masked = validate_and_mask_cpf('111.444.777-35')\n"
                "    assert ok is True\n"
                "    assert masked == '***.444.777-**'\n\n"
                "    # CPF invalido\n"
                "    fail, _ = validate_and_mask_cpf('111.222.333-00')\n"
                "    assert fail is False\n"
            ),
            "reference_solution": (
                "import re\n"
                "def validate_and_mask_cpf(raw_cpf: str):\n"
                "    digits = [int(c) for c in re.sub(r'\\D', '', raw_cpf)]\n"
                "    if len(digits) != 11 or len(set(digits)) == 1:\n"
                "        return False, ''\n"
                "    for i in (9, 10):\n"
                "        val = sum(digits[j] * ((i + 1) - j) for j in range(i))\n"
                "        d = (val * 10 % 11) % 10\n"
                "        if digits[i] != d:\n"
                "            return False, ''\n"
                "    return True, f'***.{digits[3]}{digits[4]}{digits[5]}.{digits[6]}{digits[7]}{digits[8]}-**'\n"
            ),
        },
        {
            "task_id": "BENCH_OSINT_02",
            "name": "Parser Forense de Número de Processo Judicial (CNJ)",
            "spec_markdown": (
                "Crie uma função `parse_cnj_number(cnj_str: str) -> dict | None` que analisa o número de "
                "processo brasileiro no formato `NNNNNNN-DD.AAAA.J.TR.OOOO` e retorna um dicionário contendo: "
                "`sequencial`, `digito`, `ano`, `justica`, `tribunal`, `origem`. Retorna None se inválido."
            ),
            "test_code": (
                "from solution import parse_cnj_number\n\n"
                "def test_parse_cnj_number():\n"
                "    cnj = '0012345-67.2026.8.19.0001'\n"
                "    data = parse_cnj_number(cnj)\n"
                "    assert data is not None\n"
                "    assert data['sequencial'] == '0012345'\n"
                "    assert data['digito'] == '67'\n"
                "    assert data['ano'] == 2026\n"
                "    assert data['justica'] == '8'\n"
                "    assert data['tribunal'] == '19'\n"
                "    assert data['origem'] == '0001'\n\n"
                "    assert parse_cnj_number('formato_invalido') is None\n"
            ),
            "reference_solution": (
                "import re\n"
                "def parse_cnj_number(cnj_str: str):\n"
                "    m = re.match(r'^(\\d{7})-(\\d{2})\\.(\\d{4})\\.(\\d)\\.(\\d{2})\\.(\\d{4})$', cnj_str.strip())\n"
                "    if not m:\n"
                "        return None\n"
                "    return {\n"
                "        'sequencial': m.group(1),\n"
                "        'digito': m.group(2),\n"
                "        'ano': int(m.group(3)),\n"
                "        'justica': m.group(4),\n"
                "        'tribunal': m.group(5),\n"
                "        'origem': m.group(6),\n"
                "    }\n"
            ),
        },
    ],
}
