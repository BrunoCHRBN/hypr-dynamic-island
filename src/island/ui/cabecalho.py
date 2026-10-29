"""Gerenciador do cabeçalho da ilha baseado no modelo de 3 slots (Leading, Center, Trailing)."""

from __future__ import annotations
from typing import Optional, Any
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango, Gdk
from ..core.cena import Cena
from ..core.atividade import SlotItem


class CabecalhoSlots(Gtk.Box):
    """Contêiner do cabeçalho estruturado em slots canônicos."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_name("cabecalho-slots")
        self.set_valign(Gtk.Align.CENTER)

        # Linha horizontal principal dos 3 slots
        self.linha_slots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.linha_slots.set_name("linha-slots")
        self.linha_slots.set_valign(Gtk.Align.CENTER)
        self.linha_slots.set_hexpand(True)

        # 1. Slot Leading (Esquerda: capa, ícone de status, dots)
        self.box_leading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.box_leading.set_name("slot-leading")
        self.box_leading.set_halign(Gtk.Align.START)
        self.box_leading.set_valign(Gtk.Align.CENTER)

        # 2. Slot Center (Centro: título da mídia, HUD de volume, texto de notificação)
        self.box_center = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.box_center.set_name("slot-center")
        self.box_center.set_halign(Gtk.Align.START)
        self.box_center.set_valign(Gtk.Align.CENTER)
        self.box_center.set_hexpand(True)

        # 3. Slot Trailing (Direita: equalizador, relógio, tempo do timer, pin, dnd)
        self.box_trailing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.box_trailing.set_name("slot-trailing")
        self.box_trailing.set_halign(Gtk.Align.END)
        self.box_trailing.set_valign(Gtk.Align.CENTER)

        self.linha_slots.append(self.box_leading)
        self.linha_slots.append(self.box_center)
        self.linha_slots.append(self.box_trailing)

        # Linha inferior de progresso fino (substitui o antigo #pilula-progresso)
        self.barra_progresso = Gtk.ProgressBar()
        self.barra_progresso.set_name("pilula-progresso")
        self.barra_progresso.set_hexpand(True)
        self.barra_progresso.set_visible(False)

        self.append(self.linha_slots)
        self.append(self.barra_progresso)

        self._cena_atual: Optional[Cena] = None

    def aplicar_cena(self, cena: Cena) -> None:
        """Aplica uma cena resolvida aos slots visuais de forma reativa e atômica."""
        self._cena_atual = cena

        # Atualiza Slot Leading
        self._renderizar_slot(self.box_leading, cena.leading)

        # Atualiza Slot Center
        self._renderizar_slot(self.box_center, cena.center)

        # Atualiza Slot Trailing
        self._renderizar_slot(self.box_trailing, cena.trailing)

        # Atualiza Linha de Progresso
        if cena.progresso is not None and not cena.expandido:
            self.barra_progresso.set_fraction(max(0.0, min(1.0, float(cena.progresso))))
            self.barra_progresso.set_visible(True)
        else:
            self.barra_progresso.set_visible(False)

    def _renderizar_slot(self, container: Gtk.Box, item: Optional[SlotItem]) -> None:
        """Renderiza um SlotItem reutilizando ou criando widgets de forma eficiente."""
        # Limpa widgets filhos
        while child := container.get_first_child():
            container.remove(child)

        if item is None or item.valor is None:
            container.set_visible(False)
            return

        container.set_visible(True)

        if item.tipo in ("texto", "equalizador"):
            lbl = Gtk.Label(label=str(item.valor))
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            if item.css_class:
                for c in item.css_class.split():
                    lbl.add_css_class(c)
            if item.tooltip:
                lbl.set_tooltip_text(item.tooltip)
            container.append(lbl)

        elif item.tipo == "markup":
            lbl = Gtk.Label()
            lbl.set_markup(str(item.valor))
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            if item.css_class:
                for c in item.css_class.split():
                    lbl.add_css_class(c)
            container.append(lbl)

        elif item.tipo == "icone":
            lbl = Gtk.Label(label=str(item.valor))
            if item.css_class:
                for c in item.css_class.split():
                    lbl.add_css_class(c)
            container.append(lbl)

        elif item.tipo == "capa":
            img = Gtk.Image()
            img.set_name("capa")
            if isinstance(item.valor, Gdk.Texture):
                img.set_from_paintable(item.valor)
            else:
                img.set_from_icon_name("audio-x-generic-symbolic")
            container.append(img)

        elif item.tipo == "widget" and isinstance(item.valor, Gtk.Widget):
            container.append(item.valor)
