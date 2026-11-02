#!/usr/bin/env python3
"""mtv - Micro-Tarefas Verificadas.

Para cada micro-tarefa (especificacao + testes), pede N candidatos a um modelo
local (Ollama) e deixa o pytest decidir. O primeiro candidato que passa nos
testes vira a solucao aceita. Tudo fica registrado em results.jsonl.

So usa a biblioteca padrao do Python; o pytest precisa estar instalado.

AVISO DE SEGURANCA: o codigo gerado e executado nesta maquina, sem sandbox.
Use apenas com micro-tarefas suas e com fixtures sinteticas ou anonimizadas.
Nao execute candidatos vindos de outras maquinas sem isolamento proprio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SYSTEM = (
    "Voce e um programador Python. Escreva APENAS o conteudo completo do arquivo "
    "solution.py, em Python 3, usando so a biblioteca padrao salvo se a tarefa "
    "disser outra coisa. Nao escreva testes, nao explique, nao use markdown "
    "alem de um unico bloco ```python. Mantenha o codigo curto e simples."
)

FENCE = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\n(.*?)```", re.DOTALL)
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

Generator = Callable[[str, float, int], str]


class GeneratorError(RuntimeError):
    """Falha ao obter texto do modelo (ex.: Ollama fora do ar)."""


# --------------------------------------------------------------------------
# Geracao
# --------------------------------------------------------------------------

def extract_code(text: str) -> str:
    """Extrai o codigo Python de uma resposta do modelo."""
    blocks = FENCE.findall(text)
    for block in blocks:
        if "def " in block or "class " in block:
            return block.strip("\n")
    if blocks:
        return blocks[0].strip("\n")
    # Resposta cortada: abriu o bloco e nao fechou.
    cleaned = re.sub(r"^\s*```[a-zA-Z0-9_+-]*[ \t]*\n", "", text)
    cleaned = re.sub(r"\n```\s*$", "", cleaned)
    return cleaned.strip("\n")


def build_prompt(task_dir: Path, feedback: Optional[str] = None) -> str:
    spec = (task_dir / "task.md").read_text(encoding="utf-8").strip()
    tests = "\n\n".join(
        f"# {p.name}\n{p.read_text(encoding='utf-8').strip()}"
        for p in sorted(task_dir.glob("test_*.py"))
    )
    parts = [
        f"## Tarefa\n{spec}",
        f"## Testes que o codigo precisa passar\n```python\n{tests}\n```",
    ]
    if feedback:
        parts.append(
            "## Erro de uma tentativa anterior (corrija a causa; nao copie o erro)\n"
            f"```\n{feedback.strip()}\n```"
        )
    parts.append("Responda somente com o conteudo completo de solution.py em um bloco ```python.")
    return "\n\n".join(parts)


def ollama_generator(
    model: str,
    host: str = "http://localhost:11434",
    num_ctx: int = 4096,
    num_predict: int = 768,
    timeout: float = 900,
) -> Generator:
    """Gerador que chama a API local do Ollama (/api/chat, sem streaming)."""

    def generate(prompt: str, temperature: float, seed: int) -> str:
        body = json.dumps(
            {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "options": {
                    "temperature": temperature,
                    "seed": seed,
                    "num_ctx": num_ctx,
                    "num_predict": num_predict,
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            host.rstrip("/") + "/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            raise GeneratorError(f"falha ao chamar o Ollama em {host}: {exc}") from exc
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise GeneratorError(f"resposta inesperada do Ollama: {str(data)[:200]}") from exc

    return generate


# --------------------------------------------------------------------------
# Verificacao
# --------------------------------------------------------------------------

@dataclass
class RunResult:
    passed: bool
    seconds: float
    returncode: int
    tail: str


def run_candidate(task_dir: Path, code: str, timeout: float = 20.0) -> RunResult:
    """Roda os testes da tarefa contra um candidato, em pasta temporaria."""
    with tempfile.TemporaryDirectory(prefix="mtv_") as tmp:
        work = Path(tmp)
        (work / "solution.py").write_text(code, encoding="utf-8")
        for test_file in task_dir.glob("test_*.py"):
            shutil.copy(test_file, work / test_file.name)
        fixtures = task_dir / "fixtures"
        if fixtures.is_dir():
            shutil.copytree(fixtures, work / "fixtures")
        env = {
            k: v for k, v in os.environ.items()
            if not any(hint in k.upper() for hint in SECRET_HINTS)
        }
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--tb=short"]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, cwd=work, capture_output=True, text=True, timeout=timeout, env=env
            )
        except subprocess.TimeoutExpired:
            return RunResult(False, time.monotonic() - start, -1, "timeout")
        output = (proc.stdout or "") + (proc.stderr or "")
        return RunResult(proc.returncode == 0, time.monotonic() - start, proc.returncode, output[-600:])


# --------------------------------------------------------------------------
# Registro e execucao
# --------------------------------------------------------------------------

def _append_result(results_path: Path, row: dict) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_task(
    task_dir: Path,
    generator: Generator,
    *,
    model: str,
    n: int = 8,
    temps: tuple[float, ...] = (0.2, 0.5, 0.8),
    timeout: float = 20.0,
    out_dir: Path = Path("out"),
    feedback: bool = False,
    measure: bool = False,
    log: Callable[[str], None] = print,
) -> bool:
    """Tenta ate N candidatos. Retorna True se algum passou nos testes.

    measure=True roda todas as N tentativas mesmo apos o primeiro acerto, para
    medir a taxa de acerto por tentativa (a base da matriz de capacidade).
    """
    task_id = task_dir.name
    if not (task_dir / "task.md").is_file() or not list(task_dir.glob("test_*.py")):
        raise ValueError(f"{task_dir}: precisa de task.md e ao menos um test_*.py")

    results_path = out_dir / "results.jsonl"
    accepted_code: Optional[str] = None
    last_tail: Optional[str] = None

    for attempt in range(1, n + 1):
        temperature = temps[(attempt - 1) % len(temps)]
        prompt = build_prompt(task_dir, last_tail if feedback else None)
        gen_start = time.monotonic()
        raw = generator(prompt, temperature, attempt)  # GeneratorError sobe e aborta
        gen_seconds = time.monotonic() - gen_start
        code = extract_code(raw)
        result = run_candidate(task_dir, code, timeout)
        if not result.passed:
            last_tail = result.tail

        attempts_dir = out_dir / "attempts" / task_id
        attempts_dir.mkdir(parents=True, exist_ok=True)
        (attempts_dir / f"{attempt:02d}_{'ok' if result.passed else 'falhou'}.py").write_text(
            code, encoding="utf-8"
        )
        _append_result(
            results_path,
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": task_id,
                "attempt": attempt,
                "model": model,
                "temperature": temperature,
                "passed": result.passed,
                "gen_seconds": round(gen_seconds, 2),
                "test_seconds": round(result.seconds, 2),
                "returncode": result.returncode,
                "code_sha": hashlib.sha256(code.encode("utf-8")).hexdigest()[:12],
                "error_tail": "" if result.passed else result.tail,
            },
        )
        log(f"[{task_id}] tentativa {attempt}/{n} (temp {temperature}): "
            f"{'PASSOU' if result.passed else 'falhou'}")

        if result.passed and accepted_code is None:
            accepted_code = code
            target = out_dir / "accepted" / task_id
            target.mkdir(parents=True, exist_ok=True)
            (target / "solution.py").write_text(code, encoding="utf-8")
            if not measure:
                break

    return accepted_code is not None


def git_commit(paths: list[Path], message: str, log: Callable[[str], None] = print) -> None:
    """Commit opcional dos arquivos aceitos. Falha em silencio se nao houver git."""
    try:
        subprocess.run(["git", "add", *map(str, paths)], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True)
        log(f"commit: {message}")
    except (OSError, subprocess.CalledProcessError) as exc:
        log(f"commit ignorado ({exc.__class__.__name__})")


def discover_tasks(path: Path) -> list[Path]:
    if (path / "task.md").is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(d for d in path.iterdir() if (d / "task.md").is_file())


# --------------------------------------------------------------------------
# Relatorio
# --------------------------------------------------------------------------

def load_results(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["model"], row["task"]), []).append(row)
    summary = []
    for (model, task), items in sorted(groups.items()):
        passes = sum(1 for r in items if r["passed"])
        summary.append(
            {
                "model": model,
                "task": task,
                "attempts": len(items),
                "passes": passes,
                "rate": passes / len(items),
                "ever_passed": passes > 0,
            }
        )
    return summary


def print_report(rows: list[dict], log: Callable[[str], None] = print) -> None:
    summary = summarize(rows)
    if not summary:
        log("Sem resultados ainda.")
        return
    log(f"{'modelo':<28}{'tarefa':<26}{'tent.':>6}{'acertos':>9}{'taxa':>8}")
    for s in summary:
        log(f"{s['model']:<28}{s['task']:<26}{s['attempts']:>6}{s['passes']:>9}{s['rate']:>8.0%}")
    by_model: dict[str, list[dict]] = {}
    for s in summary:
        by_model.setdefault(s["model"], []).append(s)
    log("")
    for model, items in by_model.items():
        attempts = sum(i["attempts"] for i in items)
        passes = sum(i["passes"] for i in items)
        solved = sum(1 for i in items if i["ever_passed"])
        log(f"{model}: taxa por tentativa {passes / attempts:.0%}; "
            f"tarefas resolvidas {solved}/{len(items)}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

TASK_TEMPLATE = """# {name}

