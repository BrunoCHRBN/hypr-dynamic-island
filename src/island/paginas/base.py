"""Protocolo base e classe abstrata para páginas da Dynamic Island."""

from __future__ import annotations
from typing import Protocol, runtime_checkable
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


@runtime_checkable
class Pagina(Protocol):
    """Protocolo que toda página do Stack expandido deve implementar."""
    nome: str
    titulo: str
    icone: str

    def construir(self) -> Gtk.Widget:
        """Cria e retorna o widget raiz da página."""
        ...

    def ao_mostrar(self) -> None:
        """Chamado quando a página entra em exibição no Stack (inicia assinaturas)."""
        ...

    def ao_esconder(self) -> None:
        """Chamado quando a página sai de exibição no Stack (suspende timers/I/O)."""
        ...

    def disponivel(self) -> bool:
        """Informa se a página deve ser incluída no Stack e nos dots de navegação."""
        ...
