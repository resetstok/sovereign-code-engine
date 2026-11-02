# Masterbook Piloto: Pipeline Forense de Auditoria e Tokenização
Este documento especifica a esteira encadeada (DAG) de 3 microtarefas atômicas do motor soberano.

### TAREFA TASK_AUTH_001: Autenticação Segura de Nó
Arquivo-Alvo: `src/auth_token.py`
Privacidade: `PUBLIC`
Linhas: 20
Tipo: `EXTRACTION`

Implemente a função `generate_session_token(node_id: str, secret: str) -> str` que calcula o HMAC-SHA256 em hexadecimal.

```python
import hmac
import hashlib
from src.auth_token import generate_session_token

def test_token_generation():
    token = generate_session_token("NODE_01", "SECRET_KEY_123")
    expected = hmac.new(b"SECRET_KEY_123", b"NODE_01", hashlib.sha256).hexdigest()
    assert token == expected
```

### TAREFA TASK_STORE_002: Armazenamento em Memória com Limite
Target: `src/audit_store.py`
Privacy: `PUBLIC`
Lines: 28
Type: `CRUD`

Implemente a classe `AuditStore` com métodos `append(event: str)` e `get_events() -> list[str]`.
Capacidade máxima de 100 eventos (buffer circular).

```python
from src.audit_store import AuditStore

def test_store_lifecycle():
    store = AuditStore(max_items=3)
    store.append("E1")
    store.append("E2")
    store.append("E3")
    store.append("E4")
    assert store.get_events() == ["E2", "E3", "E4"]
```

### TAREFA TASK_AUDIT_003: Validação de Integridade Forense
Target: `src/forensic_validator.py`
Privacy: `STRICT_LOCAL`
Lines: 22
Type: `PARSER_OSINT`

Implemente `validate_integrity(data_bytes: bytes, expected_sha256: str) -> bool`.
Se for válido, retorna True; se corrompido, False.

```python
import hashlib
from src.forensic_validator import validate_integrity

def test_integrity_check():
    payload = b"AUDITORIA_SESAU_2026"
    h = hashlib.sha256(payload).hexdigest()
    assert validate_integrity(payload, h) is True
    assert validate_integrity(payload, "invalid_hash") is False
```
