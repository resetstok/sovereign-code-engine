"""
Módulo: satellite_map
Módulo de Referência Geoespacial para Satélite (Piloto PCI Engine v2.1).
"""

from src.satellite_map.validator import validate_coordinate
from src.satellite_map.geojson_converter import points_to_geojson
from src.satellite_map.pipeline import process_satellite_feed

__all__ = ["validate_coordinate", "points_to_geojson", "process_satellite_feed"]
