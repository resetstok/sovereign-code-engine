"""
Módulo: satellite_map/validator.py
Validador de coordenadas geográficas padrão WGS-84 (Piloto PCI Engine).
"""

from __future__ import annotations

from typing import Tuple


def validate_coordinate(lat: float, lon: float) -> Tuple[bool, str]:
    """
    Valida se latitude e longitude estão dentro dos limites geodésicos terrestres.
    Latitude: [-90.0, 90.0]
    Longitude: [-180.0, 180.0]
    """
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False, "Coordenadas devem ser valores numéricos (float/int)."

    if lat < -90.0 or lat > 90.0:
        return False, "Latitude fora do intervalo [-90, 90]"

    if lon < -180.0 or lon > 180.0:
        return False, "Longitude fora do intervalo [-180, 180]"

    return True, "OK"
