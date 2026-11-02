# MASTERBOOK: PILOTO DE REFERÊNCIA — MÓDULO MAPA SATÉLITE GEOJSON
**ID do Masterbook:** MB_PILOTO_MAPA_SATELITE_V2
**Nível de Privacidade Global:** PUBLIC
**Arquitetura Alvo:** PCI Sovereign Code Engine v2.1
**Objetivo:** Construir e integrar um pipeline geoespacial resiliente para validação de coordenadas geográficas e conversão em camadas GeoJSON padronizadas (RFC 7946) a partir de fixtures de satélite.

---

## MICROTAREFA 1: Validação de Coordenadas Geográficas
- **task_id:** PILOT_MAP_TASK_01
- **task_type:** EXTRACTION
- **privacy_level:** PUBLIC
- **estimated_lines:** 35
- **target_file_path:** src/satellite_map/validator.py
- **depends_on_task_id:** null

### Especificação:
Crie o módulo `src/satellite_map/validator.py` com a função:
`validate_coordinate(lat: float, lon: float) -> tuple[bool, str]`
1. Latitude deve estar no intervalo fechado [-90.0, 90.0].
2. Longitude deve estar no intervalo fechado [-180.0, 180.0].
3. Se válido, retorna `(True, "OK")`.
4. Se inválido, retorna `(False, "Latitude fora do intervalo [-90, 90]" | "Longitude fora do intervalo [-180, 180]")`.

### Código de Teste (Pytest):
```python
from src.satellite_map.validator import validate_coordinate

def test_valid_coordinates():
    ok, msg = validate_coordinate(-22.8731, -42.3431)
    assert ok is True
    assert msg == "OK"

def test_invalid_latitude():
    ok, msg = validate_coordinate(-95.0, -42.3431)
    assert ok is False
    assert "Latitude fora" in msg

def test_invalid_longitude():
    ok, msg = validate_coordinate(-22.8731, 195.0)
    assert ok is False
    assert "Longitude fora" in msg
```

---

## MICROTAREFA 2: Conversor GeoJSON FeatureCollection
- **task_id:** PILOT_MAP_TASK_02
- **task_type:** CRUD
- **privacy_level:** PUBLIC
- **estimated_lines:** 45
- **target_file_path:** src/satellite_map/geojson_converter.py
- **depends_on_task_id:** PILOT_MAP_TASK_01

### Especificação:
Crie o módulo `src/satellite_map/geojson_converter.py` com a função:
`points_to_geojson(points: list[dict]) -> dict`
Cada ponto deve ser um dicionário com: `latitude`, `longitude`, e propriedades adicionais opcionais (ex: `id`, `label`, `satellite`).
Gera um dicionário GeoJSON válido no padrão RFC 7946:
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [longitude, latitude]
      },
      "properties": { ... }
    }
  ]
}
```
Nota crítica GeoJSON: em `coordinates`, a ordem é estritamente `[longitude, latitude]`!

### Código de Teste (Pytest):
```python
from src.satellite_map.geojson_converter import points_to_geojson

def test_points_to_geojson_structure():
    raw_points = [
        {"id": "P1", "latitude": -22.87, "longitude": -42.34, "label": "Araruama"},
        {"id": "P2", "latitude": -22.86, "longitude": -42.32, "label": "Hospital"}
    ]
    gj = points_to_geojson(raw_points)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 2
    f1 = gj["features"][0]
    assert f1["geometry"]["type"] == "Point"
    assert f1["geometry"]["coordinates"] == [-42.34, -22.87]
    assert f1["properties"]["label"] == "Araruama"
```

---

## MICROTAREFA 3: Integração do Pipeline com Fixture de Satélite
- **task_id:** PILOT_MAP_TASK_03
- **task_type:** REFACTOR
- **privacy_level:** PUBLIC
- **estimated_lines:** 50
- **target_file_path:** src/satellite_map/pipeline.py
- **depends_on_task_id:** PILOT_MAP_TASK_02

### Especificação:
Crie o módulo `src/satellite_map/pipeline.py` com a função:
`process_satellite_feed(fixture_json_path: str) -> dict`
1. Lê o arquivo JSON indicado em `fixture_json_path`.
2. Filtra apenas os pontos válidos usando `validate_coordinate`. Pontos inválidos são descartados.
3. Converte os pontos válidos em uma camada GeoJSON usando `points_to_geojson`.
4. Inclui na raiz do GeoJSON um campo de metadados: `metadata = {"valid_points": N, "discarded_points": M}`.
5. Retorna o GeoJSON completo.

### Código de Teste (Pytest):
```python
from pathlib import Path
from src.satellite_map.pipeline import process_satellite_feed

def test_process_satellite_feed():
    fixture_path = "tasks/fixtures/sample_coords.json"
    res = process_satellite_feed(fixture_path)
    assert res["type"] == "FeatureCollection"
    assert len(res["features"]) == 3
    assert res["metadata"]["valid_points"] == 3
    assert res["metadata"]["discarded_points"] == 1
```
