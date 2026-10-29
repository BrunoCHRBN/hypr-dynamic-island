"""Módulo de páginas modulares do painel expandido da Dynamic Island."""

from .base import Pagina
from .midia import PaginaMidia
from .hub import PaginaHub
from .controles import PaginaControles
from .historico import PaginaHistorico

__all__ = [
    "Pagina",
    "PaginaMidia",
    "PaginaHub",
    "PaginaControles",
    "PaginaHistorico",
]
