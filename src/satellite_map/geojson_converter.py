"""
Módulo: satellite_map/geojson_converter.py
Conversor de coordenadas e metadados de satélite para o formato padrão GeoJSON (RFC 7946).
"""

from __future__ import annotations

from typing import Any, Dict, List


def points_to_geojson(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Converte uma lista de dicionários contendo latitude e longitude
    em uma FeatureCollection GeoJSON válida.
    Atenção: GeoJSON exige [longitude, latitude] nas coordenadas do Point.
    """
    features: List[Dict[str, Any]] = []

    for pt in points:
        lat = float(pt.get("latitude", 0.0))
        lon = float(pt.get("longitude", 0.0))

        # Copia propriedades adicionais excluindo coordenadas diretas
        props = {k: v for k, v in pt.items() if k not in ("latitude", "longitude")}

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "properties": props,
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }
