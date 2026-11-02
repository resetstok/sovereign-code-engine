import json
from pathlib import Path

import pytest

from pci_engine import mtv_core as mtv

TASK_SRC = Path(__file__).resolve().parent.parent / "tasks" / "extrair_cnpj"
REFERENCE = (Path(__file__).resolve().parent / "reference" / "extrair_cnpj.py").read_text(encoding="utf-8")

ERRADO = "def extrair_cnpjs(texto):\n    return []\n"
LOOP_INFINITO = "def extrair_cnpjs(texto):\n    while True:\n        pass\n"


def fake_generator(responses):
    """Gerador falso: devolve respostas em sequencia (a ultima se repete)."""
    calls = {"n": 0, "prompts": []}

    def gen(prompt, temperature, seed):
        calls["prompts"].append(prompt)
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    gen.calls = calls
    return gen


def fenced(code):
    return f"Aqui esta:\n```python\n{code}```\nFim."


# ---- extracao de codigo -------------------------------------------------

def test_extract_code_de_bloco_com_cerca():
    assert mtv.extract_code(fenced("def f():\n    return 1\n")).startswith("def f()")


def test_extract_code_prefere_bloco_com_def():
    texto = "```\nexemplo de uso\n```\n```python\ndef f():\n    pass\n```"
    assert "def f()" in mtv.extract_code(texto)


def test_extract_code_resposta_cortada_sem_fechar():
    code = mtv.extract_code("```python\ndef f():\n    return 1\n")
    assert code.startswith("def f()") and "```" not in code


def test_extract_code_sem_cerca():
    assert mtv.extract_code("def f():\n    return 1") == "def f():\n    return 1"


# ---- oraculo ------------------------------------------------------------

def test_referencia_passa_nos_testes_da_tarefa():
    result = mtv.run_candidate(TASK_SRC, REFERENCE, timeout=30)
    assert result.passed, result.tail


def test_candidato_errado_falha_nos_testes_da_tarefa():
    assert not mtv.run_candidate(TASK_SRC, ERRADO, timeout=30).passed


def test_timeout_e_tratado_como_falha():
    result = mtv.run_candidate(TASK_SRC, LOOP_INFINITO, timeout=3)
    assert not result.passed and result.tail == "timeout"


# ---- run_task -----------------------------------------------------------

def test_aceita_primeiro_candidato_que_passa(tmp_path):
    gen = fake_generator([fenced(ERRADO), fenced(ERRADO), fenced(REFERENCE)])
    ok = mtv.run_task(TASK_SRC, gen, model="fake", n=5, timeout=30, out_dir=tmp_path, log=lambda _: None)
    assert ok
    assert gen.calls["n"] == 3  # parou no 3o
    assert (tmp_path / "accepted" / "extrair_cnpj" / "solution.py").read_text().strip() == REFERENCE.strip()
    rows = mtv.load_results(tmp_path / "results.jsonl")
    assert [r["passed"] for r in rows] == [False, False, True]


def test_todas_falham_nao_aceita_nada(tmp_path):
    gen = fake_generator([fenced(ERRADO)])
    ok = mtv.run_task(TASK_SRC, gen, model="fake", n=3, timeout=30, out_dir=tmp_path, log=lambda _: None)
    assert not ok
    assert not (tmp_path / "accepted").exists()
    assert len(mtv.load_results(tmp_path / "results.jsonl")) == 3


def test_measure_roda_todas_as_tentativas(tmp_path):
    gen = fake_generator([fenced(REFERENCE), fenced(ERRADO)])
    ok = mtv.run_task(TASK_SRC, gen, model="fake", n=4, timeout=30, out_dir=tmp_path,
                      measure=True, log=lambda _: None)
    assert ok and gen.calls["n"] == 4
    rows = mtv.load_results(tmp_path / "results.jsonl")
    assert sum(r["passed"] for r in rows) == 1


def test_feedback_inclui_erro_mas_nao_o_codigo_anterior(tmp_path):
    gen = fake_generator([fenced(ERRADO), fenced(REFERENCE)])
    mtv.run_task(TASK_SRC, gen, model="fake", n=3, timeout=30, out_dir=tmp_path,
                 feedback=True, log=lambda _: None)
    primeiro, segundo = gen.calls["prompts"][:2]
    assert "Erro de uma tentativa anterior" not in primeiro
    assert "Erro de uma tentativa anterior" in segundo
    assert "return []" not in segundo.split("Erro de uma tentativa anterior")[1]


def test_gerador_fora_do_ar_aborta(tmp_path):
    def quebrado(prompt, temperature, seed):
        raise mtv.GeneratorError("sem conexao")

    with pytest.raises(mtv.GeneratorError):
        mtv.run_task(TASK_SRC, quebrado, model="fake", n=2, timeout=30, out_dir=tmp_path, log=lambda _: None)


def test_tarefa_sem_testes_e_rejeitada(tmp_path):
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    (vazia / "task.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        mtv.run_task(vazia, fake_generator([""]), model="fake", out_dir=tmp_path / "out", log=lambda _: None)


# ---- relatorio e CLI ----------------------------------------------------

def test_relatorio_calcula_taxas():
    rows = [
        {"model": "m", "task": "t1", "passed": True},
        {"model": "m", "task": "t1", "passed": False},
        {"model": "m", "task": "t2", "passed": False},
        {"model": "m", "task": "t2", "passed": False},
    ]
    s = {x["task"]: x for x in mtv.summarize(rows)}
    assert s["t1"]["rate"] == 0.5 and s["t1"]["ever_passed"]
    assert s["t2"]["rate"] == 0.0 and not s["t2"]["ever_passed"]


def test_discover_tasks(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "task.md").write_text("x", encoding="utf-8")
    (tmp_path / "b").mkdir()
    assert [p.name for p in mtv.discover_tasks(tmp_path)] == ["a"]
    assert mtv.discover_tasks(tmp_path / "a") == [tmp_path / "a"]


def test_cli_new_cria_esqueleto(tmp_path, capsys):
    rc = mtv.main(["new", "minha_tarefa", "--tasks", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "minha_tarefa" / "task.md").is_file()
    assert (tmp_path / "minha_tarefa" / "test_solution.py").is_file()
    assert mtv.main(["new", "minha_tarefa", "--tasks", str(tmp_path)]) == 1


def test_cli_report(tmp_path, capsys):
    (tmp_path / "results.jsonl").write_text(
        json.dumps({"model": "m", "task": "t", "passed": True}) + "\n", encoding="utf-8"
    )
    assert mtv.main(["report", "--out", str(tmp_path)]) == 0
    assert "taxa por tentativa 100%" in capsys.readouterr().out
