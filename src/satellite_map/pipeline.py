"""
Módulo: satellite_map/pipeline.py
Pipeline integrado de processamento e auditoria de feeds de satélite para GeoJSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.satellite_map.validator import validate_coordinate
from src.satellite_map.geojson_converter import points_to_geojson


def process_satellite_feed(fixture_json_path: str | Path) -> Dict[str, Any]:
    """
    Lê uma fixture de satélite em formato JSON, filtra coordenadas inválidas,
    converte em GeoJSON e anexa metadados de auditoria.
    """
    path = Path(fixture_json_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de fixture de satélite não encontrado: {path}")

    raw_data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, list):
        raise ValueError("Fixture de satélite deve conter uma lista JSON de pontos.")

    valid_points = []
    discarded_count = 0

    for item in raw_data:
        lat = item.get("latitude")
        lon = item.get("longitude")

        if lat is not None and lon is not None:
            ok, _ = validate_coordinate(lat, lon)
            if ok:
                valid_points.append(item)
            else:
                discarded_count += 1
        else:
            discarded_count += 1

    geojson = points_to_geojson(valid_points)
    geojson["metadata"] = {
        "valid_points": len(valid_points),
        "discarded_points": discarded_count,
        "total_processed": len(raw_data),
    }

    return geojson
