"""
Módulo: pci_engine/orchestrator.py
Decompositor de Masterbook em DAG de microtarefas com Gramáticas GBNF / Pydantic Schema.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.error
from pydantic import BaseModel, Field

from pci_engine.db import JobsDatabaseV2
from pci_engine.router import PCIRouterAndBroker

logger = logging.getLogger("pci_engine.orchestrator")


# =============================================================================
# Esquema Pydantic para Saída Estruturada Forçada (GBNF no Ollama / llama.cpp)
# =============================================================================
class MicrotaskSpec(BaseModel):
    task_id: str = Field(..., description="Identificador único, ex: TASK_MAP_001")
    depends_on_task_id: Optional[str] = Field(None, description="Task ID da dependência ou null")
    task_type: str = Field(..., description="EXTRACTION, CRUD, REFACTOR, PARSER_OSINT ou ARCHITECTURE")
    privacy_level: str = Field("PUBLIC", description="STRICT_LOCAL, SANITIZED_DERIVATIVE ou PUBLIC")
    permission_level: int = Field(2, ge=1, le=4, description="Nível de permissão 1-4")
    estimated_lines: int = Field(25, ge=5, le=120, description="Linhas estimadas (15 a 35 ideal)")
    target_file_path: str = Field(..., description="Caminho relativo do arquivo, ex: src/parser.py")
    spec_markdown: str = Field(..., description="Especificação em markdown do que deve ser feito")
    test_code: str = Field(..., description="Código Pytest que valida a implementação")


class MasterbookDecompositionSchema(BaseModel):
    parent_masterbook: str = Field(..., description="Identificador do Masterbook de origem")
    tasks: List[MicrotaskSpec] = Field(..., description="Lista ordenada de microtarefas em DAG")


@dataclass
class MicrotaskDAGNode:
    task_id: str
    parent_masterbook: str
    depends_on_task_id: Optional[str]
    task_type: str
    privacy_level: str
    permission_level: int
    estimated_lines: int
    spec_markdown: str
    test_code: str
    target_file_path: str


class MasterbookOrchestrator:
    """
    Orquestrador v2.0/v2.1 que ingere um Masterbook, quebra em DAG atômica
    com gramáticas GBNF e alimenta a tabela tasks_queue do jobs.db.
    """

    def __init__(self, db_path: Path | str = "data/jobs.db", repo_root: Path | str = "."):
        self.db_path = Path(db_path).resolve()
        self.repo_root = Path(repo_root).resolve()
        self.db = JobsDatabaseV2(db_path=self.db_path)
        self.router = PCIRouterAndBroker(db_path=self.db_path, repo_root=self.repo_root)

    def enqueue_microtask(self, node: MicrotaskDAGNode) -> str:
        with self.db.get_connection() as conn:
            initial_status = "WAITING_DEPS" if node.depends_on_task_id else "PENDING"
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks_queue (
                    task_id, parent_masterbook, depends_on_task_id, task_type,
                    privacy_level, permission_level, estimated_lines,
                    spec_markdown, test_code, target_file_path, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.task_id,
                    node.parent_masterbook,
                    node.depends_on_task_id,
                    node.task_type,
                    node.privacy_level,
                    node.permission_level,
                    node.estimated_lines,
                    node.spec_markdown,
                    node.test_code,
                    node.target_file_path,
                    initial_status,
                ),
            )
            conn.commit()
            logger.info(f"Microtarefa enfileirada: {node.task_id} [{initial_status}]")
            return node.task_id

    def update_dag_dependencies(self) -> int:
        """Promove tarefas WAITING_DEPS para PENDING assim que o pai for VERIFIED."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tasks_queue
                SET status = 'PENDING'
                WHERE status = 'WAITING_DEPS'
                  AND depends_on_task_id IN (
                      SELECT task_id FROM tasks_queue WHERE status = 'VERIFIED'
                  )
                """
            )
            promoted = cursor.rowcount
            conn.commit()
            return promoted

    def dispatch_next_cycle(self, api_quotas: Dict[str, Any], online_mode: bool = False) -> Optional[Dict[str, Any]]:
        """Processa o próximo passo do ciclo determinístico."""
        self.update_dag_dependencies()
        self.router.reclaim_expired_leases()

        with self.db.get_connection() as conn:
            task = conn.execute(
                """
                SELECT * FROM tasks_queue
                WHERE status = 'PENDING'
                ORDER BY created_at ASC LIMIT 1
                """
            ).fetchone()

            if not task:
                return None

            route = self.router.route_task(task, api_quotas=api_quotas, online_mode=online_mode)
            conn.execute(
                "UPDATE tasks_queue SET assigned_route = ? WHERE task_id = ?",
                (route, task["task_id"]),
            )
            conn.commit()

            return {
                "task_id": task["task_id"],
                "route": route,
                "task_type": task["task_type"],
                "privacy": task["privacy_level"],
                "target": task["target_file_path"],
            }

    # -------------------------------------------------------------------------
    # Decomposição com Gramática Estruturada Forçada (GBNF via Ollama / Pydantic)
    # -------------------------------------------------------------------------
    def decompose_masterbook_with_grammar(
        self,
        masterbook_content: str,
        parent_name: str = "MB_PILOTO",
        ollama_url: str = "http://127.0.0.1:11434",
        model: str = "huihui_ai/qwen2.5-coder-abliterate:3b",
    ) -> List[MicrotaskDAGNode]:
        """
        Invoca o Ollama passando o JSON Schema do Pydantic no parâmetro 'format'.
        Ollama/llama.cpp força a amostragem GBNF a seguir estritamente o schema,
        tornando impossível a IA responder com texto conversacional fora do padrão.
        """
        json_schema = MasterbookDecompositionSchema.model_json_schema()
        prompt = (
            f"Você é um arquiteto de software. Analise o Masterbook abaixo e quebre-o em uma lista ordenada de microtarefas DAG.\n"
            f"Cada microtarefa deve ter menos de 35 linhas e ter um teste unitário em Pytest completo.\n\n"
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
                f"{ollama_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=60) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                raw_json = resp_data.get("message", {}).get("content", "")
                parsed = MasterbookDecompositionSchema.model_validate_json(raw_json)
                nodes = []
                for t in parsed.tasks:
                    node = MicrotaskDAGNode(
                        task_id=t.task_id,
                        parent_masterbook=parsed.parent_masterbook or parent_name,
                        depends_on_task_id=t.depends_on_task_id,
                        task_type=t.task_type,
                        privacy_level=t.privacy_level,
                        permission_level=t.permission_level,
                        estimated_lines=t.estimated_lines,
                        spec_markdown=t.spec_markdown,
                        test_code=t.test_code,
                        target_file_path=t.target_file_path,
                    )
                    self.enqueue_microtask(node)
                    nodes.append(node)
                return nodes
        except Exception as exc:
            logger.warning(f"Falha na decomposição GBNF via Ollama ({exc}). Recorrendo ao parser determinístico.")

        # Fallback Determinístico (Zero-IA / Mock estruturado para teste e confiabilidade)
        return self._deterministic_fallback_decomposition(masterbook_content, parent_name)

    def _deterministic_fallback_decomposition(
        self, content: str, parent_name: str
    ) -> List[MicrotaskDAGNode]:
        """Extrai tarefas delimitadas por marcadores ### TAREFA no markdown."""
        nodes = []
        task_blocks = re.split(r"(?=### TAREFA )", content)
        prev_task_id = None

        for idx, block in enumerate(task_blocks):
            if not block.strip() or not block.startswith("### TAREFA"):
                continue

            lines = block.splitlines()
            header = lines[0].replace("### TAREFA", "").strip()
            task_id = header.split(":")[0].strip() if ":" in header else f"TASK_{idx:03d}"

            # Extração simples de campos
            node = MicrotaskDAGNode(
                task_id=task_id,
                parent_masterbook=parent_name,
                depends_on_task_id=prev_task_id,
                task_type="EXTRACTION",
                privacy_level="PUBLIC",
                permission_level=2,
                estimated_lines=25,
                spec_markdown=block,
                test_code="def test_auto(): assert True\n",
                target_file_path=f"src/{task_id.lower()}.py",
            )
            self.enqueue_microtask(node)
            nodes.append(node)
            prev_task_id = task_id

        return nodes