Descreva aqui, em portugues e em poucas linhas, UMA funcao Python a escrever.

Assinatura: `def nome(entrada: tipo) -> tipo`

Regras:
- regra 1
- regra 2
"""

TEST_TEMPLATE = '''from solution import nome  # troque pelo nome da funcao


def test_caso_basico():
    assert nome("entrada") == "saida"
'''


def cmd_new(args: argparse.Namespace) -> int:
    task_dir = Path(args.tasks) / args.name
    if task_dir.exists():
        print(f"{task_dir} ja existe.")
        return 1
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(TASK_TEMPLATE.format(name=args.name), encoding="utf-8")
    (task_dir / "test_solution.py").write_text(TEST_TEMPLATE, encoding="utf-8")
    print(f"Tarefa criada em {task_dir}. Edite task.md e test_solution.py.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    tasks = discover_tasks(Path(args.path))
    if not tasks:
        print(f"Nenhuma tarefa (pasta com task.md) em {args.path}")
        return 1
    temps = tuple(float(t) for t in args.temps.split(","))
    out_dir = Path(args.out)
    generator = ollama_generator(
        args.model, host=args.host, num_ctx=args.num_ctx, num_predict=args.num_predict
    )
    solved = 0
    try:
        for task in tasks:
            ok = run_task(
                task, generator, model=args.model, n=args.n, temps=temps,
                timeout=args.timeout, out_dir=out_dir, feedback=args.feedback,
                measure=args.measure,
            )
            solved += ok
            if ok and args.commit:
                git_commit(
                    [out_dir / "accepted" / task.name, out_dir / "results.jsonl"],
                    f"mtv: {task.name} aprovada ({args.model})",
                )
    except GeneratorError as exc:
        print(f"Erro: {exc}\nO Ollama esta rodando? (ollama serve) O modelo existe? (ollama pull {args.model})")
        return 2
    print(f"\nTarefas resolvidas: {solved}/{len(tasks)}. Detalhes em {out_dir / 'results.jsonl'}")
    return 0 if solved == len(tasks) else 3


def cmd_report(args: argparse.Namespace) -> int:
    print_report(load_results(Path(args.out) / "results.jsonl"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtv", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="gera candidatos e testa")
    run.add_argument("path", help="pasta de uma tarefa ou pasta com varias tarefas")
    run.add_argument("--model", default="qwen2.5-coder:3b")
    run.add_argument("--n", type=int, default=8, help="tentativas por tarefa")
    run.add_argument("--temps", default="0.2,0.5,0.8", help="temperaturas alternadas")
    run.add_argument("--timeout", type=float, default=20.0, help="segundos por teste")
    run.add_argument("--out", default="out")
    run.add_argument("--host", default="http://localhost:11434")
    run.add_argument("--num-ctx", dest="num_ctx", type=int, default=4096)
    run.add_argument("--num-predict", dest="num_predict", type=int, default=768)
    run.add_argument("--feedback", action="store_true",
                     help="inclui o erro curto da tentativa anterior (sem o codigo anterior)")
    run.add_argument("--measure", action="store_true",
                     help="roda todas as N tentativas para medir a taxa de acerto")
    run.add_argument("--commit", action="store_true", help="git commit das tarefas aceitas")
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="resume results.jsonl")
    report.add_argument("--out", default="out")
    report.set_defaults(func=cmd_report)

    new = sub.add_parser("new", help="cria o esqueleto de uma tarefa")
    new.add_argument("name")
    new.add_argument("--tasks", default="tasks")
    new.set_defaults(func=cmd_new)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
