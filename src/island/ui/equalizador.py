"""Visualizadores vetoriais fluidos em Cairo para a Dynamic Island."""

from __future__ import annotations
import math
import time
import random
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib
import cairo

from ..core.mola import Mola


class EqualizadorArea(Gtk.DrawingArea):
    """Visualizador de equalizador de áudio vetorial (Cairo) com 4 barras arredondadas fluidas.

    Cada barra tem sua própria mola física independente (resposta ~0.18s, amortecimento ~0.72)
    que reage a pulsos orgânicos simulando a dinâmica da música a 144Hz.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_name("eq-area")
        self.set_size_request(22, 16)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)

        # 4 barras com molas físicas independentes
        self._molas = [
            Mola(0.20, resposta=0.18, amortecimento=0.72),
            Mola(0.20, resposta=0.16, amortecimento=0.70),
            Mola(0.20, resposta=0.19, amortecimento=0.75),
            Mola(0.20, resposta=0.17, amortecimento=0.72),
        ]
        self._alvos_tempo = 0.0
        self._cor_rgb = (0.796, 0.651, 0.969)  # Mauve Catppuccin padrão (203, 166, 247)
        self._ativo = False
        self._pausado = False
        self._mudo = False
        self._tick_id = 0
        self._ult_t = None

        self.set_draw_func(self._desenhar)

    def definir_cor(self, r: int, g: int, b: int) -> None:
        """Ajusta a cor das barras com a cor dominante da capa."""
        self._cor_rgb = (r / 255.0, g / 255.0, b / 255.0)
        self.queue_draw()

    def definir_estado(self, ativo: bool, pausado: bool = False, mudo: bool = False) -> None:
        """Atualiza estado do player (tocando, pausado, mudo)."""
        self._ativo = ativo
        self._pausado = pausado
        self._mudo = mudo

        if ativo and not pausado and not mudo:
            if not self._tick_id:
                self._ult_t = None
                try:
                    self._tick_id = self.add_tick_callback(self._passo)
                except Exception:
                    self._tick_id = 0
        else:
            # Em pausa ou mudo: direciona suavemente as barras para repouso
            alvo = 0.20 if pausado else (0.10 if mudo else 0.0)
            for m in self._molas:
                m.ir_para(alvo)
            if not self._tick_id:
                self._ult_t = None
                try:
                    self._tick_id = self.add_tick_callback(self._passo)
                except Exception:
                    self._tick_id = 0

    def _passo(self, _widget, clock) -> bool:
        t = (clock.get_frame_time() / 1e6) if clock is not None else time.monotonic()
        dt = 0.0 if self._ult_t is None else t - self._ult_t
        self._ult_t = t
        dt = min(dt, 1.0 / 30.0)

        # Se tocando, gera metas de altura dinâmicas a cada ~110 ms
        if self._ativo and not self._pausado and not self._mudo:
            if t - self._alvos_tempo > 0.11:
                self._alvos_tempo = t
                padroes = [
                    (0.85, 0.45, 0.95, 0.60),
                    (0.40, 0.90, 0.55, 0.85),
                    (0.70, 0.35, 0.80, 0.50),
                    (0.95, 0.75, 0.40, 0.90),
                    (0.50, 0.85, 0.70, 0.35),
                ]
                idx = int((t * 9) % len(padroes))
                base = padroes[idx]
                for i, m in enumerate(self._molas):
                    jitter = random.uniform(-0.08, 0.08)
                    m.ir_para(max(0.18, min(1.0, base[i] + jitter)))

        todas_assentadas = True
        for m in self._molas:
            assentou = m.passo(dt)
            if not assentou:
                todas_assentadas = False

        self.queue_draw()

        # Encerra o tick callback se estiver pausado/mudo e todas as molas assentaram
        if (not self._ativo or self._pausado or self._mudo) and todas_assentadas:
            self._tick_id = 0
            self._ult_t = None
            return GLib.SOURCE_REMOVE

        return GLib.SOURCE_CONTINUE

    def _desenhar(self, _area, cr, largura: int, altura: int) -> None:
        n_barras = 4
        espacamento = 2.0
        largura_barra = (largura - (n_barras - 1) * espacamento) / n_barras
        raio = largura_barra / 2.0
        r, g, b = self._cor_rgb

        cr.set_source_rgba(r, g, b, 0.95)

        for i, mola in enumerate(self._molas):
            frac = max(0.15, min(1.0, mola.x))
            h_barra = max(largura_barra, frac * (altura - 2.0))
            x = i * (largura_barra + espacamento)
            y = altura - 1.0 - h_barra

            cr.new_sub_path()
            cr.arc(x + largura_barra - raio, y + raio, raio, -math.pi / 2, 0)
            cr.arc(x + largura_barra - raio, y + h_barra - raio, raio, 0, math.pi / 2)
            cr.arc(x + raio, y + h_barra - raio, raio, math.pi / 2, math.pi)
            cr.arc(x + raio, y + raio, raio, math.pi, 3 * math.pi / 2)
            cr.close_path()
            cr.fill()


class AnelProgresso(Gtk.DrawingArea):
    """Anel circular de progresso vetorial (Cairo) para Timer e Atividades ao Vivo."""

    def __init__(self, raio: float = 7.0, largura_linha: float = 2.0) -> None:
        super().__init__()
        self.set_name("anel-progresso")
        tamanho = int((raio + largura_linha) * 2 + 4)
        self.set_size_request(tamanho, tamanho)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self._raio = raio
        self._largura_linha = largura_linha
        self._mola = Mola(0.0, resposta=0.25, amortecimento=0.82)
        self._progresso = 0.0
        self._cor_rgb = (0.796, 0.651, 0.969)
        self._tick_id = 0
        self._ult_t = None
        self.set_visible(False)
        self.set_draw_func(self._desenhar)

    def definir_cor(self, r: int, g: int, b: int) -> None:
        self._cor_rgb = (r / 255.0, g / 255.0, b / 255.0)
        self.queue_draw()

    def definir_progresso(self, valor: float | None) -> None:
        if valor is None:
            self._progresso = 0.0
            self.set_visible(False)
            return
        self.set_visible(True)
        self._progresso = max(0.0, min(1.0, float(valor)))
        self._mola.ir_para(self._progresso)
        if not self._tick_id:
            self._ult_t = None
            try:
                self._tick_id = self.add_tick_callback(self._passo)
            except Exception:
                self._tick_id = 0

    def _passo(self, _widget, clock) -> bool:
        t = (clock.get_frame_time() / 1e6) if clock is not None else time.monotonic()
        dt = 0.0 if self._ult_t is None else t - self._ult_t
        self._ult_t = t
        assentou = self._mola.passo(min(dt, 1.0 / 30.0))
        self.queue_draw()
        if assentou:
            self._tick_id = 0
            self._ult_t = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _desenhar(self, _area, cr, w: int, h: int) -> None:
        cx, cy = w / 2.0, h / 2.0
        cr.set_line_width(self._largura_linha)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        # Trilha de fundo
        cr.arc(cx, cy, self._raio, 0, 2 * math.pi)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.15)
        cr.stroke()

        # Arco de progresso
        p = max(0.0, min(1.0, self._mola.x))
        if p > 0.001:
            cr.arc(cx, cy, self._raio, -math.pi / 2.0, -math.pi / 2.0 + 2.0 * math.pi * p)
            r, g, b = self._cor_rgb
            cr.set_source_rgba(r, g, b, 0.95)
            cr.stroke()
