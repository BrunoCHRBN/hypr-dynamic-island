"""Módulo de fontes de dados desacopladas da interface gráfica."""

from .base import Fonte
from .mpris import FonteMpris, escolher_cor, CACHE_ART
from .volume import FonteVolume
from .sistema import FonteSistema
from .rede import FonteRede
from .kde import FonteKde
from .hypr import FonteHypr

__all__ = [
    "Fonte",
    "FonteMpris",
    "FonteVolume",
    "FonteSistema",
    "FonteRede",
    "FonteKde",
    "FonteHypr",
    "escolher_cor",
    "CACHE_ART",
]
