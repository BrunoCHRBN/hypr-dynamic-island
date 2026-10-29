"""Página de Histórico de Notificações com ações por item e limpeza rápida."""

from __future__ import annotations
import json
import re
import subprocess
from typing import Optional, List, Dict, Any
from gi.repository import Gtk, Pango, GLib


def limpar_texto_notif(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r"<[^>]*>", "", texto)
    texto = texto.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return texto.strip()


def ler_historico_dunst(limite: int = 6) -> List[Dict[str, Any]]:
    try:
        r = subprocess.run(["dunstctl", "history"], capture_output=True, text=True, timeout=0.8)
        if r.returncode != 0 or not r.stdout.strip():
            return []
        dados = json.loads(r.stdout)
        data_list = dados.get("data", [[]])[0]
        itens = []
        for n in data_list[:limite]:
            app = limpar_texto_notif(n.get("appname", {}).get("data", "")) or "Sistema"
            tit = limpar_texto_notif(n.get("summary", {}).get("data", "")) or "Notificação"
            corpo = limpar_texto_notif(n.get("body", {}).get("data", ""))
            itens.append({"app": app, "titulo": tit, "corpo": corpo})
        return itens
    except Exception:
        return []


class PaginaHistorico:
    nome = "historico"
    titulo = "Histórico de Notificações"
    icone = "󰂚"

    def __init__(self) -> None:
        self.widget_raiz: Optional[Gtk.Box] = None
        self._itens_cache: List[Dict[str, Any]] = []

    def construir(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_name("page-historico")

        # Cabeçalho da página
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("notif-hist-header")

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_box.set_hexpand(True)
        lbl_ico = Gtk.Label(label="󰂚")
        lbl_ico.add_css_class("notif-hist-header-title")
        lbl_txt = Gtk.Label(label="Histórico")
        lbl_txt.add_css_class("notif-hist-header-title")
        self.lbl_badge = Gtk.Label(label="0")
        self.lbl_badge.add_css_class("notif-hist-badge")
        title_box.append(lbl_ico)
        title_box.append(lbl_txt)
        title_box.append(self.lbl_badge)

        btn_limpar = Gtk.Button(label="󰅖 Limpar")
        btn_limpar.add_css_class("btn-limpar-hist")
        btn_limpar.set_cursor_from_name("pointer")
        btn_limpar.connect("clicked", lambda *_: self._limpar_historico())

        header.append(title_box)
        header.append(btn_limpar)

        self.box_lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        box.append(header)
        box.append(self.box_lista)
        self.widget_raiz = box
        return box

    def ao_mostrar(self) -> None:
        self._carregar_historico()

    def ao_esconder(self) -> None:
        pass

    def disponivel(self) -> bool:
        return True

    def _limpar_historico(self) -> None:
        try:
            subprocess.run(["dunstctl", "history-clear"], capture_output=True, timeout=0.5)
        except Exception:
            pass
        self._carregar_historico()

    def _carregar_historico(self) -> None:
        if not self.widget_raiz:
            return

        def _worker():
            itens = ler_historico_dunst(limite=5)
            GLib.idle_add(lambda: self._renderizar_itens(itens))

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _renderizar_itens(self, itens: List[Dict[str, Any]]) -> None:
        self._itens_cache = itens
        self.lbl_badge.set_text(str(len(itens)))

        while child := self.box_lista.get_first_child():
            self.box_lista.remove(child)

        if not itens:
            vazio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            vazio.set_halign(Gtk.Align.CENTER)
            vazio.set_valign(Gtk.Align.CENTER)
            vazio.set_margin_top(16)
            vazio.set_margin_bottom(16)

            ico = Gtk.Label(label="󰂛")
            ico.add_css_class("notif-vazio-icone")
            txt = Gtk.Label(label="Nenhuma notificação recente")
            txt.add_css_class("notif-vazio-texto")

            vazio.append(ico)
            vazio.append(txt)
            self.box_lista.append(vazio)
            return

        for it in itens:
            item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            item_box.add_css_class("notif-hist-item")

            linha_topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl_app = Gtk.Label(label=it["app"])
            lbl_app.add_css_class("notif-hist-app")
            lbl_app.set_xalign(0)
            lbl_app.set_hexpand(True)

            btn_copiar = Gtk.Button(label="󰅍")
            btn_copiar.add_css_class("notif-item-btn")
            btn_copiar.set_tooltip_text("Copiar notificação")
            texto_copia = f"{it['titulo']}\n{it['corpo']}".strip()
            btn_copiar.connect("clicked", lambda *_, t=texto_copia: self._copiar_texto(t))

            linha_topo.append(lbl_app)
            linha_topo.append(btn_copiar)

            lbl_tit = Gtk.Label(label=it["titulo"])
            lbl_tit.add_css_class("notif-hist-titulo")
            lbl_tit.set_xalign(0)
            lbl_tit.set_ellipsize(Pango.EllipsizeMode.END)

            item_box.append(linha_topo)
            item_box.append(lbl_tit)

            if it["corpo"]:
                lbl_corpo = Gtk.Label(label=it["corpo"])
                lbl_corpo.add_css_class("notif-hist-corpo")
                lbl_corpo.set_xalign(0)
                lbl_corpo.set_ellipsize(Pango.EllipsizeMode.END)
                lbl_corpo.set_max_width_chars(36)
                item_box.append(lbl_corpo)

            self.box_lista.append(item_box)

    @staticmethod
    def _copiar_texto(texto: str) -> None:
        try:
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            p.communicate(input=texto.encode("utf-8"), timeout=1.0)
        except Exception:
            pass
