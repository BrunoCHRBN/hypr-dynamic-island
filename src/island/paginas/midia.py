"""Página de Mídia (Now Playing) com controles completos de transporte."""

from __future__ import annotations
import subprocess
from typing import Optional, Callable
from gi.repository import Gtk, Pango, GLib
from ..fontes.mpris import FonteMpris


def mmss(us: int) -> str:
    s = max(0, int(us // 1_000_000))
    return f"{s // 60}:{s % 60:02d}"


class PaginaMidia:
    nome = "midia"
    titulo = "Mídia (Now Playing)"
    icone = "󰓇"

    def __init__(self, fonte_mpris: FonteMpris, ao_focar_player: Optional[Callable[[], None]] = None) -> None:
        self.mpris = fonte_mpris
        self.ao_focar_player = ao_focar_player
        self.widget_raiz: Optional[Gtk.Box] = None
        self._arrastando_barra = False
        self._ouvindo = False

    def construir(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_name("page-midia")

        # Box ativo (com player ativo)
        self.hero_active_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # 1. Topo: Capa + Informações
        self.hero_capa = Gtk.Image()
        self.hero_capa.set_name("hero-capa")
        self.hero_capa.set_pixel_size(80)
        self.hero_capa.set_from_icon_name("audio-x-generic-symbolic")

        self.hero_titulo = Gtk.Label(label="Nenhuma mídia")
        self.hero_titulo.set_name("hero-titulo")
        self.hero_titulo.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_titulo.set_max_width_chars(28)
        self.hero_titulo.set_xalign(0)

        self.hero_artista = Gtk.Label(label="nada tocando")
        self.hero_artista.set_name("hero-artista")
        self.hero_artista.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_artista.set_max_width_chars(32)
        self.hero_artista.set_xalign(0)

        self.hero_album = Gtk.Label(label="")
        self.hero_album.set_name("hero-album")
        self.hero_album.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_album.set_max_width_chars(32)
        self.hero_album.set_xalign(0)

        info_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_col.set_valign(Gtk.Align.CENTER)
        info_col.set_hexpand(True)
        info_col.append(self.hero_titulo)
        info_col.append(self.hero_artista)
        info_col.append(self.hero_album)

        hero_topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hero_topo.set_cursor_from_name("pointer")
        hero_topo.set_tooltip_text("Focar reprodutor")
        if self.ao_focar_player:
            g_click = Gtk.GestureClick()
            g_click.connect("released", lambda *_: self.ao_focar_player())
            hero_topo.add_controller(g_click)
        hero_topo.append(self.hero_capa)
        hero_topo.append(info_col)

        # 2. Barra de Progresso com Timestamps
        self.tempo_atual = Gtk.Label(label="0:00")
        self.tempo_atual.set_name("hero-tempo-atual")
        self.tempo_total = Gtk.Label(label="0:00")
        self.tempo_total.set_name("hero-tempo-total")

        self.barra = Gtk.ProgressBar()
        self.barra.set_fraction(0)
        self.barra.set_cursor_from_name("pointer")
        self.barra.set_hexpand(True)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._ao_drag_barra_inicio)
        drag.connect("drag-update", self._ao_drag_barra_atualizar)
        drag.connect("drag-end", self._ao_drag_barra_fim)
        self.barra.add_controller(drag)

        linha_progresso = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        linha_progresso.set_valign(Gtk.Align.CENTER)
        linha_progresso.append(self.tempo_atual)
        linha_progresso.append(self.barra)
        linha_progresso.append(self.tempo_total)

        # 3. Controles de Transporte
        self.b_shuffle = Gtk.Button()
        self.b_shuffle.set_child(Gtk.Image.new_from_icon_name("media-playlist-shuffle-symbolic"))
        self.b_shuffle.add_css_class("controle")
        self.b_shuffle.add_css_class("hero-sub-btn")
        self.b_shuffle.set_tooltip_text("Ordem Aleatória (Shuffle)")
        self.b_shuffle.connect("clicked", lambda *_: self.mpris.alternar_shuffle())

        self.b_ant = Gtk.Button()
        self.b_ant.set_child(Gtk.Image.new_from_icon_name("media-skip-backward-symbolic"))
        self.b_ant.add_css_class("controle")
        self.b_ant.set_tooltip_text("Faixa Anterior")
        self.b_ant.connect("clicked", lambda *_: self.mpris.anterior())

        self.b_play = Gtk.Button()
        self.b_play.set_child(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        self.b_play.add_css_class("controle")
        self.b_play.add_css_class("play-btn")
        self.b_play.set_tooltip_text("Reproduzir / Pausar")
        self.b_play.connect("clicked", lambda *_: self.mpris.play_pause())

        self.b_prox = Gtk.Button()
        self.b_prox.set_child(Gtk.Image.new_from_icon_name("media-skip-forward-symbolic"))
        self.b_prox.add_css_class("controle")
        self.b_prox.set_tooltip_text("Próxima Faixa")
        self.b_prox.connect("clicked", lambda *_: self.mpris.proxima())

        self.b_repeat = Gtk.Button()
        self.b_repeat.set_child(Gtk.Image.new_from_icon_name("media-playlist-repeat-symbolic"))
        self.b_repeat.add_css_class("controle")
        self.b_repeat.add_css_class("hero-sub-btn")
        self.b_repeat.set_tooltip_text("Repetição (Repeat)")
        self.b_repeat.connect("clicked", lambda *_: self.mpris.alternar_loop())

        controles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        controles.set_halign(Gtk.Align.CENTER)
        controles.append(self.b_shuffle)
        controles.append(self.b_ant)
        controles.append(self.b_play)
        controles.append(self.b_prox)
        controles.append(self.b_repeat)

        self.hero_active_box.append(hero_topo)
        self.hero_active_box.append(linha_progresso)
        self.hero_active_box.append(controles)

        # Box vazio (quando nenhum player está aberto)
        self.hero_empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.hero_empty_box.set_halign(Gtk.Align.CENTER)
        self.hero_empty_box.set_valign(Gtk.Align.CENTER)
        self.hero_empty_box.set_hexpand(True)
        self.hero_empty_box.set_vexpand(True)

        empty_icon = Gtk.Label(label="󰓇")
        empty_icon.add_css_class("media-empty-icon")
        empty_tit = Gtk.Label(label="Nenhuma mídia ativa")
        empty_tit.add_css_class("media-empty-titulo")
        btn_abrir = Gtk.Button(label="󰓇 Abrir Spotify")
        btn_abrir.add_css_class("media-empty-btn")
        btn_abrir.set_cursor_from_name("pointer")
        btn_abrir.connect("clicked", lambda *_: subprocess.Popen(["spotify"]))

        self.hero_empty_box.append(empty_icon)
        self.hero_empty_box.append(empty_tit)
        self.hero_empty_box.append(btn_abrir)

        box.append(self.hero_active_box)
        box.append(self.hero_empty_box)
        self.widget_raiz = box
        self._atualizar_dados(self.mpris.dados())
        return box

    def ao_mostrar(self) -> None:
        if not self._ouvindo:
            self.mpris.assinar(self._ao_evento_mpris)
            self._ouvindo = True
        self._atualizar_dados(self.mpris.dados())

    def ao_esconder(self) -> None:
        if self._ouvindo:
            self.mpris.desassinar(self._ao_evento_mpris)
            self._ouvindo = False

    def disponivel(self) -> bool:
        d = self.mpris.dados()
        return bool(d and (d.get("titulo") or d.get("art")))

    def _ao_evento_mpris(self, evento: str, dados: any) -> None:
        if evento == "mudar":
            GLib.idle_add(lambda: self._atualizar_dados(dados))
        elif evento == "posicao" and not self._arrastando_barra:
            GLib.idle_add(lambda: self._atualizar_posicao(dados))

    def _atualizar_dados(self, d: Optional[dict]) -> None:
        if not self.widget_raiz:
            return
        tem = bool(d and (d.get("titulo") or d.get("art")))
        self.hero_active_box.set_visible(tem)
        self.hero_empty_box.set_visible(not tem)
        if not tem:
            return

        tit = d.get("titulo", "Desconhecido")
        art = d.get("artista", "")
        self.hero_titulo.set_text(tit)
        self.hero_artista.set_text(art or tit)
        self.hero_album.set_text(d.get("album", ""))

        dur = d.get("duracao", 0)
        pode_seek = bool(d.get("pode_seek", False) and dur > 0)
        if dur > 0:
            self.tempo_total.set_text(mmss(dur))
            self.barra.set_cursor_from_name("pointer" if pode_seek else "default")
            self.barra.set_sensitive(pode_seek)
        else:
            self.tempo_total.set_text("Ao vivo")
            self.barra.set_cursor_from_name("default")
            self.barra.set_sensitive(False)

        tocando = (d.get("status") == "Playing")
        ico_play = "media-playback-pause-symbolic" if tocando else "media-playback-start-symbolic"
        self.b_play.set_child(Gtk.Image.new_from_icon_name(ico_play))

        self.b_ant.set_sensitive(d.get("pode_ant", False))
        self.b_prox.set_sensitive(d.get("pode_prox", False))
        self.b_play.set_sensitive(d.get("pode_play", True))

        if d.get("shuffle"):
            self.b_shuffle.add_css_class("ativo")
        else:
            self.b_shuffle.remove_css_class("ativo")

        if d.get("loop_status") != "None":
            self.b_repeat.add_css_class("ativo")
        else:
            self.b_repeat.remove_css_class("ativo")

    def _atualizar_posicao(self, pos_us: int) -> None:
        if not self.widget_raiz or self._arrastando_barra:
            return
        d = self.mpris.dados()
        if d and d.get("duracao", 0) > 0:
            frac = min(1.0, max(0.0, pos_us / d["duracao"]))
            self.barra.set_fraction(frac)
            self.tempo_atual.set_text(mmss(pos_us))
        else:
            self.barra.set_fraction(0.0)
            self.tempo_atual.set_text(mmss(pos_us) if pos_us > 0 else "")

    def _ao_drag_barra_inicio(self, _gesture, start_x: float, _start_y: float) -> None:
        d = self.mpris.dados()
        if not (d and d.get("pode_seek", False) and d.get("duracao", 0) > 0):
            return
        self._arrastando_barra = True
        self._atualizar_seek(start_x, commit=False)

    def _ao_drag_barra_atualizar(self, gesture, offset_x: float, _offset_y: float) -> None:
        if not self._arrastando_barra:
            return
        ok, x0, _ = gesture.get_start_point()
        if ok:
            self._atualizar_seek(x0 + offset_x, commit=False)

    def _ao_drag_barra_fim(self, gesture, offset_x: float, _offset_y: float) -> None:
        if not self._arrastando_barra:
            return
        ok, x0, _ = gesture.get_start_point()
        if ok:
            self._atualizar_seek(x0 + offset_x, commit=True)
        self._arrastando_barra = False

    def _atualizar_seek(self, x: float, commit: bool = False) -> None:
        d = self.mpris.dados()
        if not (d and d.get("pode_seek", False) and d.get("duracao", 0) > 0):
            return
        largura = self.barra.get_width()
        if largura <= 0:
            return
        fracao = max(0.0, min(1.0, x / largura))
        self.barra.set_fraction(fracao)
        alvo_us = int(fracao * d["duracao"])
        self.tempo_atual.set_text(mmss(alvo_us))
        if commit:
            self.mpris.seek(alvo_us)
