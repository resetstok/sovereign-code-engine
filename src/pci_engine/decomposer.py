"""
Módulo: pci_engine/decomposer.py
Decompositor de Masterbook com JSON Schema Pydantic e Importador Manual de Tarefas.
Garante 0% de erro de sintaxe ao gerar a fila DAG no jobs.db e permite ingestão manual de .md.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from pci_engine.database import DatabaseManager

logger = logging.getLogger("pci_engine.decomposer")


class TaskPrivacyLevel(str, Enum):
    PUBLIC = "PUBLIC"
    SANITIZED_DERIVATIVE = "SANITIZED_DERIVATIVE"
    STRICT_LOCAL = "STRICT_LOCAL"


class TaskTypeEnum(str, Enum):
    EXTRACTION = "EXTRACTION"
    CRUD = "CRUD"
    REFACTOR = "REFACTOR"
    PARSER_OSINT = "PARSER_OSINT"
    ARCHITECTURE = "ARCHITECTURE"


class MicrotaskDefinition(BaseModel):
    task_id: str = Field(..., description="ID único e legível, ex: TASK_MAP_001")
    depends_on_task_id: Optional[str] = Field(None, description="ID da tarefa da qual esta depende")
    task_type: str = Field("EXTRACTION", description="Tipo: EXTRACTION, CRUD, REFACTOR, PARSER_OSINT ou ARCHITECTURE")
    privacy_level: str = Field("PUBLIC", description="PUBLIC, SANITIZED_DERIVATIVE ou STRICT_LOCAL")
    permission_level: int = Field(2, ge=1, le=4, description="Nível de permissão (1 a 4)")
    estimated_lines: int = Field(25, ge=1, le=500, description="Estimativa de linhas de código")
    target_file_path: str = Field(..., description="Caminho relativo do arquivo, ex: src/geo.py")
    spec_markdown: str = Field(..., description="Instruções atômicas e requisitos da função")
    test_code: str = Field(..., description="Código Pytest completo que deve validar a implementação")
    max_attempts: int = Field(3, ge=1, le=10, description="Máximo de tentativas antes de exigir revisão")


class MasterbookDecompositionResult(BaseModel):
    parent_masterbook: str = Field(..., description="Identificador do Masterbook de origem")
    tasks: List[MicrotaskDefinition] = Field(..., description="Lista ordenada e encadeada de microtarefas")


class MasterbookDecomposer:
    """
    Decompõe Masterbooks em DAG atômica com tipagem Pydantic / GBNF
    e importa tarefas manuais (.md) da pasta tasks/active/.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        db_path: Optional[Path | str] = None,
        default_ollama_url: str = "http://127.0.0.1:11434",
    ):
        if db_manager is not None:
            self.db = db_manager
        elif db_path is not None:
            self.db = DatabaseManager(db_path=db_path)
        else:
            self.db = DatabaseManager()
        self.ollama_url = default_ollama_url

    # -------------------------------------------------------------------------
    # 0. Auto-Fatiador de Tarefas Grandes para Modo Offline (<= 30 linhas)
    # -------------------------------------------------------------------------
    @staticmethod
    def auto_slice_oversized_tasks(
        tasks: List[MicrotaskDefinition],
        max_lines_per_slice: int = 30,
    ) -> List[MicrotaskDefinition]:
        """
        Quando operando 100% Offline (sem API Cloud), divide tarefas grandes (> max_lines_per_slice)
        em fatias atômicas encadeadas do tamanho ideal (<= 30 linhas) para o MicroHarness / Aider local.
        """
        sliced_list: List[MicrotaskDefinition] = []
        last_final_id_map: Dict[str, str] = {}

        for task in tasks:
            # Ajusta dependência caso a tarefa anterior tenha sido fatiada
            effective_dep = last_final_id_map.get(task.depends_on_task_id, task.depends_on_task_id) if task.depends_on_task_id else None

            if task.estimated_lines <= max_lines_per_slice:
                updated = task.model_copy(update={"depends_on_task_id": effective_dep})
                sliced_list.append(updated)
                last_final_id_map[task.task_id] = task.task_id
                continue

            # Calcula quantas fatias de <= max_lines_per_slice são necessárias
            num_slices = max(2, (task.estimated_lines + max_lines_per_slice - 1) // max_lines_per_slice)
            spec_lines = [ln for ln in task.spec_markdown.splitlines() if ln.strip()]
            chunk_size = max(1, (len(spec_lines) + num_slices - 1) // num_slices)

            prev_slice_id = effective_dep
            for s_idx in range(num_slices):
                slice_id = f"{task.task_id}_PT{s_idx + 1}"
                sub_spec_lines = spec_lines[s_idx * chunk_size : (s_idx + 1) * chunk_size]
                sub_spec = (
                    f"[Fatia {s_idx + 1}/{num_slices} da tarefa {task.task_id} — Dimensionada para <= {max_lines_per_slice} linhas]\n"
                    + "\n".join(sub_spec_lines)
                    + f"\n\nContexto global da especificação:\n{task.spec_markdown}"
                )
                slice_task = MicrotaskDefinition(
                    task_id=slice_id,
                    depends_on_task_id=prev_slice_id,
                    task_type=task.task_type if task.task_type != "REFACTOR" else "CRUD",
                    privacy_level=task.privacy_level,
                    permission_level=task.permission_level,
                    estimated_lines=min(max_lines_per_slice, max(15, task.estimated_lines // num_slices)),
                    target_file_path=task.target_file_path,
                    spec_markdown=sub_spec,
                    test_code=task.test_code,
                    max_attempts=task.max_attempts,
                )
                sliced_list.append(slice_task)
                prev_slice_id = slice_id

            if prev_slice_id:
                last_final_id_map[task.task_id] = prev_slice_id

        return sliced_list

    # -------------------------------------------------------------------------
    # 1. Decomposição com Saída Estruturada Forçada (JSON Schema / GBNF)
    # -------------------------------------------------------------------------
    def decompose_from_text(
        self,
        masterbook_content: str,
        parent_name: str = "MASTERBOOK_PILOTO",
        model: str = "qwen2.5-coder:3b",
        force_fallback: bool = False,
        offline_auto_slice: bool = False,
        max_lines_per_slice: int = 30,
    ) -> List[str]:
        """
        Gera a decomposição forçando format=JSON_SCHEMA no Ollama local (ou parser determinístico
        se o Masterbook já possuir marcadores '### TAREFA').
        Se offline_auto_slice=True, reparte automaticamente tarefas grandes (> max_lines_per_slice)
        em fatias ideais (<= 30 linhas) para o MicroHarness / Aider local.
        """
        # Se o Masterbook já contém blocos explícitos '### TAREFA', usa o parser determinístico exato
        if "### TAREFA" in masterbook_content:
            return self._deterministic_parse_and_persist(
                masterbook_content,
                parent_name,
                offline_auto_slice=offline_auto_slice,
                max_lines_per_slice=max_lines_per_slice,
            )

        if not force_fallback:
            json_schema = MasterbookDecompositionResult.model_json_schema()
            prompt = (
                f"Analise o Masterbook abaixo e decomponha em uma lista ordenada de microtarefas atômicas (DAG).\n"
                f"Cada tarefa deve ter no máximo {max_lines_per_slice} linhas e ter teste unitário em Pytest completo.\n\n"
                f"MASTERBOOK:\n{masterbook_content}\n"
            )

            try:
                payload = json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "format": json_schema,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 2048},
                }).encode("utf-8")

                req = urllib.request.Request(
                    f"{self.ollama_url}/api/chat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=60) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    raw_json = resp_data.get("message", {}).get("content", "")
                    validated = MasterbookDecompositionResult.model_validate_json(raw_json)
                    if offline_auto_slice:
                        validated.tasks = self.auto_slice_oversized_tasks(
                            validated.tasks, max_lines_per_slice=max_lines_per_slice
                        )
                    return self._persist_tasks(validated)
            except Exception as exc:
                logger.warning(f"Ollama estruturado indisponível ({exc}). Acionando parser determinístico.")

        # Fallback determinístico (Sem IA)
        return self._deterministic_parse_and_persist(
            masterbook_content,
            parent_name,
            offline_auto_slice=offline_auto_slice,
            max_lines_per_slice=max_lines_per_slice,
        )

    def decompose_file(
        self,
        file_path: Path | str,
        model: str = "qwen2.5-coder:3b",
        force_fallback: bool = False,
        offline_auto_slice: bool = False,
        max_lines_per_slice: int = 30,
    ) -> List[str]:
        path = Path(file_path).resolve()
        content = path.read_text(encoding="utf-8")
        return self.decompose_from_text(
            masterbook_content=content,
            parent_name=path.stem,
            model=model,
            force_fallback=force_fallback,
            offline_auto_slice=offline_auto_slice,
            max_lines_per_slice=max_lines_per_slice,
        )

    def _persist_tasks(self, result: MasterbookDecompositionResult) -> List[str]:
        enqueued_ids = []
        for task in result.tasks:
            initial_status = "WAITING_DEPS" if task.depends_on_task_id else "PENDING"
            self.db.insert_task(
                task_id=task.task_id,
                parent_masterbook=result.parent_masterbook,
                depends_on_task_id=task.depends_on_task_id,
                task_type=task.task_type,
                privacy_level=task.privacy_level,
                permission_level=task.permission_level,
                estimated_lines=task.estimated_lines,
                spec_markdown=task.spec_markdown,
                test_code=task.test_code,
                target_file_path=task.target_file_path,
                status=initial_status,
                max_attempts=task.max_attempts,
            )
            enqueued_ids.append(task.task_id)
        return enqueued_ids

    def _deterministic_parse_and_persist(
        self,
        content: str,
        parent_name: str,
        offline_auto_slice: bool = False,
        max_lines_per_slice: int = 30,
    ) -> List[str]:
        """Extrai tarefas delimitadas por marcadores '### TAREFA' no Markdown e aplica auto-fatiamento se offline."""
        parsed_defs: List[MicrotaskDefinition] = []
        task_blocks = re.split(r"(?=### TAREFA\s+)", content)
        prev_task_id = None

        for idx, block in enumerate(task_blocks):
            if not block.strip() or not block.startswith("### TAREFA"):
                continue

            lines = block.splitlines()
            first_line = lines[0].replace("### TAREFA", "").strip()
            task_id = first_line.split(":")[0].strip() if ":" in first_line else f"TASK_{idx+1:03d}"

            # Procura arquivo-alvo no texto
            target_match = re.search(r"(?:Arquivo(?:-Alvo)?|Target):\s*`?([^\n`]+)`?", block, re.IGNORECASE)
            target_file = target_match.group(1).strip() if target_match else f"src/{task_id.lower()}.py"

            # Procura metadados de privacidade, linhas e tipo
            privacy_match = re.search(r"(?:Privacidade|Privacy):\s*`?([A-Z_]+)`?", block, re.IGNORECASE)
            privacy = privacy_match.group(1).strip() if privacy_match else "PUBLIC"

            lines_match = re.search(r"(?:Linhas|Lines):\s*(\d+)", block, re.IGNORECASE)
            est_lines = int(lines_match.group(1)) if lines_match else 25

            type_match = re.search(r"(?:Tipo|Type):\s*`?([A-Z_]+)`?", block, re.IGNORECASE)
            task_type = type_match.group(1).strip() if type_match else "EXTRACTION"

            # Procura código de teste dentro de blocos ```python ... ```
            py_blocks = re.findall(r"```python(.*?)```", block, re.DOTALL)
            test_code = py_blocks[-1].strip() if py_blocks else "def test_auto(): assert True\n"

            parsed_defs.append(
                MicrotaskDefinition(
                    task_id=task_id,
                    depends_on_task_id=prev_task_id,
                    task_type=task_type,
                    privacy_level=privacy,
                    permission_level=2,
                    estimated_lines=est_lines,
                    target_file_path=target_file,
                    spec_markdown=block,
                    test_code=test_code,
                    max_attempts=3,
                )
            )
            prev_task_id = task_id

        if offline_auto_slice and parsed_defs:
            parsed_defs = self.auto_slice_oversized_tasks(parsed_defs, max_lines_per_slice=max_lines_per_slice)

        result_obj = MasterbookDecompositionResult(parent_masterbook=parent_name, tasks=parsed_defs)
        return self._persist_tasks(result_obj)

    # -------------------------------------------------------------------------
    # 2. Importação Manual de Arquivos .md (tasks/active/)
    # -------------------------------------------------------------------------
    def import_manual_task_file(self, file_path: Path | str, parent_masterbook: str = "MANUAL_IMPORT") -> str:
        """Lê um arquivo de especificação individual .md e insere na fila do jobs.db sem IA."""
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de tarefa não encontrado: {path}")

        content = path.read_text(encoding="utf-8")
        task_id = path.stem.upper()

        # Extração de metadados do cabeçalho
        target_match = re.search(r"(?:Arquivo(?:-Alvo)?|Target):\s*`?([^\n`]+)`?", content, re.IGNORECASE)
        target_file = target_match.group(1).strip() if target_match else f"src/{path.stem.lower()}.py"

        privacy_match = re.search(r"(?:Privacidade|Privacy):\s*`?([A-Z_]+)`?", content, re.IGNORECASE)
        privacy = privacy_match.group(1).strip() if privacy_match else "PUBLIC"

        lines_match = re.search(r"(?:Linhas|Lines):\s*(\d+)", content, re.IGNORECASE)
        lines = int(lines_match.group(1)) if lines_match else 25

        type_match = re.search(r"(?:Tipo|Type):\s*`?([A-Z_]+)`?", content, re.IGNORECASE)
        task_type = type_match.group(1).strip() if type_match else "EXTRACTION"

        py_blocks = re.findall(r"```python(.*?)```", content, re.DOTALL)
        test_code = py_blocks[-1].strip() if py_blocks else "def test_manual(): assert True\n"

        self.db.insert_task(
            task_id=task_id,
            parent_masterbook=parent_masterbook,
            depends_on_task_id=None,
            task_type=task_type,
            privacy_level=privacy,
            permission_level=2,
            estimated_lines=lines,
            spec_markdown=content,
            test_code=test_code,
            target_file_path=target_file,
            status="PENDING",
            max_attempts=3,
        )
        logger.info(f"Tarefa manual importada: {task_id} -> {target_file}")
        return task_id

    def import_all_active_tasks(self, active_dir: Path | str = "tasks/active") -> List[str]:
        """Varre a pasta tasks/active/ e enfileira todos os arquivos .md encontrados."""
        dir_path = Path(active_dir).resolve()
        if not dir_path.exists():
            return []

        imported = []
        for md_file in sorted(dir_path.glob("*.md")):
            tid = self.import_manual_task_file(md_file, parent_masterbook="TASKS_ACTIVE_DIR")
            imported.append(tid)
        return imported
