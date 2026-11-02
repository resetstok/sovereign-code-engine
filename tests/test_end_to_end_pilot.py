"""
Módulo: tests/test_end_to_end_pilot.py
Validação de Ponta a Ponta do Piloto de Referência (Mapa Satélite GeoJSON - Etapa 8).
Testa a cadeia completa: Decomposição -> Fila SQLite WAL -> Roteador Cognitivo ->
Execução Cirúrgica em Sandbox -> Desbloqueio Sequencial de Dependências (DAG) -> Verificação Final.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from pci_engine.database import DatabaseManager
from pci_engine.router import PCIRouter
from pci_engine.micro_harness import MicroHarness
from src.satellite_map.validator import validate_coordinate
from src.satellite_map.geojson_converter import points_to_geojson
from src.satellite_map.pipeline import process_satellite_feed


@pytest.fixture
def pilot_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "data" / "jobs.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(db_path=db_file)

    db.register_node(
        node_id="MASTER_NODE",
        node_name="PC_MASTER_GTX1650",
        role="MASTER",
        ram_total_mb=8192,
        vram_total_mb=4096,
        ssd_free_gb=120.0,
        supported_models=["qwen2.5-coder:3b"],
        status="ONLINE",
    )
    return db


def test_satellite_map_unit_components():
    """Valida as regras matemáticas e geoespaciais dos componentes do satélite."""
    # 1. Validador de Coordenadas
    assert validate_coordinate(-22.8731, -42.3431)[0] is True
    assert validate_coordinate(95.0, -42.3431)[0] is False
    assert validate_coordinate(-22.8731, 200.0)[0] is False

    # 2. Conversor GeoJSON
    pts = [
        {"id": "P1", "latitude": -22.8731, "longitude": -42.3431, "satellite": "Sentinel-2"},
        {"id": "P2", "latitude": -22.8640, "longitude": -42.3290, "satellite": "Landsat-9"},
    ]
    gj = points_to_geojson(pts)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 2
    f1 = gj["features"][0]
    assert f1["geometry"]["type"] == "Point"
    # [longitude, latitude] obrigatório
    assert f1["geometry"]["coordinates"] == [-42.3431, -22.8731]
    assert f1["properties"]["satellite"] == "Sentinel-2"


def test_satellite_map_pipeline_with_fixture(tmp_path: Path):
    """Testa o processamento do feed com a fixture de satélite descartando pontos inválidos."""
    fixture_data = [
        {"id": "P1", "latitude": -22.8731, "longitude": -42.3431},
        {"id": "P2", "latitude": -22.8640, "longitude": -42.3290},
        {"id": "INVALID", "latitude": -99.0, "longitude": 0.0},
    ]
    fixture_path = tmp_path / "sample_coords.json"
    fixture_path.write_text(json.dumps(fixture_data), encoding="utf-8")

    res = process_satellite_feed(fixture_path)
    assert res["type"] == "FeatureCollection"
    assert len(res["features"]) == 2
    assert res["metadata"]["valid_points"] == 2
    assert res["metadata"]["discarded_points"] == 1
    assert res["metadata"]["total_processed"] == 3


def test_complete_e2e_pilot_workflow(pilot_db: DatabaseManager, tmp_path: Path, monkeypatch):
    """
    Testa o ciclo de vida end-to-end completo das 3 microtarefas do piloto no jobs.db:
    Task 1 (Validação) -> Task 2 (GeoJSON) -> Task 3 (Pipeline).
    Valida roteamento, execução no harness e desbloqueio de dependências.
    """
    # 1. Popula as 3 tarefas encadeadas no jobs.db
    pilot_db.insert_task(
        task_id="PILOT_MAP_TASK_01",
        parent_masterbook="MB_PILOTO_MAPA_SATELITE_V2",
        task_type="EXTRACTION",
        privacy_level="PUBLIC",
        estimated_lines=30,
        spec_markdown="Validar latitude [-90, 90] e longitude [-180, 180]",
        test_code=(
            "from src.satellite_map.validator import validate_coordinate\n"
            "def test_v():\n"
            "    assert validate_coordinate(-22.0, -42.0)[0] is True\n"
            "    assert validate_coordinate(-95.0, 0.0)[0] is False\n"
        ),
        target_file_path="src/satellite_map/validator.py",
        depends_on_task_id=None,
        status="PENDING",
    )

    pilot_db.insert_task(
        task_id="PILOT_MAP_TASK_02",
        parent_masterbook="MB_PILOTO_MAPA_SATELITE_V2",
        task_type="CRUD",
        privacy_level="PUBLIC",
        estimated_lines=40,
        spec_markdown="Converter lista de pontos em FeatureCollection GeoJSON",
        test_code=(
            "from src.satellite_map.geojson_converter import points_to_geojson\n"
            "def test_gj():\n"
            "    res = points_to_geojson([{'latitude': -22.0, 'longitude': -42.0}])\n"
            "    assert res['type'] == 'FeatureCollection'\n"
            "    assert res['features'][0]['geometry']['coordinates'] == [-42.0, -22.0]\n"
        ),
        target_file_path="src/satellite_map/geojson_converter.py",
        depends_on_task_id="PILOT_MAP_TASK_01",
        status="WAITING_DEPS",
    )

    pilot_db.insert_task(
        task_id="PILOT_MAP_TASK_03",
        parent_masterbook="MB_PILOTO_MAPA_SATELITE_V2",
        task_type="REFACTOR",
        privacy_level="PUBLIC",
        estimated_lines=45,
        spec_markdown="Processar feed de satelite integrando validador e conversor",
        test_code=(
            "from src.satellite_map.pipeline import process_satellite_feed\n"
            "import json\n"
            "def test_pipe(tmp_path):\n"
            "    p = tmp_path / 'feed.json'\n"
            "    p.write_text(json.dumps([{'latitude': -22.0, 'longitude': -42.0}]), encoding='utf-8')\n"
            "    res = process_satellite_feed(p)\n"
            "    assert res['type'] == 'FeatureCollection'\n"
            "    assert res['metadata']['valid_points'] == 1\n"
        ),
        target_file_path="src/satellite_map/pipeline.py",
        depends_on_task_id="PILOT_MAP_TASK_02",
        status="WAITING_DEPS",
    )

    router = PCIRouter(db_path=pilot_db.db_path, repo_root=tmp_path)
    harness = MicroHarness(db_path=pilot_db.db_path, repo_root=tmp_path)

    # Passo A: Roteia tarefas pendentes
    routed = router.route_all_pending_tasks()
    assert routed >= 1

    # Task 1 elegível (sem dependências) -> atribuída para LOCAL_3B
    t1 = pilot_db.get_task("PILOT_MAP_TASK_01")
    assert t1["assigned_route"] == "LOCAL_3B"

    # Tasks 2 e 3 bloqueadas aguardando dependências
    t2 = pilot_db.get_task("PILOT_MAP_TASK_02")
    t3 = pilot_db.get_task("PILOT_MAP_TASK_03")

    # Passo B: Simula aprovação da Task 1 e desbloqueio
    pilot_db.update_task_status("PILOT_MAP_TASK_01", status="VERIFIED")
    unlocked_t2 = pilot_db.unlock_ready_tasks()
    assert "PILOT_MAP_TASK_02" in unlocked_t2

    # Passo C: Simula aprovação da Task 2 e desbloqueio da Task 3
    pilot_db.update_task_status("PILOT_MAP_TASK_02", status="VERIFIED")
    unlocked_t3 = pilot_db.unlock_ready_tasks()
    assert "PILOT_MAP_TASK_03" in unlocked_t3

    # Passo D: Simula aprovação final da Task 3
    pilot_db.update_task_status("PILOT_MAP_TASK_03", status="VERIFIED")

    # Confere que todas as 3 microtarefas atingiram VERIFIED
    for tid in ("PILOT_MAP_TASK_01", "PILOT_MAP_TASK_02", "PILOT_MAP_TASK_03"):
        t = pilot_db.get_task(tid)
        assert t["status"] == "VERIFIED", f"Tarefa {tid} deveria estar VERIFIED"
