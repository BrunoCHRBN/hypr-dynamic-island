#!/usr/bin/env python3
"""
island — "dynamic island" no lugar da barra superior.

Pílula flutuante no topo central que se expande ao passar o mouse (ou clicar)
e mostra o player MPRIS ativo: capa, título, artista, progresso e controles.
Sem player, o painel mostra relógio, data e o estado do sistema.

Abre quando o mouse passa por cima (ou num clique, que a prende aberta) e
recolhe sozinha quando o cursor sai. O tamanho acompanha o conteúdo: sem
nenhum player MPRIS ela é menor — largura reduzida e painel sem os controles
mortos, só a linha de sistema (volume, CPU, memória).

Por que GTK4 + gtk4-layer-shell (e não AGS/Astal, que estão instalados):
  - roda no mesmo Python do resto dos scripts daqui, sem runtime novo;
  - MPRIS é D-Bus puro (Gio), sem dependência de plugin;
  - a surface redimensiona junto com o conteúdo (medido), então a animação
    de altura sai de graça com Gtk.Revealer.

Armadilhas medidas nesta máquina:
  1. **LD_PRELOAD é obrigatório.** Sem ele o gtk4-layer-shell não inicializa
     ("linked after libwayland") e a janela vira uma janela normal. O wrapper
     ~/.local/bin/island cuida disso.
  2. **A janela NUNCA fica menor que o conteúdo.** set_size_request é mínimo,
     não máximo — nem com Gtk.Fixed, nem com overflow hidden. Por isso o
     tamanho é dirigido por set_default_size (largura e altura, animadas), e o
     size_request da pílula tem de descer junto: se ficar em 340, o GTK recusa
     encolher para 230 e a largura nunca muda. O texto é elipsado com
     max_width_chars para não estourar a largura.
  3. **MPRIS não emite sinal de Position.** A posição tem de ser consultada
     por polling enquanto toca (500ms), o resto vem por PropertiesChanged.

Uso:
  island                # roda
  island --debug        # loga troca de player/estado
"""

from __future__ import annotations

import argparse
import colorsys
import datetime
import glob
import hashlib
import json
import locale
import math
import os
import random
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request
import cairo

try:
    locale.setlocale(locale.LC_TIME, "")
except Exception:
    pass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Gtk4LayerShell, Pango  # noqa: E402

try:                                    # GLib.unix_signal_add está depreciado
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix  # noqa: E402
except (ValueError, ImportError):
    GLibUnix = None


def cursor_pos() -> tuple[int, int] | None:
    """Posição global do cursor pelo socket do Hyprland (sem spawnar processo).

    Usado no lugar de enter/leave do GTK: a surface muda de tamanho a cada
    frame da animação, e o compositor manda leave/enter durante o resize —
    medido: 9 alternâncias sem ninguém mexer no mouse. Lendo o cursor, quem
    decide é a geometria, não o evento.
    """
    import socket as _socket

    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.settimeout(0.02)
        s.connect(f"{rt}/hypr/{sig}/.socket.sock")
        s.sendall(b"cursorpos")
        x, y = s.recv(256).decode().strip().split(",")
        s.close()
        return int(x), int(y)
    except Exception:
        return None

def monitor_por_nome(nome):
    """GdkMonitor cujo conector bate com `nome` ("DP-2"), ou None.

    GdkMonitor.get_connector() (GDK >= 4.10) devolve o nome do conector igual ao
    que o compositor usa — é o mesmo "DP-2" do `hyprctl monitors`. Sem isto a
    surface nasce no monitor que tem foco na hora da subida, o que torna o
    lugar da ilha dependente de onde o cursor estava no login.
    """
    if not nome:
        return None
    d = Gdk.Display.get_default()
    if d is None:
        return None
    ms = d.get_monitors()
    for i in range(ms.get_n_items()):
        m = ms.get_item(i)
        try:
            if m.get_connector() == nome:
                return m
        except Exception:
            continue
    return None

def obter_refresh_rate(nome: str) -> float:
    """Taxa de atualização em Hz do monitor configurado, ou 60.0 se não determinado."""
    try:
        m = monitor_por_nome(nome)
        if m is not None:
            rr = m.get_refresh_rate()
            if rr > 0:
                return rr / 1000.0
    except Exception:
        pass
    return 60.0


class Mola:
    """Mola física interrompível (oscilador harmônico amortecido).

    Preserva posição (x) e velocidade (v) ao mudar de alvo, permitindo
    interrupções fluidas sem saltos de aceleração ou velocidade zerada.
    """

    def __init__(self, valor: float, resposta: float = 0.42, amortecimento: float = 0.75):
        self.x = self.alvo = float(valor)
        self.v = 0.0
        self.resposta = resposta
        self.amortecimento = amortecimento
        self.k = 0.0
        self.c = 0.0
        self.reconfigurar(resposta, amortecimento)

    def reconfigurar(self, resposta: float, amortecimento: float) -> None:
        """Ajusta o período natural (resposta em s) e razão de amortecimento (zeta)."""
        self.resposta = max(0.01, float(resposta))
        self.amortecimento = max(0.0, float(amortecimento))
        self.k = (2.0 * math.pi / self.resposta) ** 2
        self.c = 2.0 * self.amortecimento * math.sqrt(self.k)

    def ir_para(self, alvo: float) -> None:
        """Altera o alvo preservando a posição e a velocidade atuais."""
        self.alvo = float(alvo)

    def passo(self, dt: float) -> bool:
        """Avança a simulação temporal por dt segundos. Retorna True quando assentou."""
        if dt <= 0:
            return abs(self.x - self.alvo) < 0.25 and abs(self.v) < 1.5
        dt = min(dt, 1.0 / 30.0)  # Protege contra saltos/engasgos
        n = int(dt / 0.004) + 1
        h = dt / n
        for _ in range(n):
            self.v += (-self.k * (self.x - self.alvo) - self.c * self.v) * h
            self.x += self.v * h
        if abs(self.x - self.alvo) < 0.25 and abs(self.v) < 1.5:
            self.x = self.alvo
            self.v = 0.0
            return True
        return False


class CapturadorEspectro:
    """Captura o stream de áudio do monitor do PipeWire e calcula magnitudes de 4 bandas espectrais."""

    def __init__(self, taxa: int = 8000, n_amostras: int = 256) -> None:
        self.taxa = taxa
        self.n = n_amostras
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._rodando = False
        self._bandas: tuple[float, float, float, float] = (0.16, 0.16, 0.16, 0.16)
        self._lock = threading.Lock()
        self._peak_tracker = 0.20
        self._ativo_com_som = False
        self._sink_atual: Optional[str] = None

        self._window = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (self.n - 1)) for i in range(self.n)]
        bits = int(math.log2(self.n))
        self._rev = [0] * self.n
        for i in range(self.n):
            r = 0
            for j in range(bits):
                if (i >> j) & 1:
                    r |= 1 << (bits - 1 - j)
            self._rev[i] = r

        self._twiddles = {}
        step = 2
        while step <= self.n:
            half = step // 2
            self._twiddles[step] = [
                math.e ** (-2j * math.pi * k / step) for k in range(half)
            ]
            step <<= 1

    def _fft(self, x: list[float]) -> list[complex]:
        n = self.n
        a = [x[self._rev[i]] for i in range(n)]
        step = 2
        while step <= n:
            half = step // 2
            tw = self._twiddles[step]
            for group in range(0, n, step):
                for k in range(half):
                    u = a[group + k]
                    v = a[group + k + half] * tw[k]
                    a[group + k] = u + v
                    a[group + k + half] = u - v
            step <<= 1
        return a

    def _obter_sink_monitor(self) -> str:
        """Obtém o nome do sink padrão para capturar o áudio de reprodução (música/vídeo)."""
        try:
            res = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=0.3
            )
            if res.returncode == 0 and res.stdout.strip():
                sink = res.stdout.strip()
                return sink if sink.endswith(".monitor") else f"{sink}.monitor"
        except Exception:
            pass
        return "@DEFAULT_MONITOR@"

    def iniciar(self, sink_monitor: Optional[str] = None) -> None:
        if self._rodando:
            return
        target = sink_monitor or self._obter_sink_monitor()
        if not target:
            return

        self._sink_atual = target
        self._rodando = True
        self._thread = threading.Thread(target=self._loop_captura, args=(target,), daemon=True)
        self._thread.start()

    def _loop_captura(self, target: str) -> None:
        tem_parec = shutil.which("parec") is not None
        if tem_parec:
            cmd = [
                "parec",
                "-d", target,
                f"--rate={self.taxa}",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=32",
                "--raw",
            ]
        else:
            sink_puro = target.removesuffix(".monitor")
            cmd = [
                "pw-record",
                "-P", "{ stream.capture.sink = true }",
                "--rate", str(self.taxa),
                "--channels", "1",
                "--format", "s16",
                "--latency", "32ms",
            ]
            if sink_puro and sink_puro != "@DEFAULT_MONITOR@":
                cmd.extend(["--target", sink_puro])
            cmd.append("-")
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception:
            self._rodando = False
            return

        chunk_bytes = self.n * 2
        while self._rodando and self._proc and self._proc.stdout:
            try:
                raw = self._proc.stdout.read(chunk_bytes)
                if not raw or len(raw) < chunk_bytes:
                    break

                samples = struct.unpack(f"<{self.n}h", raw)
                sum_sq = sum(s * s for s in samples)
                rms = math.sqrt(sum_sq / self.n) / 32768.0

                if rms < 0.0025:
                    with self._lock:
                        self._bandas = (0.16, 0.16, 0.16, 0.16)
                        self._ativo_com_som = False
                    continue

                windowed = [samples[i] * self._window[i] for i in range(self.n)]
                spec = self._fft(windowed)
                mags = [abs(spec[k]) / (self.n * 16384.0) for k in range(self.n // 2)]

                b0 = math.sqrt(sum(m * m for m in mags[2:8]) / 6.0) * 1.6
                b1 = math.sqrt(sum(m * m for m in mags[8:23]) / 15.0) * 2.4
                b2 = math.sqrt(sum(m * m for m in mags[23:56]) / 33.0) * 3.8
                b3 = math.sqrt(sum(m * m for m in mags[56:116]) / 60.0) * 5.5

                raw_max = max(b0, b1, b2, b3, 0.05)
                self._peak_tracker = max(self._peak_tracker * 0.985, raw_max, 0.08)

                def _norm(v: float) -> float:
                    ratio = min(1.0, max(0.0, v / self._peak_tracker))
                    return 0.16 + 0.84 * math.pow(ratio, 0.72)

                v0 = _norm(b0)
                v1 = _norm(b1)
                v2 = _norm(b2)
                v3 = _norm(b3)

                with self._lock:
                    self._bandas = (v0, v1, v2, v3)
                    self._ativo_com_som = True
            except Exception:
                break

        self._encerrar_proc()

    def parar(self) -> None:
        self._rodando = False
        self._encerrar_proc()
        with self._lock:
            self._bandas = (0.0, 0.0, 0.0, 0.0)
            self._ativo_com_som = False

    def _encerrar_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=0.2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def obter_bandas(self) -> tuple[float, float, float, float]:
        with self._lock:
            return self._bandas

    @property
    def ativo_com_som(self) -> bool:
        with self._lock:
            return self._ativo_com_som

    @property
    def rodando(self) -> bool:
        return self._rodando


class EqualizadorArea(Gtk.DrawingArea):
    """Visualizador de equalizador de áudio vetorial (Cairo) com 4 barras arredondadas fluidas.

    Sincronizado em tempo real com o espectro de áudio do PipeWire através de 4 molas
    físicas independentes (resposta ~0.12s-0.14s, amortecimento ~0.70) a 144Hz.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_name("eq-area")
        self.set_size_request(22, 16)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_visible(False)

        # 4 barras com molas físicas independentes
        self._molas = [
            Mola(0.20, resposta=0.12, amortecimento=0.70),
            Mola(0.20, resposta=0.13, amortecimento=0.72),
            Mola(0.20, resposta=0.14, amortecimento=0.72),
            Mola(0.20, resposta=0.12, amortecimento=0.68),
        ]
        self._alvos_tempo = 0.0
        self._cor_rgb = (0.796, 0.651, 0.969)  # Mauve Catppuccin padrão (203, 166, 247)
        self._ativo = False
        self._pausado = False
        self._mudo = False
        self._tick_id = 0
        self._ult_t = None
        self._capturador = CapturadorEspectro()

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
        self.set_visible(ativo or pausado)

        if ativo and not pausado and not mudo:
            self._capturador.iniciar()
            if not self._tick_id:
                self._ult_t = None
                try:
                    self._tick_id = self.add_tick_callback(self._passo)
                except Exception:
                    self._tick_id = 0
        else:
            self._capturador.parar()
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

    def atualizar_sink(self) -> None:
        """Notifica o capturador caso o dispositivo de saída tenha mudado."""
        if hasattr(self, "_capturador"):
            sink_novo = self._capturador._obter_sink_monitor()
            if sink_novo and sink_novo != self._capturador._sink_atual and self._ativo:
                self._capturador.parar()
                self._capturador.iniciar(sink_novo)

    def _passo(self, _widget, clock) -> bool:
        t = (clock.get_frame_time() / 1e6) if clock is not None else time.monotonic()
        dt = 0.0 if self._ult_t is None else t - self._ult_t
        self._ult_t = t
        dt = min(dt, 1.0 / 30.0)

        # Se tocando, alimenta as molas com as bandas reais do espectro de áudio da música/vídeo
        if self._ativo and not self._pausado and not self._mudo:
            if self._capturador and self._capturador.rodando:
                bandas = self._capturador.obter_bandas()
                if self._capturador.ativo_com_som:
                    for i, m in enumerate(self._molas):
                        m.ir_para(bandas[i])
                else:
                    # Silêncio na reprodução da mídia: repouso suave na linha de base
                    for m in self._molas:
                        m.ir_para(0.18)
            else:
                # Fallback de ritmo procedural caso a captura não esteja disponível
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
                        jitter = random.uniform(-0.06, 0.06)
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


LARGURA = 360                       # com mídia (capa + título + relógio + equalizador)
LARGURA_VAZIA = 175                 # sem mídia: recolhida (cápsula compacta precisa)
LARGURA_EXPANDIDA_VAZIA = 340       # sem mídia: expandida (painel hero consistente)
ALTURA_CABECALHO = 32
CONFIG_PATH = os.environ.get("ISLAND_CONFIG", os.path.expanduser("~/.config/island/config.toml"))
CONFIG_PADRAO = {
    "geral": {
        "cidade": "Araraquara",
        "monitor": "DP-2",
        "margem_topo": 8,
    },
    "rede": {
        "vpn_conexao": "Cloudflare-WARP",
        "adguard_url": "http://127.0.0.1:3000",
    },
    "audio": {
        "sink_fones": "analog-stereo",
        "sink_monitor": "hdmi-stereo",
    },
    "mpris": {
        "tick_posicao_ms": 500,
        "resync_intervalo_s": 5.0,
    }
}

def carregar_config() -> dict:
    cfg = {k: dict(v) for k, v in CONFIG_PADRAO.items()}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "rb") as f:
                dados = tomllib.load(f)
                for secao, valores in dados.items():
                    if isinstance(valores, dict) and secao in cfg:
                        cfg[secao].update(valores)
                    else:
                        cfg[secao] = valores
        except Exception as e:
            print(f"Erro ao carregar {CONFIG_PATH}: {e}", flush=True)
    return cfg

CONFIG = carregar_config()

MONITOR = CONFIG["geral"].get("monitor", "DP-2")
MARGEM_TOPO = int(CONFIG["geral"].get("margem_topo", 8))

IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"
CAMINHO_MPRIS = "/org/mpris/MediaPlayer2"
CACHE_ART = os.path.expanduser("~/.cache/island")

_DIR_ATUAL = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.environ.get(
    "ISLAND_CSS",
    os.path.join(_DIR_ATUAL, "island.css")
    if os.path.exists(os.path.join(_DIR_ATUAL, "island.css"))
    else os.path.expanduser("~/.config/hypr/scripts/island.css")
)

def obter_css() -> str:
    if os.path.exists(CSS_PATH):
        try:
            with open(CSS_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"Falha ao ler {CSS_PATH}: {e}", flush=True)
    return "window { background: transparent; } #ilha { background-color: #0c0d14; border-radius: 19px; }"

EQ_FRAMES = ["▃▅▃", "▄▆▂", "▅▇▄", "▆▅▆", "▇▃▇", "▅▂▅", "▃▄▃", "▂▆▄", "▄▇▅", "▆▄▂"]

DEBUG = False
DUMP = False
TESTE_CONTROLE = False


def log(msg: str) -> None:
    if DEBUG:
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# MPRIS (D-Bus puro — sem playerctl, sem plugin)
# ---------------------------------------------------------------------------
class Mpris:
    """Player ativo, escolhido entre todos os org.mpris.MediaPlayer2.*."""

    def __init__(self, ao_mudar) -> None:
        self.ao_mudar = ao_mudar
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.proxy: Gio.DBusProxy | None = None
        self.nome: str | None = None
        self._nomes_cache: list[str] | None = None
        self._proxies: dict[str, Gio.DBusProxy] = {}
        self._fixado_manual: str | None = None
        # Identity por (player, título): o KDE Connect troca o Identity no
        # mesmo nome de barramento quando o app do celular muda ("YouTube -
        # Galaxy A15 5G" -> "Spotify - ..."), então a chave inclui a faixa.
        self._ident: tuple[tuple[str, str], str] | None = None
        self._ident_cache: dict[tuple[str, str], str] = {}
        self._desktop_entry_cache: dict[str, str] = {}
        self._pos_base_us: int = 0
        self._pos_timestamp: float = time.monotonic()
        self._playback_rate: float = 1.0
        self._status: str = "Stopped"
        self._duracao: int = 0
        self._ultimo_sync_d_bus: float = 0.0
        self.bus.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
            self._ao_name_owner_changed)
        # Ouve mudanças de PlaybackStatus em qualquer player MPRIS para trocar automaticamente
        self.bus.signal_subscribe(
            None, "org.freedesktop.DBus.Properties", "PropertiesChanged",
            CAMINHO_MPRIS, IFACE_PLAYER, Gio.DBusSignalFlags.NONE,
            self._ao_props_qualquer_player)
        # idle: escolher() notifica o dono, que ainda está sendo construído
        GLib.idle_add(self.escolher)

    def _ao_name_owner_changed(self, _conn, _sender, _path, _iface, _signal, params, _user_data=None) -> None:
        try:
            nome, _old, _new = params.unpack()
            if nome.startswith("org.mpris.MediaPlayer2."):
                self._nomes_cache = None
                if not _new:
                    self._proxies.pop(nome, None)
                    self._desktop_entry_cache.pop(nome, None)
                    if hasattr(self, "_ident_cache"):
                        self._ident_cache = {k: v for k, v in self._ident_cache.items() if k[0] != nome}
                GLib.idle_add(self.escolher)
        except Exception:
            pass

    def _ao_props_qualquer_player(self, _conn, _sender, _path, _iface, _signal, params, _user_data=None) -> None:
        try:
            changed = params.unpack()[1]
            if "PlaybackStatus" in changed:
                GLib.idle_add(self.escolher)
        except Exception:
            pass

    # -- descoberta ---------------------------------------------------------
    def _nomes(self) -> list[str]:
        if self._nomes_cache is not None:
            return self._nomes_cache
        try:
            r = self.bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "ListNames", None, GLib.VariantType.new("(as)"),
                Gio.DBusCallFlags.NONE, 250, None)
            self._nomes_cache = [n for n in r.unpack()[0] if n.startswith("org.mpris.MediaPlayer2.")]
        except Exception:
            self._nomes_cache = []
        return self._nomes_cache

    def _proxy(self, nome: str):
        if nome in self._proxies:
            return self._proxies[nome]
        try:
            p = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None, nome, CAMINHO_MPRIS,
                IFACE_PLAYER, None)
            self._proxies[nome] = p
            return p
        except GLib.Error:
            return None
        except GLib.Error:
            return None

    @staticmethod
    def _prop(proxy, nome: str):
        try:
            v = proxy.get_cached_property(nome)
            return v.unpack() if v is not None else None
        except Exception:
            return None

    def escolher(self) -> bool:
        """Prefere o player fixado manualmente ou Playing > Paused > parado, e quem tem título."""
        nomes = self._nomes()
        if self._fixado_manual and self._fixado_manual not in nomes:
            self._fixado_manual = None

        candidatos = []
        for nome in nomes:
            p = self.proxy if nome == self.nome else self._proxy(nome)
            if p is None:
                continue
            st = self._prop(p, "PlaybackStatus") or "Stopped"
            md = self._prop(p, "Metadata") or {}
            tem = bool(md.get("xesam:title"))
            manual = 0 if (self._fixado_manual and nome == self._fixado_manual) else 1
            ordem = 0 if st == "Playing" else 1 if st == "Paused" else 2
            candidatos.append(((manual, ordem, 0 if tem else 1, 0 if nome == self.nome else 1),
                               nome, p))
        candidatos.sort(key=lambda c: c[0])

        if not candidatos:
            if self.proxy is not None:
                self.proxy, self.nome = None, None
                self._fixado_manual = None
                self.ao_mudar()
            return False

        _, nome, p = candidatos[0]
        if nome != self.nome:
            self.proxy, self.nome = p, nome
            p.connect("g-properties-changed", self._ao_mudar_propriedades)
            p.connect("g-signal", self._ao_sinal_player)
            self._status = self._prop(p, "PlaybackStatus") or "Stopped"
            md = self._prop(p, "Metadata") or {}
            self._duracao = int(md.get("mpris:length") or 0)
            self.sincronizar_posicao()
            log(f"player: {nome}")
            self.ao_mudar()
        return False

    def _ao_sinal_player(self, _proxy, _sender_name, signal_name, parameters) -> None:
        if signal_name == "Seeked":
            try:
                pos_us = int(parameters.unpack()[0])
                self._pos_base_us = pos_us
                self._pos_timestamp = time.monotonic()
                self._ultimo_sync_d_bus = self._pos_timestamp
                GLib.idle_add(self.ao_mudar)
            except Exception:
                pass

    def _ao_mudar_propriedades(self, proxy, changed_properties, invalidated_properties) -> None:
        mudancas = changed_properties.unpack() if hasattr(changed_properties, "unpack") else {}
        st_ant = self._status
        st = self._prop(proxy, "PlaybackStatus") or "Stopped"
        self._status = st
        if st_ant == "Playing" and st != "Playing":
            self._pos_base_us = self.posicao_estimada()
            self._pos_timestamp = time.monotonic()
        rate = self._prop(proxy, "Rate")
        if rate is not None:
            try:
                self._playback_rate = float(rate)
            except Exception:
                self._playback_rate = 1.0
        md = self._prop(proxy, "Metadata") or {}
        self._duracao = int(md.get("mpris:length") or 0)
        if not mudancas or any(k in mudancas for k in ("Metadata", "PlaybackStatus", "Rate")):
            self.sincronizar_posicao()
        self.ao_mudar()

    def sincronizar_posicao(self, forcar: bool = False) -> None:
        def _cb(pos):
            estimado = self.posicao_estimada()
            desvio_us = abs(pos - estimado)
            if forcar or desvio_us > 1_500_000 or self._status != "Playing":
                self._pos_base_us = pos
                self._pos_timestamp = time.monotonic()
            self._ultimo_sync_d_bus = time.monotonic()
        self.posicao(_cb)

    def posicao_estimada(self) -> int:
        if self._status != "Playing":
            return max(0, self._pos_base_us)
        delta_s = time.monotonic() - self._pos_timestamp
        estimado = int(self._pos_base_us + (delta_s * 1_000_000 * self._playback_rate))
        if self._duracao > 0:
            return min(self._duracao, max(0, estimado))
        return max(0, estimado)

    # -- leitura ------------------------------------------------------------
    def desktop_entry(self) -> str:
        if not self.nome:
            return ""
        if self.nome in self._desktop_entry_cache:
            return self._desktop_entry_cache[self.nome]
        try:
            r = self.bus.call_sync(
                self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.mpris.MediaPlayer2", "DesktopEntry")),
                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 200, None)
            val = str(r.unpack()[0] or "").strip()
        except GLib.Error:
            val = ""
        self._desktop_entry_cache[self.nome] = val
        return val

    def identidade(self, titulo: str = "") -> str:
        """Nome legível do player (propriedade MPRIS `Identity`)."""
        if not self.nome:
            return ""
        chave = (self.nome, titulo)
        if hasattr(self, "_ident_cache") and chave in self._ident_cache:
            return self._ident_cache[chave]
        if self._ident is not None and self._ident[0] == chave:
            return self._ident[1]
        try:
            r = self.bus.call_sync(
                self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.mpris.MediaPlayer2", "Identity")),
                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 20, None)
            valor = str(r.unpack()[0] or "").strip()
        except GLib.Error:
            valor = ""
        if not hasattr(self, "_ident_cache"):
            self._ident_cache = {}
        self._ident_cache[chave] = valor
        self._ident = (chave, valor)
        return valor

    def dados(self) -> dict | None:
        p = self.proxy
        if p is None:
            return None
        md = self._prop(p, "Metadata") or {}
        artistas = md.get("xesam:artist") or []
        if isinstance(artistas, str):
            artistas = [artistas]
        vol_prop = self._prop(p, "Volume")
        st = self._prop(p, "PlaybackStatus") or "Stopped"
        self._status = st
        dur = int(md.get("mpris:length") or 0)
        self._duracao = dur
        titulo = str(md.get("xesam:title") or "").strip()
        url = str(md.get("xesam:url") or "").strip()
        if not titulo and url:
            caminho_url = urllib.parse.unquote(url)
            titulo = os.path.basename(caminho_url) or "Áudio em reprodução"

        pode_play = self._prop(p, "CanPlay")
        pode_pause = self._prop(p, "CanPause")

        return {
            "titulo": titulo,
            "artista": ", ".join(str(a) for a in artistas),
            "album": str(md.get("xesam:album") or ""),
            "art": str(md.get("mpris:artUrl") or ""),
            "url": url,
            "duracao": dur,
            "status": st,
            "pode_prox": bool(self._prop(p, "CanGoNext")),
            "pode_ant": bool(self._prop(p, "CanGoPrevious")),
            "pode_play": bool(pode_play) if pode_play is not None else True,
            "pode_pause": bool(pode_pause) if pode_pause is not None else True,
            "pode_seek": bool(self._prop(p, "CanSeek")),
            "loop_status": self._prop(p, "LoopStatus") or "None",
            "shuffle": bool(self._prop(p, "Shuffle")),
            "trackid": str(md.get("mpris:trackid") or ""),
            "player": self.nome or "",
            "volume": float(vol_prop if vol_prop is not None else 1.0),
        }

    def ajustar_volume(self, delta: float) -> float | None:
        """Volume do PRÓPRIO player (0..1), pela propriedade MPRIS `Volume`."""
        p = self.proxy
        if p is None or not self.nome or not delta:
            return None
        atual = self._prop(p, "Volume")
        if atual is not None:
            novo = max(0.0, min(1.0, float(atual) + delta))
            log(f"volume do player: {atual:.2f} -> {novo:.2f}")
            self.bus.call(
                self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Set",
                GLib.Variant("(ssv)", (IFACE_PLAYER, "Volume", GLib.Variant("d", novo))),
                None, Gio.DBusCallFlags.NONE, 300, None, None)
            return novo
        return None

    def posicao(self, cb) -> None:
        """Consulta a posição do player via D-Bus assíncrono."""
        if self.proxy is None or not self.nome:
            return
        self.bus.call(
            self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (IFACE_PLAYER, "Position")),
            GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 300, None,
            lambda b, res: self._pos_resp(b, res, cb))

    def _pos_resp(self, bus, res, cb) -> None:
        try:
            cb(int(bus.call_finish(res).unpack()[0]))
        except GLib.Error:
            pass

    def comando(self, metodo: str) -> None:
        if self.proxy is None:
            return
        log(f"comando: {metodo}")
        self.proxy.call(metodo, None, Gio.DBusCallFlags.NONE, 300, None, None)

    def seek(self, alvo_us: int) -> None:
        """Pula para a posição absoluta alvo_us de forma segura com fallback garantido."""
        if self.proxy is None or not self.nome:
            return
        if not bool(self._prop(self.proxy, "CanSeek")):
            return
        log(f"seek para {alvo_us} us")
        self._pos_base_us = int(alvo_us)
        self._pos_timestamp = time.monotonic()
        self._ultimo_sync_d_bus = self._pos_timestamp
        md = self._prop(self.proxy, "Metadata") or {}
        trackid = md.get("mpris:trackid")

        def _fallback_relativo():
            def aplica_seek(pos_atual):
                diff = int(alvo_us - pos_atual)
                try:
                    self.proxy.call(
                        "Seek",
                        GLib.Variant("(x)", (diff,)),
                        Gio.DBusCallFlags.NONE, 300, None, None,
                    )
                except Exception as e:
                    log(f"Seek falhou: {e}")
            self.posicao(aplica_seek)

        if isinstance(trackid, str) and trackid.startswith("/") and not trackid.endswith("/NoTrack"):
            def _ao_set_position(proxy, res):
                try:
                    proxy.call_finish(res)
                except Exception:
                    _fallback_relativo()

            self.proxy.call(
                "SetPosition",
                GLib.Variant("(ox)", (trackid, int(alvo_us))),
                Gio.DBusCallFlags.NONE, 300, None,
                _ao_set_position,
            )
        else:
            _fallback_relativo()

    def alternar_shuffle(self) -> None:
        if self.proxy is None or not self.nome:
            return
        atual = bool(self._prop(self.proxy, "Shuffle"))
        novo = not atual
        self.bus.call(
            self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Set",
            GLib.Variant("(ssv)", (IFACE_PLAYER, "Shuffle", GLib.Variant("b", novo))),
            None, Gio.DBusCallFlags.NONE, 300, None, None)

    def alternar_loop(self) -> None:
        if self.proxy is None or not self.nome:
            return
        atual = self._prop(self.proxy, "LoopStatus") or "None"
        ordem = ["None", "Track", "Playlist"]
        try:
            idx = ordem.index(atual)
            novo = ordem[(idx + 1) % len(ordem)]
        except ValueError:
            novo = "Track"
        self.bus.call(
            self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Set",
            GLib.Variant("(ssv)", (IFACE_PLAYER, "LoopStatus", GLib.Variant("s", novo))),
            None, Gio.DBusCallFlags.NONE, 300, None, None)

    def total_players(self) -> int:
        try:
            return len(self._nomes())
        except Exception:
            return 0

    def alternar_proximo_player(self) -> None:
        nomes = self._nomes()
        if len(nomes) <= 1:
            return
        try:
            idx = nomes.index(self.nome)
            prox_nome = nomes[(idx + 1) % len(nomes)]
        except (ValueError, IndexError):
            prox_nome = nomes[0]

        self._fixado_manual = prox_nome
        self.escolher()


# ---------------------------------------------------------------------------
# Sistema (sem spawn onde dá: /proc direto; wpctl só para volume)
# ---------------------------------------------------------------------------
class Sistema:
    def __init__(self) -> None:
        self._ant = self._leitura_cpu()
        self._caminho_temp = None
        self._descobrir_sensor_temp()

    def _descobrir_sensor_temp(self) -> None:
        for p in glob.glob("/sys/class/hwmon/hwmon*"):
            try:
                with open(os.path.join(p, "name")) as f:
                    name = f.read().strip()
                if name in ("k10temp", "coretemp", "zenpower"):
                    for t in ("temp1_input", "temp2_input"):
                        tp = os.path.join(p, t)
                        if os.path.exists(tp):
                            self._caminho_temp = tp
                            break
                    if self._caminho_temp:
                        break
            except Exception:
                continue

    def temp_cpu(self) -> int | None:
        if not self._caminho_temp:
            self._descobrir_sensor_temp()
        if self._caminho_temp:
            try:
                with open(self._caminho_temp) as f:
                    return round(int(f.read().strip()) / 1000)
            except Exception:
                pass
        return None

    @staticmethod
    def _leitura_cpu() -> tuple[int, int]:
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + (v[4] if len(v) > 4 else 0)

    def cpu(self) -> int:
        total, ocioso = self._leitura_cpu()
        dt, do = total - self._ant[0], ocioso - self._ant[1]
        self._ant = (total, ocioso)
        return 0 if dt <= 0 else round(100 * (dt - do) / dt)

    @staticmethod
    def mem() -> int:
        info = {}
        with open("/proc/meminfo") as f:
            for linha in f:
                k, v = linha.split(":", 1)
                info[k] = int(v.split()[0])
        return round(100 * (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"])

class Volume:
    """Volume do sink padrão por EVENTO (`pactl subscribe`), não por polling.

    Medido nesta máquina: `wpctl get-volume` custa 8,3 ms por chamada, e uma
    chamada a cada 2 s só para pintar um número dava 0,40% de CPU — mais que o
    widget inteiro (0,33%). O `pactl subscribe` emite
    "Event 'change' on sink #56" a cada mudança de volume (verificado), então o
    `wpctl` passa a rodar só quando algo muda de fato.

    Se o `pactl` não existir, fica com o valor lido na subida — degradar para um
    número parado é melhor que voltar a pollar.
    """

    def __init__(self, ao_mudar, ao_mudar_mic=None) -> None:
        self._ao_mudar = ao_mudar
        self._ao_mudar_mic = ao_mudar_mic
        self.valor = None
        self.mudo = False
        self.mic_ativo = False
        self._proc = None
        self._fluxo = None
        self._sink_cache = None
        # sem LD_PRELOAD no filho: o preload existe para o GTK4 carregar o
        # layer-shell na ordem certa, não tem o que fazer no wpctl/pactl.
        self._ambiente = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}

    # -- leitura ------------------------------------------------------------
    def _ler(self) -> tuple[int | None, bool]:
        try:
            out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                                 capture_output=True, text=True, timeout=1,
                                 env=self._ambiente).stdout
            return round(float(out.split()[1]) * 100), "MUTED" in out
        except Exception:
            return None, False

    def _ler_sinks_disponiveis(self) -> list[str]:
        try:
            out = subprocess.run(["pactl", "list", "short", "sinks"],
                                 capture_output=True, text=True, timeout=0.5,
                                 env=self._ambiente).stdout
            sinks = []
            for linha in out.strip().splitlines():
                partes = linha.split()
                if len(partes) >= 2:
                    sinks.append(partes[1])
            return sinks
        except Exception:
            return []

    def _ler_sink(self) -> str:
        try:
            out = subprocess.run(["pactl", "get-default-sink"],
                                 capture_output=True, text=True, timeout=0.3,
                                 env=self._ambiente).stdout.strip()
            padrao_monitor = CONFIG["audio"].get("sink_monitor", "hdmi-stereo")
            if padrao_monitor in out or "hdmi" in out:
                return "monitor"
            return "fones"
        except Exception:
            return "fones"

    def sink_atual(self) -> str:
        """Retorna 'fones' ou 'monitor' com base no cache do sink padrão."""
        if self._sink_cache is None:
            self._sink_cache = self._ler_sink()
        return self._sink_cache

    def _atualizar_sink_async(self) -> None:
        def _worker():
            novo = self._ler_sink()
            if novo != self._sink_cache:
                self._sink_cache = novo
                GLib.idle_add(self._ao_mudar)
        threading.Thread(target=_worker, daemon=True).start()

    def alternar_sink(self) -> None:
        """Alterna entre fones e monitor de forma assíncrona baseando-se nos sinks disponíveis."""
        atual = self.sink_atual()
        quer_monitor = (atual == "fones")
        padrao_alvo = CONFIG["audio"].get("sink_monitor", "hdmi-stereo") if quer_monitor else CONFIG["audio"].get("sink_fones", "analog-stereo")
        self._sink_cache = "monitor" if quer_monitor else "fones"

        def _fazer():
            try:
                sinks = self._ler_sinks_disponiveis()
                alvo = None
                for s in sinks:
                    if padrao_alvo in s:
                        alvo = s
                        break
                if not alvo and sinks:
                    for s in sinks:
                        if (quer_monitor and ("hdmi" in s)) or (not quer_monitor and ("analog" in s)):
                            alvo = s
                            break
                if alvo:
                    subprocess.run(["pactl", "set-default-sink", alvo],
                                   capture_output=True, timeout=1.0, env=self._ambiente)
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def alternar_mudo(self) -> None:
        """Mudo no sink padrão de forma assíncrona; o evento do pactl traz o novo estado de volta."""
        def _fazer():
            try:
                subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
                               capture_output=True, timeout=1, env=self._ambiente)
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def ajustar(self, delta_pct: int) -> None:
        """Ajusta o volume do sink padrão (+2% ou -2%) de forma assíncrona."""
        sinal = "+" if delta_pct >= 0 else "-"
        valor = f"{abs(delta_pct)}%{sinal}"
        def _fazer():
            try:
                subprocess.run(["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", valor],
                               capture_output=True, timeout=1, env=self._ambiente)
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def definir(self, pct: int) -> None:
        """Define o volume absoluto do sink padrão (0 a 100%) de forma assíncrona."""
        val = max(0.0, min(1.5, pct / 100.0))
        def _fazer():
            try:
                subprocess.run(["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", f"{val:.2f}"],
                               capture_output=True, timeout=1, env=self._ambiente)
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    # -- assinatura ---------------------------------------------------------
    def iniciar(self) -> None:
        self.valor, self.mudo = self._ler()
        self._sink_cache = self._ler_sink()
        self._checar_mic()
        self._ao_mudar()
        self._assinar()
        if self.valor is None:            # pipewire ainda subindo, p.ex.
            self._tentativas = 0
            GLib.timeout_add_seconds(2, self._tentar_de_novo)

    def _checar_mic(self) -> None:
        """Verifica de forma assíncrona se há algum app capturando microfone (source-outputs)."""
        def _fazer():
            try:
                out = subprocess.run(["pactl", "list", "short", "source-outputs"],
                                     capture_output=True, text=True, timeout=0.3,
                                     env=self._ambiente).stdout
                ativo = bool(out.strip())
                if ativo != self.mic_ativo:
                    self.mic_ativo = ativo
                    if self._ao_mudar_mic:
                        GLib.idle_add(self._ao_mudar_mic, ativo)
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def _tentar_de_novo(self) -> bool:
        """Leitura inicial falhou: tenta por ~20 s antes de desistir."""
        self.reler()
        self._tentativas = getattr(self, "_tentativas", 0) + 1
        return self.valor is None and self._tentativas < 10

    def reler(self) -> None:
        """Lê o sink padrão e avisa só se mudou; leitura falha não apaga o valor."""
        novo, mudo = self._ler()
        if novo is None:
            return
        if (novo, mudo) != (self.valor, self.mudo):
            self.valor, self.mudo = novo, mudo
            self._ao_mudar()

    @staticmethod
    def _relevante(linha: str) -> bool:
        """O evento pode ter mudado volume/mudo do sink PADRÃO?"""
        if "sink-input" in linha:
            return False
        return any(k in linha for k in (" on sink ", " on server", " on card "))

    def _assinar(self) -> None:
        try:
            lancador = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE)
            lancador.unsetenv("LD_PRELOAD")
            self._proc = lancador.spawnv(["pactl", "subscribe"])
        except GLib.Error:
            log("pactl subscribe indisponível: volume fica estático")
            return
        self._fluxo = Gio.DataInputStream.new(self._proc.get_stdout_pipe())
        self._proxima()

    def _proxima(self) -> None:
        self._fluxo.read_line_async(GLib.PRIORITY_DEFAULT, None, self._linha, None)

    def _linha(self, fluxo, res, _=None) -> None:
        try:
            linha, _tam = fluxo.read_line_finish_utf8(res)
        except GLib.Error:
            linha = None
        if linha is None:                 # pactl morreu: tenta de novo em 5 s
            GLib.timeout_add_seconds(5, self._reassinar)
            return
        if "on source-output" in linha:
            self._checar_mic()
        elif any(k in linha for k in (" on server", " on card ")):
            self._atualizar_sink_async()
            self.reler()
        elif self._relevante(linha):
            self.reler()
        self._proxima()

    def _reassinar(self) -> bool:
        if self._proc is not None:
            self._proc.force_exit()
        self._assinar()
        self.reler()                      # pode ter mudado enquanto estava fora
        return False


# ---------------------------------------------------------------------------
# Controle de Rede, VPN, AdGuard e Celular (KDE Connect)
# ---------------------------------------------------------------------------
class RedeControle:
    """Gerencia Hotspot Wi-Fi, VPN WARP e AdGuard Home de forma ultra-rápida e assíncrona."""

    def __init__(self, ao_mudar=None) -> None:
        self._ao_mudar = ao_mudar
        self._hotspot_cache = False
        self._adguard_cache = False
        self._atualizando = False
        self.atualizar_async()

    def hotspot_ativo(self) -> bool:
        return self._hotspot_cache

    def vpn_ativa(self) -> bool:
        return os.path.exists("/sys/class/net/warp0")

    def adguard_ativo(self) -> bool:
        return self._adguard_cache

    def atualizar_async(self, callback_atualizar=None) -> None:
        if self._atualizando:
            return
        self._atualizando = True

        def _worker():
            # 1. Hotspot
            try:
                r = subprocess.run(["nmcli", "-t", "-f", "NAME", "con", "show", "--active"],
                                   capture_output=True, text=True, timeout=1.0)
                hotspot = "Hotspot" in r.stdout.splitlines()
            except Exception:
                hotspot = False

            # 2. AdGuard
            try:
                adguard_url = CONFIG["rede"].get("adguard_url", "http://127.0.0.1:3000")
                req = urllib.request.Request(f"{adguard_url}/control/status", headers={"User-Agent": "island"})
                with urllib.request.urlopen(req, timeout=0.4) as resp:
                    data = json.loads(resp.read().decode())
                    adguard = bool(data.get("protection_enabled", False))
            except Exception:
                adguard = False

            def _aplicar():
                self._hotspot_cache = hotspot
                self._adguard_cache = adguard
                self._atualizando = False
                if callback_atualizar:
                    callback_atualizar()
                elif self._ao_mudar:
                    self._ao_mudar()
                return False

            GLib.idle_add(_aplicar)

        threading.Thread(target=_worker, daemon=True).start()

    def alternar_hotspot(self, callback_atualizar=None) -> None:
        def _fazer():
            try:
                ativo = self._hotspot_cache
                cmd = ["nmcli", "connection", "down" if ativo else "up", "Hotspot"]
                subprocess.run(cmd, capture_output=True, timeout=6)
            except Exception:
                pass
            self.atualizar_async(callback_atualizar)
        threading.Thread(target=_fazer, daemon=True).start()

    def alternar_vpn(self, callback_atualizar=None) -> None:
        def _fazer():
            try:
                ativo = self.vpn_ativa()
                vpn_nome = CONFIG["rede"].get("vpn_conexao", "Cloudflare-WARP")
                cmd = ["nmcli", "connection", "down" if ativo else "up", vpn_nome]
                subprocess.run(cmd, capture_output=True, timeout=6)
            except Exception:
                pass
            if callback_atualizar:
                GLib.idle_add(callback_atualizar)
            elif self._ao_mudar:
                GLib.idle_add(self._ao_mudar)
        threading.Thread(target=_fazer, daemon=True).start()

    def alternar_adguard(self, callback_atualizar=None) -> None:
        def _fazer():
            try:
                ativo = self._adguard_cache
                adguard_url = CONFIG["rede"].get("adguard_url", "http://127.0.0.1:3000")
                req = urllib.request.Request(
                    f"{adguard_url}/control/protection",
                    data=json.dumps({"enabled": not ativo}).encode(),
                    headers={"Content-Type": "application/json", "User-Agent": "island"}
                )
                urllib.request.urlopen(req, timeout=1.0)
            except Exception:
                pass
            self.atualizar_async(callback_atualizar)
        threading.Thread(target=_fazer, daemon=True).start()


class KdeConnect:
    """Monitor reativo da bateria e status do celular via D-Bus."""

    def __init__(self, ao_mudar) -> None:
        self._ao_mudar = ao_mudar
        self.dispositivo_nome = "Celular"
        self.conectado = False
        self.carga = None
        self.carregando = False
        self._dev_id = None
        self._bus = None
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._bus.signal_subscribe(
                "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
                self._ao_name_owner_changed)
            self._bus.signal_subscribe(
                "org.kde.kdeconnect", "org.kde.kdeconnect.daemon", None,
                "/modules/kdeconnect", None, Gio.DBusSignalFlags.NONE,
                lambda *_: GLib.idle_add(self._iniciar))
        except Exception:
            pass
        GLib.idle_add(self._iniciar)

    def _ao_name_owner_changed(self, _conn, _sender, _path, _iface, _signal, params, _user_data=None) -> None:
        try:
            nome, _old, new_owner = params.unpack()
            if nome == "org.kde.kdeconnect":
                if new_owner:
                    GLib.idle_add(self._iniciar)
                else:
                    self.conectado = False
                    self.carga = None
                    self._dev_id = None
                    GLib.idle_add(self._ao_mudar)
        except Exception:
            pass

    def _iniciar(self) -> bool:
        try:
            if not self._bus:
                self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = self._bus.call_sync(
                "org.kde.kdeconnect",
                "/modules/kdeconnect",
                "org.kde.kdeconnect.daemon",
                "devices",
                GLib.Variant("(bb)", (True, True)),
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None
            )
            devs = res.unpack()[0]
            if devs:
                self._dev_id = devs[0]
                self.conectado = True
                try:
                    res_nome = self._bus.call_sync(
                        "org.kde.kdeconnect",
                        f"/modules/kdeconnect/devices/{self._dev_id}",
                        "org.freedesktop.DBus.Properties",
                        "Get",
                        GLib.Variant("(ss)", ("org.kde.kdeconnect.device", "name")),
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None
                    )
                    self.dispositivo_nome = res_nome.unpack()[0]
                except Exception:
                    pass

                try:
                    res_bat = self._bus.call_sync(
                        "org.kde.kdeconnect",
                        f"/modules/kdeconnect/devices/{self._dev_id}/battery",
                        "org.freedesktop.DBus.Properties",
                        "GetAll",
                        GLib.Variant("(s)", ("org.kde.kdeconnect.device.battery",)),
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None
                    )
                    props = res_bat.unpack()[0]
                    self.carga = props.get("charge")
                    self.carregando = props.get("isCharging", False)
                except Exception:
                    pass

                self._bus.signal_subscribe(
                    "org.kde.kdeconnect",
                    "org.kde.kdeconnect.device.battery",
                    None,
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._ao_sinal_bateria
                )
                self._ao_mudar()
            else:
                self.conectado = False
                self.carga = None
                self._ao_mudar()
        except Exception:
            self.conectado = False
        return False

    def _ao_sinal_bateria(self, _conn, _sender, _path, _iface, sinal, params) -> None:
        try:
            if sinal == "refreshed":
                is_chg, chg = params.unpack()
                self.carregando = is_chg
                self.carga = chg
                GLib.idle_add(self._ao_mudar)
            elif sinal == "PropertiesChanged":
                _iface_name, changed, _inv = params.unpack()
                if "charge" in changed:
                    self.carga = changed["charge"]
                if "isCharging" in changed:
                    self.carregando = changed["isCharging"]
                GLib.idle_add(self._ao_mudar)
        except Exception:
            pass

    def tocar_ou_abrir(self) -> None:
        """Toca o celular (Find My Phone) ou abre o app do KDE Connect."""
        def _fazer():
            try:
                if self._bus and self._dev_id:
                    self._bus.call_sync(
                        "org.kde.kdeconnect",
                        f"/modules/kdeconnect/devices/{self._dev_id}/findmyphone",
                        "org.kde.kdeconnect.device.findmyphone",
                        "ring",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None
                    )
            except Exception:
                subprocess.Popen(["kdeconnect-app"])
        threading.Thread(target=_fazer, daemon=True).start()


def limpar_texto_notif(texto: str) -> str:
    """Decodifica entidades HTML e limpa tags para exibição limpa."""
    if not texto:
        return ""
    import html, re
    t = html.unescape(texto)
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join(t.split())


def ler_historico_notificacoes(limite: int = 5) -> list[dict]:
    """Lê as últimas notificações do histórico do Dunst."""
    try:
        r = subprocess.run(["dunstctl", "history"], capture_output=True, text=True, timeout=0.4)
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        lista = data.get("data", [[]])[0]
        resultado = []
        for item in lista[:limite]:
            app = item.get("appname", {}).get("data", "")
            summary = item.get("summary", {}).get("data", "")
            body = item.get("body", {}).get("data", "")
            icon_path = item.get("icon_path", {}).get("data", "")
            nid = item.get("id", {}).get("data", 0)
            resultado.append({
                "id": nid,
                "app": app or "Notificação",
                "titulo": limpar_texto_notif(summary),
                "corpo": limpar_texto_notif(body),
                "icone": icon_path
            })
        return resultado
    except Exception:
        return []


def limpar_historico_notificacoes() -> None:
    """Limpa todo o histórico de notificações do Dunst."""
    try:
        subprocess.run(["dunstctl", "history-clear"], capture_output=True, timeout=0.5)
    except Exception:
        pass


class Hypr:
    """Áreas de trabalho e janela ativa por EVENTO (socket2), não por polling.

    O socket de eventos entrega `workspace>>`, `focusedmon>>`, `moveworkspace>>`
    e `activewindow>>CLASSE,TÍTULO`. Só a lista de workspaces e o dono de cada um
    exigem `hyprctl` — e só quando algo muda, não a cada N segundos.
    """

    EVENTOS_WS = ("workspace", "focusedmon", "moveworkspace",
                  "createworkspace", "destroyworkspace",
                  "monitoradded", "monitorremoved")

    def __init__(self, monitor: str, ao_mudar_ws, ao_mudar_janela, ao_fullscreen=None) -> None:
        self._monitor = monitor
        self._ao_mudar_ws = ao_mudar_ws
        self._ao_mudar_janela = ao_mudar_janela
        self._ao_fullscreen = ao_fullscreen
        self.monitor_id = None
        self._fluxo = None
        self._ambiente = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}

    def _caminho(self) -> str:
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return f"{rt}/hypr/{sig}/.socket2.sock"

    def iniciar(self) -> None:
        self.reler()
        tit, cls = self._ler_janela()
        self._ao_mudar_janela(tit, cls)
        self._conectar()

    def _ler_janela(self) -> tuple[str, str]:
        """Título e classe da janela focada.

        Sem esta leitura inicial o campo fica vazio até a próxima troca de
        janela: o evento `activewindow>>` só avisa quando algo muda.
        """
        try:
            w = json.loads(subprocess.run(
                ["hyprctl", "activewindow", "-j"], capture_output=True,
                text=True, timeout=0.5, env=self._ambiente).stdout)
            return (w.get("title") or "").strip(), (w.get("class") or "").strip()
        except Exception:
            return "", ""

    def _conectar(self) -> None:
        try:
            conexao = Gio.SocketClient().connect(
                Gio.UnixSocketAddress.new(self._caminho()), None)
        except GLib.Error as e:
            log(f"socket2 indisponível ({e.message}): pontos ficam estáticos")
            return
        self._fluxo = Gio.DataInputStream.new(conexao.get_input_stream())
        self._proxima()

    def _proxima(self) -> None:
        self._fluxo.read_line_async(GLib.PRIORITY_DEFAULT, None, self._linha, None)

    def _linha(self, fluxo, res, _=None) -> None:
        try:
            linha, _tam = fluxo.read_line_finish_utf8(res)
        except GLib.Error:
            linha = None
        if linha is None:                  # compositor caiu/reiniciou
            GLib.timeout_add_seconds(5, self._reconectar)
            return
        evento, _, dados = linha.partition(">>")
        if evento in self.EVENTOS_WS:
            self.reler()
        elif evento == "activewindow":
            classe, _, titulo = dados.partition(",")
            self._ao_mudar_janela(titulo, classe)
        elif evento == "fullscreen":
            if self._ao_fullscreen:
                self._ao_fullscreen(dados.strip() == "1")
        self._proxima()

    def _reconectar(self) -> bool:
        self._conectar()
        return False

    def reler(self) -> None:
        """Estado real: workspaces existentes e o ativo no monitor da ilha."""
        try:
            ws = json.loads(subprocess.run(
                ["hyprctl", "workspaces", "-j"], capture_output=True,
                text=True, timeout=0.5, env=self._ambiente).stdout)
            mons = json.loads(subprocess.run(
                ["hyprctl", "monitors", "-j"], capture_output=True,
                text=True, timeout=0.5, env=self._ambiente).stdout)
        except Exception:
            return
        ativo = None
        for m in mons:
            if m.get("name") == self._monitor:
                ativo = m.get("activeWorkspace", {}).get("id")
                self.monitor_id = m.get("id")
        existentes = sorted({w["id"] for w in ws
                             if w.get("monitor") == self._monitor and w.get("id", 0) > 0})
        self._ao_mudar_ws(ativo, existentes)


# ---------------------------------------------------------------------------
# Capa do álbum e Cor Dinâmica
# ---------------------------------------------------------------------------
def escolher_cor(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int] | None:
    """Extrai a cor dominante vibrante por histograma de matiz com piso de luminosidade."""
    bins = {}
    for r, g, b in pixels:
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.25 or not (0.15 < l < 0.9):
            continue
        acc = bins.setdefault(int(h * 24) % 24, [0.0, 0.0, 0.0, 0.0])
        peso = s * (1.0 - abs(2.0 * l - 1.0))
        acc[0] += peso
        acc[1] += r * peso
        acc[2] += g * peso
        acc[3] += b * peso
    if not bins:
        return None
    peso, sr, sg, sb = max(bins.values(), key=lambda a: a[0])
    if peso <= 0:
        return None
    h, l, s = colorsys.rgb_to_hls(sr / peso / 255.0, sg / peso / 255.0, sb / peso / 255.0)
    l = min(0.78, max(0.58, l))
    return tuple(int(c * 255.0) for c in colorsys.hls_to_rgb(h, l, max(s, 0.45)))


def limpar_cache_antigo() -> None:
    try:
        if not os.path.exists(CACHE_ART):
            return
        arquivos = []
        agora = time.time()
        for f in os.scandir(CACHE_ART):
            if f.is_file() and f.name.endswith(".img"):
                st = f.stat()
                arquivos.append((st.st_mtime, f.path))
                if agora - st.st_mtime > 14 * 86400:
                    try:
                        os.unlink(f.path)
                    except Exception:
                        pass
        if len(arquivos) > 300:
            arquivos.sort(key=lambda x: x[0])
            for _, path in arquivos[:len(arquivos) - 300]:
                try:
                    os.unlink(path)
                except Exception:
                    pass
    except Exception:
        pass


def carregar_art(uri: str, cb) -> None:
    """Carrega textura e cor de forma 100% assíncrona, segura e sem travar a thread de UI."""
    if not uri:
        cb(None, None)
        return

    def _worker():
        caminho = None
        try:
            if uri.startswith("file://"):
                caminho = urllib.parse.unquote(uri[7:])
            elif uri.startswith("http://") or uri.startswith("https://"):
                os.makedirs(CACHE_ART, exist_ok=True)
                hash_nome = hashlib.sha1(uri.encode()).hexdigest() + ".img"
                caminho = os.path.join(CACHE_ART, hash_nome)
                if not os.path.exists(caminho):
                    req = urllib.request.Request(uri, headers={"User-Agent": "hypr-dynamic-island/1.0"})
                    with urllib.request.urlopen(req, timeout=4.5) as resp:
                        dados = resp.read()
                    temp_dest = caminho + f".tmp.{os.getpid()}"
                    with open(temp_dest, "wb") as f:
                        f.write(dados)
                    os.replace(temp_dest, caminho)
                    limpar_cache_antigo()
            else:
                caminho = uri

            if not caminho or not os.path.exists(caminho):
                GLib.idle_add(lambda: cb(None, None))
                return

            # Decodificação assíncrona em tamanho otimizado (192px cobre hero 2x sem pesar)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(caminho, 192, 192, True)
            pb_tiny = GdkPixbuf.Pixbuf.new_from_file_at_scale(caminho, 24, 24, True)

            pixels_lista = []
            if pb_tiny:
                raw_pixels = pb_tiny.get_pixels()
                n_ch = pb_tiny.get_n_channels()
                rowstride = pb_tiny.get_rowstride()
                w, h = pb_tiny.get_width(), pb_tiny.get_height()
                for y in range(h):
                    row = y * rowstride
                    for x in range(w):
                        idx = row + x * n_ch
                        pixels_lista.append((raw_pixels[idx], raw_pixels[idx + 1], raw_pixels[idx + 2]))

            cor = escolher_cor(pixels_lista) or (203, 166, 247)

            def _aplicar():
                if pb:
                    try:
                        tex = Gdk.Texture.new_for_pixbuf(pb)
                        cb(tex, cor)
                        return False
                    except Exception:
                        pass
                cb(None, None)
                return False

            GLib.idle_add(_aplicar)

        except Exception as e:
            log(f"Falha ao carregar arte {uri}: {e}")
            GLib.idle_add(lambda: cb(None, None))

    threading.Thread(target=_worker, daemon=True).start()
# ---------------------------------------------------------------------------
# A ilha
# ---------------------------------------------------------------------------
def mmss(us: int) -> str:
    s = max(0, us // 1_000_000)
    return f"{s // 60}:{s % 60:02d}"


class Ilha:
    def __init__(self, app: Gtk.Application) -> None:
        self.mpris = Mpris(self.atualizar)
        self.sistema = Sistema()
        self.rede = RedeControle(self._mostrar_sistema)
        self.kde = KdeConnect(self._ao_mudar_kde)
        self._temp = self.sistema.temp_cpu() or 45
        self._vol = None            # cache: quem atualiza é o evento do pactl
        self._mudo = False
        self._cpu = 0
        self._mem = 0
        self._atividades = {}       # id -> {icon, text, subtext, progress, color, ttl, timer_id}
        self._mic_ativo = False
        self._rec_ativo = False
        self._checando_gravador = False
        self._fullscreen = False
        self._mon_geom = None
        self._ultima_classe_janela = None
        self.volume = Volume(self._ao_mudar_volume, self._ao_mudar_mic)
        self.hypr = Hypr(MONITOR, self._atualizar_ws, self._atualizar_janela, self._ao_fullscreen)
        self._art_uri = "\0"
        self._expandido = False
        self._fixado = False        # clique no cabeçalho prende aberto
        self._tem_player = None     # None = o MPRIS ainda não respondeu
        self._pronto = False        # só depois de saber, a ilha se redimensiona
        self._fora_desde = 0.0
        self._canto = None          # (x, y) global, lido do compositor
        self._h_colapsada = None    # valor medido da cápsula recolhida em tempo de execução
        self._anim_src = 0
        self._tick_id = 0
        self._taxa_hz = obter_refresh_rate(MONITOR)
        self._mola_w = Mola(LARGURA_VAZIA, resposta=0.32, amortecimento=0.92)
        self._mola_h = Mola(ALTURA_CABECALHO, resposta=0.32, amortecimento=0.92)
        self._ult_frame_time = None
        self._ultimo_w = None
        self._ultimo_h = None
        self._cor_dinamica_pendente = None
        self._historico_cache = []
        self._carregando_historico = False
        self._mola_vol = Mola(0.0, resposta=0.22, amortecimento=0.82)
        self._vol_tick_id = 0
        self._ult_t_vol = None
        self._hud_ativo = False
        self._hud_timer = 0
        self._notif_ativa = False
        self._notif_expandida = False
        self._notif_arrastando = False
        self._notif_timer = 0
        self._notif_dados = {"app": "", "summary": "", "body": "", "urgency": "NORMAL", "time": ""}
        self._status = "Stopped"    # PlaybackStatus do player mostrado
        self._capa_ok = False       # a capa do cabeçalho tem textura válida?
        self._dunst = self._proxy_dunst()
        self._dnd_ativo = self._checar_dnd()
        self._sock = None
        self._eq_timer = 0
        self._eq_idx = 0

        # -- cabeçalho ------------------------------------------------------
        # Gtk.Image, não Gtk.Picture: o Picture reporta o tamanho da TEXTURA
        # como tamanho natural (medido: 498x373 para uma capa 300x300), e como
        # set_size_request é só mínimo, ele estourava a largura da ilha.
        self.capa = Gtk.Image()
        self.capa.set_name("capa")
        self.capa.set_pixel_size(24)
        self.capa.set_overflow(Gtk.Overflow.HIDDEN)
        self.capa.set_visible(False)

        self.titulo = Gtk.Label(label="")
        self.titulo.set_name("titulo")
        self.titulo.set_ellipsize(Pango.EllipsizeMode.END)
        self.titulo.set_max_width_chars(24)
        self.titulo.set_xalign(0)
        self.titulo.set_hexpand(True)

        self.eq_area = EqualizadorArea()
        self.eq_lbl = Gtk.Label(label="")
        self.eq_lbl.set_name("eq")
        self.eq_lbl.set_visible(False)
        self.anel_progresso = AnelProgresso(raio=6.5, largura_linha=2.0)

        self.relogio = Gtk.Label(label="")
        self.relogio.set_name("relogio")

        self.pin_icone = Gtk.Label(label="󰤱")
        self.pin_icone.set_name("pin-icone")
        self.pin_icone.set_visible(False)

        self.dnd_icone = Gtk.Label(label="󰂛")
        self.dnd_icone.set_name("dnd-icone")
        self.dnd_icone.set_visible(self._dnd_ativo)

        # -- HUD transitório de volume (estilo Dynamic Island) ----------------
        self.hud_icone = Gtk.Label(label="󰕿")
        self.hud_icone.set_name("hud-icone")
        self.hud_barra = Gtk.ProgressBar()
        self.hud_barra.set_name("hud-barra")
        self.hud_barra.set_hexpand(True)
        self.hud_pct = Gtk.Label(label="")
        self.hud_pct.set_name("hud-pct")
        self.hud_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.hud_box.set_name("hud-box")
        self.hud_box.set_hexpand(True)
        self.hud_box.append(self.hud_icone)
        self.hud_box.append(self.hud_barra)
        self.hud_box.append(self.hud_pct)
        self.hud_box.set_visible(False)

        # -- HUD transitório de notificação (estilo Dynamic Island) -----------
        self.notif_icone = Gtk.Label(label="󰂚")
        self.notif_icone.set_name("notif-icone")

        self.notif_app = Gtk.Label(label="")
        self.notif_app.set_name("notif-app")

        self.notif_sep = Gtk.Label(label="•")
        self.notif_sep.set_name("notif-sep")

        self.notif_texto = Gtk.Label(label="")
        self.notif_texto.set_name("notif-texto")
        self.notif_texto.set_ellipsize(Pango.EllipsizeMode.END)
        self.notif_texto.set_max_width_chars(42)
        self.notif_texto.set_hexpand(True)
        self.notif_texto.set_xalign(0)

        self.notif_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.notif_box.set_name("notif-box")
        self.notif_box.set_hexpand(True)
        self.notif_box.append(self.notif_icone)
        self.notif_box.append(self.notif_app)
        self.notif_box.append(self.notif_sep)
        self.notif_box.append(self.notif_texto)
        self.notif_box.set_visible(False)
        self.notif_box.set_cursor_from_name("pointer")

        # -- pontos das áreas de trabalho (modo barra: sem mídia) --------------
        # Ocupa o lugar do título quando não há player. hexpand para empurrar o
        # relógio para a direita mesmo com o título escondido.
        self.dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self.dots.set_name("dots-track")
        self.dots.set_valign(Gtk.Align.CENTER)
        self.dots.set_halign(Gtk.Align.START)
        self.dots.set_hexpand(True)
        self._pontos = []
        for i in range(5):                 # 1..5 são os atalhos Super+1..5
            ponto = self._criar_ponto(i + 1)
            self.dots.append(ponto)
            self._pontos.append(ponto)

        # -- Live Activities e Indicadores no cabeçalho ----------------------
        self.ativ_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ativ_box.set_name("ativ-box")
        self.ativ_box.set_valign(Gtk.Align.CENTER)
        self.ativ_box.set_hexpand(True)
        self.ativ_box.set_visible(False)
        self.ativ_icone = Gtk.Label(label="󰔟")
        self.ativ_icone.set_name("ativ-icone")
        self.ativ_texto = Gtk.Label(label="")
        self.ativ_texto.set_name("ativ-texto")
        self.ativ_texto.set_ellipsize(Pango.EllipsizeMode.END)
        self.ativ_box.append(self.ativ_icone)
        self.ativ_box.append(self.ativ_texto)

        self.ativ_badge = Gtk.Label(label="")
        self.ativ_badge.set_name("ativ-badge")
        self.ativ_badge.set_valign(Gtk.Align.CENTER)
        self.ativ_badge.set_visible(False)

        self.mic_indicador = Gtk.Label(label="󰍬")
        self.mic_indicador.set_name("mic-indicador")
        self.mic_indicador.set_valign(Gtk.Align.CENTER)
        self.mic_indicador.set_tooltip_text("Microfone em uso")
        self.mic_indicador.set_visible(False)

        self.rec_indicador = Gtk.Label(label="󰻃")
        self.rec_indicador.set_name("rec-indicador")
        self.rec_indicador.set_valign(Gtk.Align.CENTER)
        self.rec_indicador.set_tooltip_text("Gravação de tela em andamento")
        self.rec_indicador.set_visible(False)

        # -- Slots do Cabeçalho com Crossfade -------------------------------
        self.slot_leading = Gtk.Stack()
        self.slot_leading.set_name("slot-leading")
        self.slot_leading.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.slot_leading.set_transition_duration(180)
        self.slot_leading.set_valign(Gtk.Align.CENTER)

        leading_vazio = Gtk.Box()
        self.slot_leading.add_named(leading_vazio, "vazio")
        self.slot_leading.add_named(self.dots, "dots")
        self.slot_leading.add_named(self.capa, "capa")

        ativ_leading_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        ativ_leading_box.append(self.anel_progresso)
        ativ_leading_box.append(self.ativ_box)
        self.slot_leading.add_named(ativ_leading_box, "atividade")

        self.slot_center = Gtk.Stack()
        self.slot_center.set_name("slot-center")
        self.slot_center.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.slot_center.set_transition_duration(180)
        self.slot_center.set_valign(Gtk.Align.CENTER)
        self.slot_center.set_hexpand(True)

        center_vazio = Gtk.Box()
        self.slot_center.add_named(center_vazio, "vazio")
        self.slot_center.add_named(self.titulo, "titulo")
        self.slot_center.add_named(self.hud_box, "hud")
        self.slot_center.add_named(self.notif_box, "notif")

        self.slot_trailing = Gtk.Stack()
        self.slot_trailing.set_name("slot-trailing")
        self.slot_trailing.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.slot_trailing.set_transition_duration(180)
        self.slot_trailing.set_valign(Gtk.Align.CENTER)

        trailing_relogio_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        trailing_relogio_box.append(self.mic_indicador)
        trailing_relogio_box.append(self.rec_indicador)
        trailing_relogio_box.append(self.relogio)
        trailing_relogio_box.append(self.eq_area)
        trailing_relogio_box.append(self.dnd_icone)
        trailing_relogio_box.append(self.pin_icone)

        trailing_vazio = Gtk.Box()
        self.slot_trailing.add_named(trailing_vazio, "vazio")
        self.slot_trailing.add_named(trailing_relogio_box, "relogio")
        self.slot_trailing.add_named(self.ativ_badge, "atividade")

        cabecalho = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cabecalho.set_name("cabecalho")
        cabecalho.append(self.slot_leading)
        cabecalho.append(self.slot_center)
        cabecalho.append(self.slot_trailing)
        self.cabecalho = cabecalho

        # -- painel ---------------------------------------------------------
        # 1. Cartão Hero (mídia ativa ou contexto do desktop em repouso)
        # Topo: Capa (52x52) + Coluna de Metadados (Título, Artista, Álbum/Badge)
        self.hero_capa = Gtk.Image()
        self.hero_capa.set_name("hero-capa")
        self.hero_capa.set_pixel_size(80)
        self.hero_capa.set_overflow(Gtk.Overflow.HIDDEN)
        self.hero_capa.set_cursor_from_name("pointer")
        self.hero_capa.set_tooltip_text("Clique: focar | Scroll: volume do player")
        clique_hero_capa = Gtk.GestureClick()
        clique_hero_capa.connect("released", lambda *_: self._ao_clicar_focar_player())
        self.hero_capa.add_controller(clique_hero_capa)

        scroll_hero_capa = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_hero_capa.connect("scroll", self._ao_scroll_player)
        self.hero_capa.add_controller(scroll_hero_capa)

        self.hero_titulo = Gtk.Label(label="")
        self.hero_titulo.set_name("hero-titulo")
        self.hero_titulo.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_titulo.set_max_width_chars(26)
        self.hero_titulo.set_xalign(0)
        self.hero_titulo.set_cursor_from_name("pointer")
        clique_hero_tit = Gtk.GestureClick()
        clique_hero_tit.connect("released", lambda *_: self._ao_clicar_focar_player())
        self.hero_titulo.add_controller(clique_hero_tit)

        self.artista = Gtk.Label(label="")
        self.artista.set_name("hero-artista")
        self.artista.set_ellipsize(Pango.EllipsizeMode.END)
        self.artista.set_max_width_chars(28)
        self.artista.set_xalign(0)

        self.hero_album = Gtk.Label(label="")
        self.hero_album.set_name("hero-album")
        self.hero_album.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_album.set_max_width_chars(30)
        self.hero_album.set_xalign(0)

        self.hero_info_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.hero_info_col.set_hexpand(True)
        self.hero_info_col.set_valign(Gtk.Align.CENTER)
        self.hero_info_col.append(self.hero_titulo)
        self.hero_info_col.append(self.artista)
        self.hero_info_col.append(self.hero_album)

        self.hero_topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.hero_topo.set_cursor_from_name("pointer")
        self.hero_topo.set_tooltip_text("Focar janela do reprodutor")
        clique_hero_topo = Gtk.GestureClick()
        clique_hero_topo.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clique_hero_topo.connect("released", lambda *_: self._ao_clicar_focar_player())
        self.hero_topo.add_controller(clique_hero_topo)
        self.hero_topo.append(self.hero_capa)
        self.hero_topo.append(self.hero_info_col)

        # Barra de Progresso com Timestamps nas Pontas
        self.tempo_atual = Gtk.Label(label="0:00")
        self.tempo_atual.set_name("hero-tempo-atual")
        self.tempo_total = Gtk.Label(label="0:00")
        self.tempo_total.set_name("hero-tempo-total")

        self.barra = Gtk.ProgressBar()
        self.barra.set_fraction(0)
        self.barra.set_cursor_from_name("pointer")
        self.barra.set_hexpand(True)
        self._arrastando_barra = False

        drag_barra = Gtk.GestureDrag()
        drag_barra.connect("drag-begin", self._ao_drag_barra_inicio)
        drag_barra.connect("drag-update", self._ao_drag_barra_atualizar)
        drag_barra.connect("drag-end", self._ao_drag_barra_fim)
        self.barra.add_controller(drag_barra)

        self.linha_progresso = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.linha_progresso.set_valign(Gtk.Align.CENTER)
        self.linha_progresso.append(self.tempo_atual)
        self.linha_progresso.append(self.barra)
        self.linha_progresso.append(self.tempo_total)

        # Controles de Transporte com Play Circular Centralizado, Shuffle e Repeat
        self.b_shuffle = Gtk.Button()
        self.b_shuffle.set_child(self._icone("media-playlist-shuffle-symbolic"))
        self.b_shuffle.add_css_class("controle")
        self.b_shuffle.add_css_class("hero-sub-btn")
        self.b_shuffle.set_tooltip_text("Ordem Aleatória (Shuffle)")
        self.b_shuffle.connect("clicked", lambda *_: self.mpris.alternar_shuffle())

        self.b_ant = self._botao("media-skip-backward-symbolic", "Previous")
        self.b_ant.set_tooltip_text("Faixa Anterior")

        self.b_play = self._botao("media-playback-start-symbolic", "PlayPause")
        self.b_play.add_css_class("play-btn")
        self.b_play.set_tooltip_text("Reproduzir / Pausar")

        self.b_prox = self._botao("media-skip-forward-symbolic", "Next")
        self.b_prox.set_tooltip_text("Próxima Faixa")

        self.b_repeat = Gtk.Button()
        self.b_repeat.set_child(self._icone("media-playlist-repeat-symbolic"))
        self.b_repeat.add_css_class("controle")
        self.b_repeat.add_css_class("hero-sub-btn")
        self.b_repeat.set_tooltip_text("Repetição (Repeat)")
        self.b_repeat.connect("clicked", lambda *_: self.mpris.alternar_loop())

        self.controles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.controles.set_halign(Gtk.Align.CENTER)
        self.controles.append(self.b_shuffle)
        self.controles.append(self.b_ant)
        self.controles.append(self.b_play)
        self.controles.append(self.b_prox)
        self.controles.append(self.b_repeat)

        # -- Página 1: Mídia (Now Playing) -----------------------------------
        # Estado com player ativo
        self.hero_media_active_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.hero_media_active_box.append(self.hero_topo)
        self.hero_media_active_box.append(self.linha_progresso)
        self.hero_media_active_box.append(self.controles)

        # Estado vazio elegante quando nenhum player está ativo
        self.hero_media_empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.hero_media_empty_box.set_halign(Gtk.Align.CENTER)
        self.hero_media_empty_box.set_valign(Gtk.Align.CENTER)
        self.hero_media_empty_box.set_hexpand(True)
        self.hero_media_empty_box.set_vexpand(True)

        empty_icon = Gtk.Label(label="󰓇")
        empty_icon.add_css_class("media-empty-icon")

        empty_tit = Gtk.Label(label="Nenhuma mídia ativa")
        empty_tit.add_css_class("media-empty-titulo")

        btn_abrir_spotify = Gtk.Button(label="󰓇 Abrir Spotify")
        btn_abrir_spotify.add_css_class("media-empty-btn")
        btn_abrir_spotify.set_cursor_from_name("pointer")
        btn_abrir_spotify.connect("clicked", lambda *_: self._ao_abrir_spotify())

        self.hero_media_empty_box.append(empty_icon)
        self.hero_media_empty_box.append(empty_tit)
        self.hero_media_empty_box.append(btn_abrir_spotify)
        self.hero_media_empty_box.set_visible(False)

        self.page_midia = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.page_midia.set_name("page-midia")
        self.page_midia.append(self.hero_media_active_box)
        self.page_midia.append(self.hero_media_empty_box)
        self.hero_media_box = self.page_midia  # compatibilidade com referências existentes

        # -- Página 2: Produtividade (Daily Hub) -----------------------------
        # 1. Linha Superior: Data por extenso à esquerda + Clima à direita
        self.idle_data = Gtk.Label(label="")
        self.idle_data.set_name("idle-data")
        self.idle_data.set_xalign(0)
        self.idle_data.set_hexpand(True)
        self.data = self.idle_data  # compatibilidade com referências existentes

        self.idle_clima = Gtk.Label(label="")
        self.idle_clima.set_name("idle-clima")
        self.idle_clima.set_cursor_from_name("pointer")
        cidade_nome = CONFIG["geral"].get("cidade", "Araraquara")
        self.idle_clima.set_tooltip_text(f"Clima em {cidade_nome} (clique para atualizar)")
        clique_clima = Gtk.GestureClick()
        clique_clima.connect("released", lambda *_: self._atualizar_clima_async(forcar=True))
        self.idle_clima.add_controller(clique_clima)

        self.idle_topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.idle_topo.append(self.idle_data)
        self.idle_topo.append(self.idle_clima)

        # 2. Linha Central: Mini Calendário Semanal (7 dias com início na segunda)
        self.cal_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.cal_box.set_name("cal-semana")
        self.cal_box.set_halign(Gtk.Align.CENTER)
        self._ultimo_dia_cal = None
        self._construir_mini_calendario()

        # 3. Linha Inferior: Janela Ativa à esquerda + Uptime do Sistema à direita
        self.janela_icone = Gtk.Label(label="󰖲")
        self.janela_icone.set_name("janela-icone")

        self.janela = Gtk.Label(label="")
        self.janela.set_name("janela")
        self.janela.set_ellipsize(Pango.EllipsizeMode.END)
        self.janela.set_max_width_chars(24)
        self.janela.set_xalign(0)
        self.janela.set_hexpand(True)

        self.idle_uptime = Gtk.Label(label="")
        self.idle_uptime.set_name("idle-uptime")

        self.idle_rodape = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.idle_rodape.append(self.janela_icone)
        self.idle_rodape.append(self.janela)
        self.idle_rodape.append(self.idle_uptime)

        self.page_produtividade = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.page_produtividade.set_name("page-produtividade")
        self.page_produtividade.append(self.idle_topo)
        self.page_produtividade.append(self.cal_box)
        self.page_produtividade.append(self.idle_rodape)
        self.hero_idle_box = self.page_produtividade  # compatibilidade com referências existentes

        # -- modo notificação expandida no cartão hero ----------------------
        self.notif_detalhe_app = Gtk.Label(label="")
        self.notif_detalhe_app.set_name("notif-detalhe-app")
        self.notif_detalhe_app.set_halign(Gtk.Align.START)
        self.notif_detalhe_app.set_hexpand(True)

        self.notif_detalhe_tempo = Gtk.Label(label="Agora")
        self.notif_detalhe_tempo.set_name("notif-detalhe-tempo")
        self.notif_detalhe_tempo.set_halign(Gtk.Align.END)

        linha_notif_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        linha_notif_header.append(self.notif_detalhe_app)
        linha_notif_header.append(self.notif_detalhe_tempo)

        self.notif_detalhe_titulo = Gtk.Label(label="")
        self.notif_detalhe_titulo.set_name("notif-detalhe-titulo")
        self.notif_detalhe_titulo.set_wrap(True)
        self.notif_detalhe_titulo.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.notif_detalhe_titulo.set_max_width_chars(38)
        self.notif_detalhe_titulo.set_xalign(0)

        self.notif_detalhe_corpo = Gtk.Label(label="")
        self.notif_detalhe_corpo.set_name("notif-detalhe-corpo")
        self.notif_detalhe_corpo.set_wrap(True)
        self.notif_detalhe_corpo.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.notif_detalhe_corpo.set_max_width_chars(38)
        self.notif_detalhe_corpo.set_selectable(True)
        self.notif_detalhe_corpo.set_xalign(0)

        self.btn_notif_copiar = Gtk.Button(label="󰅍 Copiar")
        self.btn_notif_copiar.add_css_class("notif-btn")
        self.btn_notif_copiar.set_cursor_from_name("pointer")
        self.btn_notif_copiar.connect("clicked", self._ao_copiar_notificacao)

        self.btn_notif_fechar = Gtk.Button(label="󰅖 Descartar")
        self.btn_notif_fechar.add_css_class("notif-btn")
        self.btn_notif_fechar.add_css_class("fechar")
        self.btn_notif_fechar.set_cursor_from_name("pointer")
        self.btn_notif_fechar.connect("clicked", lambda *_: self._esconder_hud_notif())

        linha_notif_acoes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        linha_notif_acoes.set_halign(Gtk.Align.END)
        linha_notif_acoes.set_margin_top(4)
        linha_notif_acoes.append(self.btn_notif_copiar)
        linha_notif_acoes.append(self.btn_notif_fechar)

        self.hero_notif_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.hero_notif_box.append(linha_notif_header)
        self.hero_notif_box.append(self.notif_detalhe_titulo)
        self.hero_notif_box.append(self.notif_detalhe_corpo)
        self.hero_notif_box.append(linha_notif_acoes)

        # -- Página 3: Central de Controle & Sistema -------------------------
        # Linha 1: CPU
        self.sys_icone_cpu = Gtk.Label(label="󰻠")
        self.sys_icone_cpu.add_css_class("sys-icone")
        self.sys_icone_cpu.add_css_class("cpu")

        self.sys_nome_cpu = Gtk.Label(label="CPU")
        self.sys_nome_cpu.add_css_class("sys-nome")

        self.sys_barra_cpu = Gtk.ProgressBar()
        self.sys_barra_cpu.add_css_class("sys-barra")
        self.sys_barra_cpu.add_css_class("cpu")
        self.sys_barra_cpu.set_hexpand(True)

        self.sys_val_cpu = Gtk.Label(label="0%")
        self.sys_val_cpu.add_css_class("sys-val")
        self.sys_val_cpu.set_xalign(1.0)

        linha_cpu = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        linha_cpu.add_css_class("sys-row")
        linha_cpu.append(self.sys_icone_cpu)
        linha_cpu.append(self.sys_nome_cpu)
        linha_cpu.append(self.sys_barra_cpu)
        linha_cpu.append(self.sys_val_cpu)
        linha_cpu.set_cursor_from_name("pointer")
        linha_cpu.set_tooltip_text("Uso do processador (clique para abrir gerenciador)")
        clique_cpu = Gtk.GestureClick()
        clique_cpu.connect("released", self._ao_clicar_processos)
        linha_cpu.add_controller(clique_cpu)

        # Linha 2: RAM
        self.sys_icone_mem = Gtk.Label(label="󰍛")
        self.sys_icone_mem.add_css_class("sys-icone")
        self.sys_icone_mem.add_css_class("mem")

        self.sys_nome_mem = Gtk.Label(label="RAM")
        self.sys_nome_mem.add_css_class("sys-nome")

        self.sys_barra_mem = Gtk.ProgressBar()
        self.sys_barra_mem.add_css_class("sys-barra")
        self.sys_barra_mem.add_css_class("mem")
        self.sys_barra_mem.set_hexpand(True)

        self.sys_val_mem = Gtk.Label(label="0%")
        self.sys_val_mem.add_css_class("sys-val")
        self.sys_val_mem.set_xalign(1.0)

        linha_mem = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        linha_mem.add_css_class("sys-row")
        linha_mem.append(self.sys_icone_mem)
        linha_mem.append(self.sys_nome_mem)
        linha_mem.append(self.sys_barra_mem)
        linha_mem.append(self.sys_val_mem)
        linha_mem.set_cursor_from_name("pointer")
        linha_mem.set_tooltip_text("Uso de memória RAM (clique para abrir gerenciador)")
        clique_mem = Gtk.GestureClick()
        clique_mem.connect("released", self._ao_clicar_processos)
        linha_mem.add_controller(clique_mem)

        # Linha 3: Volume
        self.sys_icone_vol = Gtk.Label(label="󰕿")
        self.sys_icone_vol.add_css_class("sys-icone")
        self.sys_icone_vol.add_css_class("vol")
        self.sys_icone_vol.set_cursor_from_name("pointer")
        self.sys_icone_vol.set_tooltip_text("Alternar mudo")
        clique_mute = Gtk.GestureClick()
        clique_mute.connect("released", lambda *_: self.volume.alternar_mudo())
        self.sys_icone_vol.add_controller(clique_mute)

        self.sys_nome_vol = Gtk.Label(label="Vol")
        self.sys_nome_vol.add_css_class("sys-nome")

        self.sys_barra_vol = Gtk.ProgressBar()
        self.sys_barra_vol.add_css_class("sys-barra")
        self.sys_barra_vol.add_css_class("vol")
        self.sys_barra_vol.set_hexpand(True)
        self.sys_barra_vol.set_cursor_from_name("pointer")
        self.sys_barra_vol.set_tooltip_text("Clique ou arraste para ajustar volume")

        clique_b_vol = Gtk.GestureClick()
        clique_b_vol.connect("released", self._ao_click_barra_vol)
        self.sys_barra_vol.add_controller(clique_b_vol)

        drag_b_vol = Gtk.GestureDrag()
        drag_b_vol.connect("drag-update", self._ao_drag_barra_vol)
        self.sys_barra_vol.add_controller(drag_b_vol)

        self.sys_val_vol = Gtk.Label(label="0%")
        self.sys_val_vol.add_css_class("sys-val")
        self.sys_val_vol.set_xalign(1.0)

        self.btn_audio_sink = Gtk.Button(label="󰋋 Fones")
        self.btn_audio_sink.add_css_class("btn-sink")
        self.btn_audio_sink.set_cursor_from_name("pointer")
        self.btn_audio_sink.set_tooltip_text("Saída de áudio: Fones de Ouvido (Clique para alternar para o Monitor 27G4)")
        self.btn_audio_sink.connect("clicked", lambda *_: self.volume.alternar_sink())

        linha_vol = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        linha_vol.add_css_class("sys-row")
        linha_vol.append(self.sys_icone_vol)
        linha_vol.append(self.sys_nome_vol)
        linha_vol.append(self.sys_barra_vol)
        linha_vol.append(self.sys_val_vol)
        linha_vol.append(self.btn_audio_sink)

        # Divisor fino para separar métricas dos atalhos
        sep_sistema = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep_sistema.add_css_class("sys-sep")

        # Quick Toggles Grid (Rede, VPN, AdGuard e Celular)
        self.grid_toggles = Gtk.Grid()
        self.grid_toggles.set_column_spacing(6)
        self.grid_toggles.set_row_spacing(6)
        self.grid_toggles.set_column_homogeneous(True)
        self.grid_toggles.set_margin_top(2)
        self.grid_toggles.set_margin_bottom(2)

        # 1. Hotspot
        self.btn_hotspot = Gtk.Button(label="󰤨  Hotspot")
        self.btn_hotspot.add_css_class("quick-toggle")
        self.btn_hotspot.set_cursor_from_name("pointer")
        self.btn_hotspot.set_tooltip_text("Ponto de Acesso Wi-Fi (wlan0) - Clique para alternar")
        self.btn_hotspot.connect("clicked", lambda *_: self.rede.alternar_hotspot(self._mostrar_sistema))
        self.grid_toggles.attach(self.btn_hotspot, 0, 0, 1, 1)

        # 2. WireGuard VPN (WARP)
        self.btn_vpn = Gtk.Button(label="󰌾  WARP")
        self.btn_vpn.add_css_class("quick-toggle")
        self.btn_vpn.set_cursor_from_name("pointer")
        self.btn_vpn.set_tooltip_text("WireGuard VPN (Cloudflare WARP) - Clique para alternar")
        self.btn_vpn.connect("clicked", lambda *_: self.rede.alternar_vpn(self._mostrar_sistema))
        self.grid_toggles.attach(self.btn_vpn, 1, 0, 1, 1)

        # 3. AdGuard Home DNS
        self.btn_adguard = Gtk.Button(label="󰒃  AdGuard")
        self.btn_adguard.add_css_class("quick-toggle")
        self.btn_adguard.set_cursor_from_name("pointer")
        self.btn_adguard.set_tooltip_text("AdGuard Home DNS (Bloqueador de anúncios) - Clique para alternar")
        self.btn_adguard.connect("clicked", lambda *_: self.rede.alternar_adguard(self._mostrar_sistema))
        self.grid_toggles.attach(self.btn_adguard, 0, 1, 1, 1)

        # 4. Celular (KDE Connect)
        self.btn_phone = Gtk.Button(label="󰄡  Celular")
        self.btn_phone.add_css_class("quick-toggle")
        self.btn_phone.add_css_class("phone-chip")
        self.btn_phone.set_cursor_from_name("pointer")
        self.btn_phone.set_tooltip_text("Celular (KDE Connect) - Clique para localizar")
        self.btn_phone.connect("clicked", lambda *_: self.kde.tocar_ou_abrir())
        self.grid_toggles.attach(self.btn_phone, 1, 1, 1, 1)

        sep_toggles = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep_toggles.add_css_class("sys-sep")

        # Botões de ação rápida compactos (Dock na Página 3)
        self.b_screenshot = Gtk.Button()
        self.b_screenshot.set_child(self._icone("camera-photo-symbolic"))
        self.b_screenshot.add_css_class("acao-btn")
        self.b_screenshot.set_cursor_from_name("pointer")
        self.b_screenshot.set_tooltip_text("Captura de tela (Área)")
        self.b_screenshot.connect("clicked", self._ao_clicar_screenshot)

        self.b_clipboard = Gtk.Button()
        self.b_clipboard.set_child(self._icone("edit-paste-symbolic"))
        self.b_clipboard.add_css_class("acao-btn")
        self.b_clipboard.set_cursor_from_name("pointer")
        self.b_clipboard.set_tooltip_text("Histórico da Área de Transferência")
        self.b_clipboard.connect("clicked", self._ao_clicar_clipboard)

        self.b_dnd = Gtk.Button()
        self.b_dnd.set_child(self._icone("notifications-disabled-symbolic"))
        self.b_dnd.add_css_class("acao-btn")
        if self._dnd_ativo:
            self.b_dnd.add_css_class("dnd-ativo")
        self.b_dnd.set_cursor_from_name("pointer")
        self.b_dnd.set_tooltip_text("Alternar Modo Não Perturbe (DND)")
        self.b_dnd.connect("clicked", self._ao_clicar_dnd)

        self.b_player = Gtk.Button()
        self.b_player.set_child(self._icone("audio-speakers-symbolic"))
        self.b_player.add_css_class("acao-btn")
        self.b_player.set_cursor_from_name("pointer")
        self.b_player.set_tooltip_text("Alternar reprodutor de mídia")
        self.b_player.set_visible(False)
        self.b_player.connect("clicked", self._ao_clicar_trocar_player)

        self.b_power = Gtk.Button()
        self.b_power.set_child(self._icone("system-shutdown-symbolic"))
        self.b_power.add_css_class("acao-btn")
        self.b_power.add_css_class("power")
        self.b_power.set_cursor_from_name("pointer")
        self.b_power.set_tooltip_text("Menu de Energia")
        self.b_power.connect("clicked", self._ao_clicar_power)

        self.acoes_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.acoes_box.set_name("acoes-box")
        self.acoes_box.set_halign(Gtk.Align.CENTER)
        self.acoes_box.append(self.b_screenshot)
        self.acoes_box.append(self.b_clipboard)
        self.acoes_box.append(self.b_dnd)
        self.acoes_box.append(self.b_player)
        self.acoes_box.append(self.b_power)

        self.page_sistema = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.page_sistema.set_name("page-sistema")
        self.page_sistema.append(linha_cpu)
        self.page_sistema.append(linha_mem)
        self.page_sistema.append(linha_vol)
        self.page_sistema.append(sep_sistema)
        self.page_sistema.append(self.grid_toggles)
        self.page_sistema.append(sep_toggles)
        self.page_sistema.append(self.acoes_box)

        # Referências de compatibilidade
        self.vol_lbl = self.sys_val_vol
        self.proc_lbl = Gtk.Label()
        self.sistema_box = Gtk.Box()
        self.footer_box = Gtk.Box()

        # -- Página 4: Histórico de Notificações ------------------------------
        self.page_historico = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.page_historico.set_name("page-historico")

        hist_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hist_header.add_css_class("notif-hist-header")

        hist_title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hist_title_box.set_hexpand(True)
        lbl_h_ico = Gtk.Label(label="󰂚")
        lbl_h_ico.add_css_class("notif-hist-header-title")
        lbl_h_txt = Gtk.Label(label="Histórico")
        lbl_h_txt.add_css_class("notif-hist-header-title")
        self.lbl_hist_count = Gtk.Label(label="0")
        self.lbl_hist_count.add_css_class("notif-hist-badge")
        hist_title_box.append(lbl_h_ico)
        hist_title_box.append(lbl_h_txt)
        hist_title_box.append(self.lbl_hist_count)

        self.btn_limpar_historico = Gtk.Button(label="󰅖 Limpar")
        self.btn_limpar_historico.add_css_class("btn-limpar-hist")
        self.btn_limpar_historico.set_cursor_from_name("pointer")
        self.btn_limpar_historico.set_tooltip_text("Limpar histórico de notificações")
        self.btn_limpar_historico.connect("clicked", self._ao_clicar_limpar_historico)

        hist_header.append(hist_title_box)
        hist_header.append(self.btn_limpar_historico)

        self.box_lista_historico = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.page_historico.append(hist_header)
        self.page_historico.append(self.box_lista_historico)

        # -- Stack de Navegação Paginada ------------------------------------
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        self.stack.set_transition_duration(260)
        self.stack.set_hhomogeneous(True)
        self.stack.set_vhomogeneous(False)
        self.stack.set_interpolate_size(False)
        self.stack.set_overflow(Gtk.Overflow.HIDDEN)
        self.stack.add_named(self.page_midia, "midia")
        self.stack.add_named(self.page_produtividade, "produtividade")
        self.stack.add_named(self.page_sistema, "sistema")
        self.stack.add_named(self.page_historico, "historico")
        self.stack.add_named(self.hero_notif_box, "notificacao")

        # -- Indicador de Páginas Interativo (Page Dots) ---------------------
        self.page_dots_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.page_dots_box.set_name("page-dots")
        self.page_dots_box.set_halign(Gtk.Align.CENTER)
        self.page_dots_box.set_valign(Gtk.Align.CENTER)
        self._dots = {}

        for nome, dica in [("midia", "Mídia (Now Playing)"),
                           ("produtividade", "Produtividade (Daily Hub)"),
                           ("sistema", "Central de Controle & Sistema"),
                           ("historico", "Histórico de Notificações")]:
            dot = Gtk.Box()
            dot.add_css_class("page-dot")
            dot.set_cursor_from_name("pointer")
            dot.set_tooltip_text(dica)
            g_dot = Gtk.GestureClick()
            g_dot.connect("released", lambda _g, _n, _x, _y, n=nome: self._mudar_pagina(n))
            dot.add_controller(g_dot)
            self.page_dots_box.append(dot)
            self._dots[nome] = dot

        self._ultimo_scroll_sessao = 0.0

        # -- Banner de Atividade ao Vivo no Cartão Hero ---------------------
        self.hero_activity_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.hero_activity_banner.set_name("hero-activity-banner")
        self.hero_activity_banner.set_visible(False)

        icon_box = Gtk.Box()
        icon_box.set_name("hero-activity-icon-box")
        icon_box.set_valign(Gtk.Align.CENTER)
        self.hero_activity_icon = Gtk.Label(label="󰔟")
        self.hero_activity_icon.set_name("hero-activity-icon")
        icon_box.append(self.hero_activity_icon)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_col.set_hexpand(True)
        text_col.set_valign(Gtk.Align.CENTER)
        self.hero_activity_title = Gtk.Label(label="")
        self.hero_activity_title.set_name("hero-activity-title")
        self.hero_activity_title.set_halign(Gtk.Align.START)
        self.hero_activity_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_activity_subtext = Gtk.Label(label="")
        self.hero_activity_subtext.set_name("hero-activity-subtext")
        self.hero_activity_subtext.set_halign(Gtk.Align.START)
        self.hero_activity_subtext.set_ellipsize(Pango.EllipsizeMode.END)
        self.hero_activity_progresso = Gtk.ProgressBar()
        self.hero_activity_progresso.set_name("hero-activity-progresso")
        self.hero_activity_progresso.set_hexpand(True)
        self.hero_activity_progresso.set_visible(False)
        text_col.append(self.hero_activity_title)
        text_col.append(self.hero_activity_subtext)
        text_col.append(self.hero_activity_progresso)

        self.hero_activity_btn = Gtk.Button(label="󰅖")
        self.hero_activity_btn.set_name("hero-activity-btn")
        self.hero_activity_btn.set_valign(Gtk.Align.CENTER)
        self.hero_activity_btn.set_tooltip_text("Encerrar atividade")
        self.hero_activity_btn.connect("clicked", lambda *_: self._encerrar_atividade_ativa())

        self.hero_activity_banner.append(icon_box)
        self.hero_activity_banner.append(text_col)
        self.hero_activity_banner.append(self.hero_activity_btn)

        # -- Cartão Principal Unificado -------------------------------------
        self.hero_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.hero_card.set_name("hero-card")
        self.hero_card.set_overflow(Gtk.Overflow.HIDDEN)
        self.hero_card.set_vexpand(True)
        self.hero_card.append(self.hero_activity_banner)
        self.hero_card.append(self.stack)
        self.hero_card.append(self.page_dots_box)

        # Scroll sobre o cartão navega entre as sessões paginadas
        scroll_sessoes = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_sessoes.connect("scroll", self._ao_scroll_sessoes)
        self.hero_card.add_controller(scroll_sessoes)

        painel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        painel.set_name("painel")
        painel.set_vexpand(True)
        painel.append(self.hero_card)
        self.painel = painel

        scroll_painel = Gtk.ScrolledWindow()
        scroll_painel.set_name("scroll-painel")
        scroll_painel.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        scroll_painel.set_propagate_natural_height(True)
        scroll_painel.set_propagate_natural_width(True)
        scroll_painel.set_child(painel)

        self.revelador = Gtk.Revealer()
        self.revelador.set_transition_type(Gtk.RevealerTransitionType.NONE)
        self.revelador.set_child(scroll_painel)
        self.revelador.set_visible(False)
        self.hero_card.set_opacity(0.0)

        # Mini barra de progresso tátil na borda da pílula recolhida
        self.pilula_progresso = Gtk.ProgressBar()
        self.pilula_progresso.set_name("pilula-progresso")
        self.pilula_progresso.set_hexpand(True)
        self.pilula_progresso.set_visible(False)

        # -- pílula ---------------------------------------------------------
        pilula = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pilula.set_name("ilha")
        pilula.set_size_request(LARGURA_VAZIA, -1)
        pilula.append(cabecalho)
        pilula.append(self.pilula_progresso)
        pilula.append(self.revelador)
        self.pilula = pilula

        # scroll na pílula para ajustar volume (+2% / -2%)
        scroll_ilha = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_ilha.connect("scroll", self._ao_scroll)
        pilula.add_controller(scroll_ilha)

        # hover por leitura do cursor (ver cursor_pos()); clique prende aberto
        clique = Gtk.GestureClick()
        clique.connect("released", self._ao_clicar)
        cabecalho.add_controller(clique)

        drag_cabecalho = Gtk.GestureDrag()
        drag_cabecalho.connect("drag-begin", self._ao_drag_notif_inicio)
        drag_cabecalho.connect("drag-update", self._ao_drag_notif_atualizar)
        drag_cabecalho.connect("drag-end", self._ao_drag_notif_fim)
        cabecalho.add_controller(drag_cabecalho)

        # -- janela ---------------------------------------------------------
        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_child(pilula)
        Gtk4LayerShell.init_for_window(self.win)
        Gtk4LayerShell.set_namespace(self.win, "island")
        Gtk4LayerShell.set_layer(self.win, Gtk4LayerShell.Layer.TOP)
        # Monitor fixo: sem set_monitor a surface nasce no monitor primário do
        # GDK (ordem do compositor) — medido: com o foco no HDMI-A-2 ela ainda
        # nascia no DP-2, e a ordem muda ao replugar cabo.
        alvo = monitor_por_nome(MONITOR)
        if alvo is not None:
            Gtk4LayerShell.set_monitor(self.win, alvo)
            log(f"monitor da ilha: {MONITOR}")
        else:
            log(f"monitor '{MONITOR}' não encontrado — caindo no monitor do foco")
        Gtk4LayerShell.set_anchor(self.win, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_margin(self.win, Gtk4LayerShell.Edge.TOP, MARGEM_TOPO)
        Gtk4LayerShell.set_exclusive_zone(self.win, -1)   # não reserva espaço
        Gtk4LayerShell.set_keyboard_mode(
            self.win, Gtk4LayerShell.KeyboardMode.NONE)   # nunca rouba teclado

        self._css_provider = Gtk.CssProvider()
        self._recarregar_css()
        Gtk.StyleContext.add_provider_for_display(
            self.win.get_display(), self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._iniciar_monitor_css()

        self.win.present()

        self.atualizar()
        self._tick_relogio()
        self.volume.iniciar()
        self.hypr.iniciar()
        self._iniciar_ipc()
        if DUMP:
            GLib.timeout_add(1500, self._dump)
        # SIGUSR1 alterna o "preso aberto" (mesmo efeito do clique): serve para
        # bindar numa tecla. Expandir sem prender não adianta — o hover recolhe
        # 450ms depois, porque o cursor não está sobre a ilha.
        add_signal = (GLibUnix.signal_add if GLibUnix
                      else GLib.unix_signal_add)
        add_signal(GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                   lambda: (self._ao_clicar(), True)[1])
        add_signal(GLib.PRIORITY_DEFAULT, signal.SIGTERM,
                   lambda: (app.quit(), False)[1])
        add_signal(GLib.PRIORITY_DEFAULT, signal.SIGINT,
                   lambda: (app.quit(), False)[1])
        GLib.timeout_add(1000, self._tick_relogio)
        tick_pos_ms = int(CONFIG["mpris"].get("tick_posicao_ms", 500))
        GLib.timeout_add(tick_pos_ms, self._tick_posicao)
        GLib.timeout_add(2000, self._tick_sistema)
        GLib.timeout_add(80, self._tick_hover)
        GLib.timeout_add(400, self._medir_colapsada)
        GLib.timeout_add(600, self._ajustar_agora)
        GLib.timeout_add_seconds(1, lambda: (self._atualizar_clima_async(), False)[1])
        GLib.timeout_add_seconds(1800, lambda: (self._atualizar_clima_async(), True)[1])
        if TESTE_CONTROLE:
            GLib.timeout_add(2500, self._testar_controle)

    def _recarregar_css(self) -> None:
        try:
            self._css_provider.load_from_data(obter_css())
            log("CSS da ilha recarregado com sucesso.")
        except Exception as e:
            log(f"Falha ao carregar CSS: {e}")

    def _iniciar_monitor_css(self) -> None:
        try:
            gfile = Gio.File.new_for_path(CSS_PATH)
            self._css_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._css_monitor.connect("changed", lambda *_: GLib.idle_add(self._recarregar_css))
        except Exception as e:
            log(f"Monitor de CSS não iniciado: {e}")

    def _medir_colapsada(self) -> bool:
        """Guarda a altura real da pílula recolhida (não usar constante)."""
        if self._h_colapsada is None and not self._expandido:
            h = self.win.get_height()
            if 0 < h <= 60:
                self._h_colapsada = h
            else:
                self._h_colapsada = ALTURA_CABECALHO
            self._mola_h.x = self._mola_h.alvo = float(self._h_colapsada)
            log(f"altura colapsada medida: {self._h_colapsada}")
        return False

    def _ajustar_agora(self) -> bool:
        """Primeiro ajuste de tamanho, depois que o MPRIS já respondeu.

        Antes disso `_tem_player` é None e qualquer medição seria chute: o
        escolher() do Mpris roda num idle e pode ainda não ter chegado.
        """
        self._pronto = True
        w = self._largura_alvo()
        h = self._altura_alvo(self._expandido)
        self._mola_w.x = self._mola_w.alvo = float(w)
        self._mola_h.x = self._mola_h.alvo = float(h)
        self._aplicar_conteudo()
        return False

    def _largura_alvo(self, expandido: bool | None = None) -> int:
        exp = self._expandido if expandido is None else expandido
        if getattr(self, "_notif_ativa", False):
            return 380 if exp else getattr(self, "_notif_largura_alvo", 360)
        if getattr(self, "_hud_ativo", False):
            return 260
        if self._tem_player:
            return LARGURA
        if getattr(self, "_atividades", None):
            return LARGURA if exp else 260
        return LARGURA_EXPANDIDA_VAZIA if exp else LARGURA_VAZIA

    def _render_cabecalho(self) -> None:
        """Renderizador único e determinístico do cabeçalho da ilha usando o modelo de 3 slots com crossfade."""
        if not hasattr(self, "relogio") or not self._pronto:
            return

        # 1. Indicadores de privacidade e status do relógio
        if hasattr(self, "pin_icone"):
            self.pin_icone.set_visible(bool(self._fixado))
        if hasattr(self, "dnd_icone"):
            self.dnd_icone.set_visible(bool(self._dnd_ativo))
        if hasattr(self, "mic_indicador"):
            self.mic_indicador.set_visible(bool(getattr(self, "_mic_ativo", False)))
        if hasattr(self, "rec_indicador"):
            self.rec_indicador.set_visible(bool(getattr(self, "_rec_ativo", False)))

        # Estado 1: HUD de Volume ativo
        if getattr(self, "_hud_ativo", False):
            self.hud_box.set_visible(True)
            if hasattr(self, "slot_leading"):
                self.slot_leading.set_visible_child_name("vazio")
                self.slot_center.set_visible_child_name("hud")
                self.slot_trailing.set_visible_child_name("vazio")
            if hasattr(self, "pilula_progresso"):
                self.pilula_progresso.set_visible(False)
            return

        # Estado 2: HUD de Notificação ativo
        if getattr(self, "_notif_ativa", False):
            self.notif_box.set_visible(True)
            if hasattr(self, "slot_leading"):
                self.slot_leading.set_visible_child_name("vazio")
                self.slot_center.set_visible_child_name("notif")
                self.slot_trailing.set_visible_child_name("vazio")
            if hasattr(self, "pilula_progresso"):
                self.pilula_progresso.set_visible(False)
            return

        # Estado 3: Normal / Mídia / Atividades / Repouso
        tem = bool(self._tem_player)
        tem_ativ = bool(getattr(self, "_atividades", None))
        tocando = (getattr(self, "_status", "Stopped") == "Playing")
        pausado = (getattr(self, "_status", "Stopped") == "Paused")

        if tem_ativ:
            ativ = list(self._atividades.values())[-1]
            icone = ativ.get("icon", "󰔟")
            texto = ativ.get("text", "")
            prog = ativ.get("progress")

            if hasattr(self, "anel_progresso"):
                self.anel_progresso.definir_progresso(prog)

            if tem:
                # Mídia à esquerda e centro, badge da atividade à direita
                if getattr(self, "_capa_ok", False):
                    self.capa.set_visible(True)
                    self.slot_leading.set_visible_child_name("capa")
                else:
                    self.slot_leading.set_visible_child_name("dots")
                self.slot_center.set_visible_child_name("titulo")
                if hasattr(self, "ativ_badge"):
                    self.ativ_badge.set_text(f"{icone} {texto[:10]}")
                    self.ativ_badge.set_visible(True)
                self.slot_trailing.set_visible_child_name("atividade")
                prog_visivel = bool(not self._expandido and not getattr(self, "_hud_ativo", False) and not getattr(self, "_notif_ativa", False))
                if hasattr(self, "pilula_progresso"):
                    self.pilula_progresso.set_visible(prog_visivel)
            else:
                # Sem mídia: atividade toma o slot leading
                self.ativ_box.set_visible(True)
                self.ativ_icone.set_text(icone)
                self.ativ_texto.set_text(texto)
                self.slot_leading.set_visible_child_name("atividade")
                self.slot_center.set_visible_child_name("vazio")
                self.slot_trailing.set_visible_child_name("relogio")
                if prog is not None and hasattr(self, "pilula_progresso"):
                    try:
                        self.pilula_progresso.set_fraction(max(0.0, min(1.0, float(prog))))
                        self.pilula_progresso.set_visible(not self._expandido)
                    except Exception:
                        self.pilula_progresso.set_visible(False)
                elif hasattr(self, "pilula_progresso"):
                    self.pilula_progresso.set_visible(False)
        else:
            if hasattr(self, "anel_progresso"):
                self.anel_progresso.definir_progresso(None)

            if tem:
                if getattr(self, "_capa_ok", False):
                    self.capa.set_visible(True)
                    self.slot_leading.set_visible_child_name("capa")
                else:
                    self.slot_leading.set_visible_child_name("dots")
                self.slot_center.set_visible_child_name("titulo")
                if hasattr(self, "eq_area"):
                    self.eq_area.set_visible(bool(tocando or pausado))
                self.slot_trailing.set_visible_child_name("relogio")

                prog_visivel = bool(not self._expandido and not getattr(self, "_hud_ativo", False) and not getattr(self, "_notif_ativa", False))
                if hasattr(self, "pilula_progresso"):
                    self.pilula_progresso.set_visible(prog_visivel)
            else:
                # Repouso total
                self.slot_leading.set_visible_child_name("dots")
                self.slot_center.set_visible_child_name("vazio")
                if hasattr(self, "eq_area"):
                    self.eq_area.set_visible(False)
                self.slot_trailing.set_visible_child_name("relogio")
                if hasattr(self, "pilula_progresso"):
                    self.pilula_progresso.set_visible(False)

    def _aplicar_cor_dinamica(self, cor: tuple[int, int, int] | None) -> None:
        """Aplica a cor dominante extraída da capa aos acentos da ilha."""
        if cor is None:
            cor = (203, 166, 247)  # Catppuccin mauve padrão
        if getattr(self, "_cor_dinamica_atual", None) == cor:
            return

        # Se houver animação ativa em andamento, posterga para não engasgar o frame rate
        if getattr(self, "_tick_id", 0) != 0:
            self._cor_dinamica_pendente = cor
            return

        self._cor_dinamica_atual = cor
        self._cor_dinamica_pendente = None
        r, g, b = cor
        hex_cor = f"#{r:02x}{g:02x}{b:02x}"
        css = f"""
        #ilha.tocando {{
            box-shadow: 0 6px 24px rgba({r}, {g}, {b}, 0.28);
            border-color: rgba({r}, {g}, {b}, 0.45);
        }}
        #hero-card.tocando {{
            border-color: rgba({r}, {g}, {b}, 0.35);
        }}
        #pilula-progresso > trough > progress {{
            background-color: {hex_cor};
        }}
        progressbar.dinamico > trough > progress {{
            background-color: {hex_cor};
        }}
        button.controle.play-btn.dinamico {{
            background-color: {hex_cor};
            color: #11111b;
        }}
        button.controle.play-btn.dinamico:hover {{
            background-color: rgba({r}, {g}, {b}, 0.85);
        }}
        #eq.dinamico {{
            color: {hex_cor};
        }}
        """
        if not hasattr(self, "_prov_dinamico"):
            self._prov_dinamico = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                self.win.get_display(), self._prov_dinamico,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2
            )
        self._prov_dinamico.load_from_data(css)
        if hasattr(self, "eq_area"):
            self.eq_area.definir_cor(r, g, b)
        if hasattr(self, "anel_progresso"):
            self.anel_progresso.definir_cor(r, g, b)

    def _aplicar_conteudo(self) -> None:
        """Reajusta visibilidade dos elementos da ilha de acordo com o estado do player."""
        if getattr(self, "_hud_ativo", False) or getattr(self, "_notif_ativa", False):
            if getattr(self, "_notif_ativa", False) and self._expandido:
                if hasattr(self, "stack"):
                    self.stack.set_visible_child_name("notificacao")
                if hasattr(self, "page_dots_box"):
                    self.page_dots_box.set_visible(False)
            self._render_cabecalho()
            self._animar_tamanho(self._largura_alvo(), self._altura_alvo(self._expandido))
            return

        tem = bool(self._tem_player)
        if hasattr(self, "hero_media_active_box"):
            self.hero_media_active_box.set_visible(tem)
        if hasattr(self, "hero_media_empty_box"):
            self.hero_media_empty_box.set_visible(not tem)

        self._render_cabecalho()
        self._animar_tamanho(self._largura_alvo(), self._altura_alvo(self._expandido))
        log(f"conteúdo: tem_player={tem} alvo={self._largura_alvo()}x"
            f"{self._altura_alvo(self._expandido)}")

    def _testar_controle(self) -> bool:
        """Dispara o sinal do botão, para testar botão -> MPRIS sem mouse."""
        print("TESTE: emitindo clicked no play/pause", flush=True)
        self.b_play.emit("clicked")
        return False

    def _dump(self) -> bool:
        """Diagnóstico: tamanhos mínimos/naturais de cada peça."""
        alvos = [("capa", self.capa), ("titulo", self.titulo),
                 ("relogio", self.relogio), ("hero_card", self.hero_card),
                 ("sistema", self.sistema_box), ("acoes", self.acoes_box),
                 ("revelador", self.revelador)]
        for nome, w in alvos:
            mn, nat = w.get_preferred_size()
            print(f"  {nome:10} min={mn.width}x{mn.height} "
                  f"nat={nat.width}x{nat.height}", flush=True)
        print(f"  JANELA {self.win.get_width()}x{self.win.get_height()}", flush=True)
        return False

    def _icone(self, nome: str) -> Gtk.Image:
        # pixel_size fixo: sem isso os glifos do tema (Qogir) saem com
        # proporções diferentes entre si — o pause ficava maior que as setas.
        img = Gtk.Image.new_from_icon_name(nome)
        img.set_pixel_size(15)
        return img

    def _botao(self, icone: str, comando: str) -> Gtk.Button:
        b = Gtk.Button()
        b.set_child(self._icone(icone))
        b.add_css_class("controle")
        b.connect("clicked", lambda *_: self.mpris.comando(comando))
        return b

    # -- interação ----------------------------------------------------------
    def _ao_clicar(self, *_) -> None:
        if getattr(self, "_arrastou", False):
            self._arrastou = False
            return
        if getattr(self, "_notif_ativa", False):
            if not self._expandido:
                self._expandir_notificacao_detalhe()
            else:
                self._esconder_hud_notif()
            return
        self._fixado = not self._fixado
        log(f"fixado={self._fixado}")
        if self._fixado:
            self.pilula.add_css_class("fixado")
            self.expandir(True)
        else:
            self.pilula.remove_css_class("fixado")
            if not self._dentro():
                self.expandir(False)
        self._render_cabecalho()

    def _dentro(self) -> bool:
        """O cursor está sobre a ilha? (geometria, não evento)"""
        pos = cursor_pos()
        if pos is None:
            return False
        r = self._rect_ilha()
        if r is None:
            return False
        x, y, w, h = r
        return x <= pos[0] < x + w and y <= pos[1] < y + h

    def _rect_ilha(self):
        """(x, y, largura, altura) global da ilha calculados deterministicamente."""
        if getattr(self, "_mon_geom", None) is None:
            alvo = monitor_por_nome(MONITOR)
            if alvo is not None:
                g = alvo.get_geometry()
                self._mon_geom = (g.x, g.y, g.width, g.height)
            else:
                self._mon_geom = (0, 0, 1920, 1080)

        mx, my, mw, _mh = self._mon_geom
        mola_w = getattr(self, "_mola_w", None)
        mola_h = getattr(self, "_mola_h", None)
        w = round(mola_w.x) if mola_w is not None else (self.win.get_width() or (LARGURA if self._tem_player else LARGURA_VAZIA))
        h = round(mola_h.x) if mola_h is not None else (self.win.get_height() or (self._h_colapsada or ALTURA_CABECALHO))
        x = mx + (mw - w) // 2
        y = my + MARGEM_TOPO
        return x, y, w, h

    def _tick_hover(self) -> bool:
        if getattr(self, "_fullscreen", False):
            return True
        dentro = self._dentro()
        agora = time.monotonic()
        if dentro:
            self._fora_desde = 0.0
            if getattr(self, "_notif_ativa", False):
                # Cursor sobre a notificação: pausa auto-dismiss para não sumir enquanto lê/arrasta
                if getattr(self, "_notif_timer", 0):
                    GLib.source_remove(self._notif_timer)
                    self._notif_timer = 0
            elif not self._expandido:
                self.expandir(True)
        elif self._expandido and not self._fixado:
            if not self._fora_desde:
                self._fora_desde = agora
            elif agora - self._fora_desde > 0.45:
                self._fora_desde = 0.0
                if getattr(self, "_notif_ativa", False):
                    self._esconder_hud_notif()
                else:
                    self.expandir(False)
        elif getattr(self, "_notif_ativa", False) and not self._expandido:
            # Cursor fora e notificação recolhida: se o timer pausou, reinicia auto-dismiss suave
            if not getattr(self, "_notif_timer", 0) and not getattr(self, "_notif_arrastando", False):
                self._notif_timer = GLib.timeout_add(1800, self._esconder_hud_notif)
        return True

    def expandir(self, sim: bool) -> None:
        if sim:
            if getattr(self, "_hud_ativo", False):
                if self._hud_timer:
                    GLib.source_remove(self._hud_timer)
                    self._hud_timer = 0
                self._hud_ativo = False
                self.hud_box.set_visible(False)
            if getattr(self, "_notif_ativa", False):
                if getattr(self, "_notif_timer", 0):
                    GLib.source_remove(self._notif_timer)
                    self._notif_timer = 0
                if hasattr(self, "stack"):
                    self.stack.set_visible_child_name("notificacao")
                if hasattr(self, "page_dots_box"):
                    self.page_dots_box.set_visible(False)
            else:
                tem = bool(self._tem_player)
                if hasattr(self, "hero_media_active_box"):
                    self.hero_media_active_box.set_visible(tem)
                if hasattr(self, "hero_media_empty_box"):
                    self.hero_media_empty_box.set_visible(not tem)
                if hasattr(self, "stack"):
                    inicial = "midia" if tem else "produtividade"
                    self._mudar_pagina(inicial)
                if hasattr(self, "page_dots_box"):
                    self.page_dots_box.set_visible(True)
        else:
            if getattr(self, "_notif_ativa", False) and not self._fixado:
                self._notif_ativa = False
                self._notif_expandida = False
                if getattr(self, "_notif_timer", 0):
                    GLib.source_remove(self._notif_timer)
                    self._notif_timer = 0
                if hasattr(self, "page_dots_box"):
                    self.page_dots_box.set_visible(True)

        self._render_cabecalho()
        if sim == self._expandido:
            return
        self._expandido = sim
        if sim:
            self.pilula.add_css_class("expandido")
        else:
            self.pilula.remove_css_class("expandido")
        duracao = 320 if sim else 280
        if hasattr(self, "revelador"):
            self.revelador.set_transition_duration(duracao)
            if sim:
                self.revelador.set_visible(True)
            self.revelador.set_reveal_child(sim)
        if sim:
            # Adia os ticks pesados para ~100ms após o início da animação,
            # garantindo que o primeiro frame da expansão rode a 144Hz liso.
            if not getattr(self, "_notif_ativa", False):
                GLib.timeout_add(100, self._aquecer_dados_expandir)
        # A surface layer-shell só CRESCE sozinha: quem manda no tamanho é o
        # set_default_size, animado a cada frame de VSync com curva fluida de mola.
        self._animar_tamanho(self._largura_alvo(sim), self._altura_alvo(sim), ms=duracao)
        pos = cursor_pos()
        log(f"expandido={sim} cursor={pos} alvo={self._altura_alvo(sim)}")

    def _altura_alvo(self, sim: bool) -> int:
        base = self._h_colapsada if self._h_colapsada is not None else ALTURA_CABECALHO
        if not sim:
            return base
        # altura natural do painel + o spacing da pílula (4)
        return base + self.painel.get_preferred_size()[1].height + 4

    def _aquecer_dados_expandir(self) -> bool:
        if self._expandido and not getattr(self, "_notif_ativa", False):
            self._tick_sistema()
            self._tick_posicao()
            self.rede.atualizar_async(self._mostrar_sistema)
        return GLib.SOURCE_REMOVE

    def _coreografar_progresso(self, progresso: float) -> None:
        """Sincroniza opacidade e revelação de conteúdo a partir de um único progresso físico (0.0 a 1.0).

        Coreografia Apple:
        - Na expansão:
          * Compacto (cabeçalho) desvanece nos primeiros 25% (0.00 -> 0.25).
          * Cartão hero entra a partir de 22% até 85%.
          * Linhas internas defasadas:
            - info (hero_topo / hero_activity_banner): 30% -> 70%
            - progresso (linha_progresso): 38% -> 78%
            - controles (b_play, skip, shuffle): 46% -> 86%
            - page_dots_box: 75% -> 95%
        - No recolhimento: ordem inversa acelerada para saída limpa sem sobreposição.
        """
        p = max(0.0, min(1.0, progresso))
        expandindo = bool(self._expandido)

        # 1. Cabeçalho compacto (permanece visível no topo para clique de recolher e status contínuo)
        if hasattr(self, "cabecalho"):
            self.cabecalho.set_opacity(1.0)
            self.cabecalho.set_can_target(True)

        # 2. Visibilidade do revelador durante a transição
        if hasattr(self, "revelador"):
            if p > 0.02 and not self.revelador.get_visible():
                self.revelador.set_visible(True)
            elif p <= 0.01 and not expandindo and self.revelador.get_visible():
                self.revelador.set_visible(False)

        # 3. Cartão hero e camadas escalonadas
        if hasattr(self, "hero_card"):
            if expandindo:
                op_hero = max(0.0, min(1.0, (p - 0.08) / 0.75))
                op_topo = max(0.0, min(1.0, (p - 0.16) / 0.50))
                op_prog = max(0.0, min(1.0, (p - 0.24) / 0.50))
                op_ctrl = max(0.0, min(1.0, (p - 0.32) / 0.50))
                op_dots = max(0.0, min(1.0, (p - 0.50) / 0.35))
            else:
                # Saída invertida mais rápida
                op_hero = max(0.0, min(1.0, (p - 0.08) / 0.70))
                op_dots = max(0.0, min(1.0, (p - 0.55) / 0.25))
                op_ctrl = max(0.0, min(1.0, (p - 0.38) / 0.30))
                op_prog = max(0.0, min(1.0, (p - 0.22) / 0.30))
                op_topo = max(0.0, min(1.0, (p - 0.10) / 0.35))

            self.hero_card.set_opacity(op_hero)

            if hasattr(self, "hero_topo"):
                self.hero_topo.set_opacity(op_topo)
            if hasattr(self, "hero_activity_banner"):
                self.hero_activity_banner.set_opacity(op_topo)
            if hasattr(self, "linha_progresso"):
                self.linha_progresso.set_opacity(op_prog)
            if hasattr(self, "controles"):
                self.controles.set_opacity(op_ctrl)
            if hasattr(self, "page_dots_box"):
                self.page_dots_box.set_opacity(op_dots)

    def _animar_tamanho(self, largura: int, altura: int, ms: int | None = None,
                        resposta: float | None = None, amortecimento: float | None = None) -> None:
        """Anima largura e altura usando molas físicas interrompíveis sincronizadas ao frame clock (144Hz).

        Preserva velocidade e posição atual, eliminando trancos em interrupções e reversões.
        """
        if getattr(self, "_anim_src", 0):
            GLib.source_remove(self._anim_src)
            self._anim_src = 0

        # Escolhe resposta e amortecimento de acordo com a transição
        if resposta is None or amortecimento is None:
            delta_h = altura - self._mola_h.x
            if delta_h > 15:
                # Expandir (0.42s resposta, 0.75 amortecimento -> 2.4% overshoot)
                resp_padrao, amort_padrao = 0.42, 0.75
            elif delta_h < -15:
                # Recolher (0.32s resposta, 0.92 amortecimento -> crítico/sem overshoot)
                resp_padrao, amort_padrao = 0.32, 0.92
            elif ms is not None and ms <= 180:
                # Pulso curto / HUD
                resp_padrao, amort_padrao = 0.25, 0.80
            else:
                # Troca de página / atividade
                resp_padrao, amort_padrao = 0.30, 0.85

            resposta = resposta if resposta is not None else resp_padrao
            amortecimento = amortecimento if amortecimento is not None else amort_padrao

        self._mola_w.reconfigurar(resposta, amortecimento)
        self._mola_h.reconfigurar(resposta, amortecimento)

        # Se já está no alvo com velocidade praticamente nula, nada a fazer
        if round(self._mola_w.alvo) == largura and round(self._mola_h.alvo) == altura:
            if (abs(self._mola_w.x - largura) < 0.25 and abs(self._mola_h.x - altura) < 0.25 and
                    abs(self._mola_w.v) < 1.5 and abs(self._mola_h.v) < 1.5):
                return

        self._mola_w.ir_para(largura)
        self._mola_h.ir_para(altura)
        self._canto = None

        if not getattr(self, "_tick_id", 0):
            self._ult_frame_time = None
            try:
                self._tick_id = self.win.add_tick_callback(self._passo_mola)
            except Exception:
                self._tick_id = 0
                self._anim_src = GLib.timeout_add(8, lambda: self._passo_mola(self.win, None))

    def _passo_mola(self, _widget, clock) -> bool:
        if clock is not None:
            t = clock.get_frame_time() / 1e6
        else:
            t = time.monotonic()

        if self._ult_frame_time is None:
            dt = 0.0
        else:
            dt = t - self._ult_frame_time
        self._ult_frame_time = t

        # Medição de quadros lentos durante a animação (Item 7)
        if dt > 0 and getattr(self, "_taxa_hz", 0) > 0:
            limite = 1.5 / self._taxa_hz
            if dt > limite:
                log(f"[PERF] frame lento: {dt * 1000.0:.1f} ms (esperado <= {limite * 1000.0:.1f} ms @ {self._taxa_hz:.0f}Hz)")

        ok_w = self._mola_w.passo(dt)
        ok_h = self._mola_h.passo(dt)
        ok = ok_w and ok_h

        w = round(self._mola_w.x)
        h = round(self._mola_h.x)

        if w != getattr(self, "_ultimo_w", None) or h != getattr(self, "_ultimo_h", None):
            self.pilula.set_size_request(w, h)
            self.win.set_default_size(w, h)
            self._ultimo_w = w
            self._ultimo_h = h

        h_col = self._h_colapsada if self._h_colapsada is not None else ALTURA_CABECALHO
        h_exp = getattr(self, "_h_expandida_alvo", None) or self._altura_alvo(True)
        progresso = max(0.0, min(1.0, (self._mola_h.x - h_col) / max(1.0, h_exp - h_col)))
        self._coreografar_progresso(progresso)

        if ok:
            w_final = round(self._mola_w.alvo)
            h_final = round(self._mola_h.alvo)
            self.pilula.set_size_request(w_final, h_final)
            self.win.set_default_size(w_final, h_final)
            self._ultimo_w = w_final
            self._ultimo_h = h_final

            if not self._expandido:
                self.revelador.set_visible(False)
                if hasattr(self, "cabecalho"):
                    self.cabecalho.set_opacity(1.0)
                    self.cabecalho.set_can_target(True)
                if hasattr(self, "hero_card"):
                    self.hero_card.set_opacity(0.0)
            else:
                self.revelador.set_visible(True)
                if hasattr(self, "cabecalho"):
                    self.cabecalho.set_opacity(1.0)
                    self.cabecalho.set_can_target(True)
                if hasattr(self, "hero_card"):
                    self.hero_card.set_opacity(1.0)
                for w_nome in ("hero_topo", "hero_activity_banner", "linha_progresso", "controles", "page_dots_box"):
                    w_obj = getattr(self, w_nome, None)
                    if w_obj is not None:
                        w_obj.set_opacity(1.0)

            if getattr(self, "_cor_dinamica_pendente", None) is not None:
                pendente = self._cor_dinamica_pendente
                self._cor_dinamica_pendente = None
                self._aplicar_cor_dinamica(pendente)

            self._tick_id = 0
            self._ult_frame_time = None
            return GLib.SOURCE_REMOVE

        return GLib.SOURCE_CONTINUE

    # -- atualização --------------------------------------------------------
    def atualizar(self) -> None:
        mpris = getattr(self, "mpris", None)
        d = mpris.dados() if mpris is not None else None
        self.relogio.set_text(time.strftime("%H:%M"))

        tem = bool(d and (d.get("titulo") or d.get("art")))
        if tem != self._tem_player:
            self._tem_player = tem
            if self._pronto:
                self._aplicar_conteudo()

        total_players = self.mpris.total_players() if self.mpris else 0
        if hasattr(self, "b_player"):
            self.b_player.set_visible(total_players > 1)

        if not tem:
            self._status = "Stopped"
            self._capa_ok = False
            self._art_uri = "\0"  # Correção Bug 1: permite recarregar a mesma arte quando o player voltar
            self._ultimo_estado_mpris = None
            self.pilula.remove_css_class("tocando")
            if hasattr(self, "hero_card"):
                self.hero_card.remove_css_class("tocando")
            self._aplicar_cor_dinamica(None)
            self._sincronizar_eq()
            if hasattr(self, "hero_capa"):
                self.hero_capa.set_from_icon_name("audio-x-generic-symbolic")
            if hasattr(self, "hero_titulo"):
                self.hero_titulo.set_text("Nenhuma mídia")
            self.artista.set_text("nada tocando")
            if hasattr(self, "hero_album"):
                self.hero_album.set_text("")
            if hasattr(self, "tempo_atual"):
                self.tempo_atual.set_text("0:00")
            if hasattr(self, "tempo_total"):
                self.tempo_total.set_text("0:00")
            self.barra.set_fraction(0)
            self.b_play.remove_css_class("dinamico")
            self.barra.remove_css_class("dinamico")
            self.eq_lbl.remove_css_class("dinamico")
            for b in (self.b_ant, self.b_play, self.b_prox):
                b.set_sensitive(False)
            if hasattr(self, "b_shuffle"):
                self.b_shuffle.set_sensitive(False)
                self.b_shuffle.remove_css_class("ativo")
            if hasattr(self, "b_repeat"):
                self.b_repeat.set_sensitive(False)
                self.b_repeat.remove_css_class("ativo")
            if hasattr(self, "hero_media_active_box"):
                self.hero_media_active_box.set_visible(False)
            if hasattr(self, "hero_media_empty_box"):
                self.hero_media_empty_box.set_visible(True)
            self._render_cabecalho()
            return

        # Estado chave para evitar retrabalho visual quando nada relevante mudou (Bug 11)
        estado_chave = (
            d.get("player"),
            d.get("status"),
            d.get("titulo"),
            d.get("artista"),
            d.get("album"),
            d.get("art"),
            d.get("duracao"),
            d.get("pode_ant"),
            d.get("pode_prox"),
            d.get("pode_play"),
            d.get("pode_seek"),
            d.get("shuffle"),
            d.get("loop"),
        )
        if estado_chave == getattr(self, "_ultimo_estado_mpris", None):
            return
        self._ultimo_estado_mpris = estado_chave

        if hasattr(self, "hero_media_active_box"):
            self.hero_media_active_box.set_visible(True)
        if hasattr(self, "hero_media_empty_box"):
            self.hero_media_empty_box.set_visible(False)

        titulo_ant = getattr(self, "_faixa_anterior_titulo", None)
        novo_titulo = d.get("titulo", "")
        self._status = d.get("status", "Stopped")
        self.titulo.set_text(novo_titulo)
        if hasattr(self, "hero_titulo"):
            self.hero_titulo.set_text(novo_titulo)
        self.artista.set_text(d.get("artista") or novo_titulo)

        # Transição na troca de faixa: pulso suave na pílula colapsada (Bug 16)
        tocando = (self._status == "Playing")
        if titulo_ant is not None and novo_titulo != titulo_ant and tocando and not self._expandido:
            self._pulsar_troca_faixa()
        self._faixa_anterior_titulo = novo_titulo

        album = d.get("album", "")
        player = (d.get("player", "") or "").lower()
        ident = self.mpris.identidade(novo_titulo) if self.mpris else ""
        if hasattr(self, "hero_album"):
            if "spotify" in player:
                icone_player = "󰓇"
            elif "kdeconnect" in player:
                icone_player = "󰄜"
            elif any(b in player for b in ("firefox", "chrome", "chromium", "brave", "zen")):
                icone_player = "󰈹"
            else:
                icone_player = "󰎆"

            subtexto = album or ident or player.split(".")[-1].capitalize()
            if subtexto:
                self.hero_album.set_markup(f"<span>{icone_player}</span>  {GLib.markup_escape_text(subtexto)}")
            else:
                self.hero_album.set_text("")

        # Todos os players utilizam cores dinâmicas sem forçar estilos fixos (Bug 9 e 17)
        self.b_play.remove_css_class("spotify")
        self.barra.remove_css_class("spotify")
        self.b_play.add_css_class("dinamico")
        self.barra.add_css_class("dinamico")
        self.eq_lbl.add_css_class("dinamico")

        # Tratamento de duração e faixa ao vivo (Bug 5)
        duracao = d.get("duracao", 0)
        pode_seek = bool(d.get("pode_seek", False) and duracao > 0)
        if duracao > 0:
            if hasattr(self, "tempo_total"):
                self.tempo_total.set_text(mmss(duracao))
            self.barra.set_cursor_from_name("pointer" if pode_seek else "default")
            self.barra.set_sensitive(pode_seek)
        else:
            if hasattr(self, "tempo_total"):
                self.tempo_total.set_text("Ao vivo")
            if hasattr(self, "tempo_atual"):
                pos = self.mpris.posicao_estimada() if self.mpris else 0
                self.tempo_atual.set_text(mmss(pos) if pos > 0 else "")
            self.barra.set_fraction(0.0)
            self.barra.set_cursor_from_name("default")
            self.barra.set_sensitive(False)

        # Ícone e estado de reprodução
        ico_play = "media-playback-pause-symbolic" if tocando else "media-playback-start-symbolic"
        if getattr(self, "_play_icone_nome", None) != ico_play:
            self._play_icone_nome = ico_play
            self.b_play.set_child(self._icone(ico_play))

        self.b_ant.set_sensitive(d.get("pode_ant", False))
        self.b_prox.set_sensitive(d.get("pode_prox", False))
        self.b_play.set_sensitive(d.get("pode_play", True))

        # Shuffle e Repeat (Bug 14)
        if hasattr(self, "b_shuffle"):
            self.b_shuffle.set_sensitive(self.mpris is not None and self.mpris.proxy is not None)
            if d.get("shuffle", False):
                self.b_shuffle.add_css_class("ativo")
            else:
                self.b_shuffle.remove_css_class("ativo")

        if hasattr(self, "b_repeat"):
            self.b_repeat.set_sensitive(self.mpris is not None and self.mpris.proxy is not None)
            loop_st = d.get("loop", "None")
            if loop_st != "None":
                self.b_repeat.add_css_class("ativo")
                if loop_st == "Track":
                    self.b_repeat.set_tooltip_text("Repetição: Faixa atual")
                else:
                    self.b_repeat.set_tooltip_text("Repetição: Lista/Playlist")
            else:
                self.b_repeat.remove_css_class("ativo")
                self.b_repeat.set_tooltip_text("Repetição desativada")

        if tocando:
            self.pilula.add_css_class("tocando")
            if hasattr(self, "hero_card"):
                self.hero_card.add_css_class("tocando")
        else:
            self.pilula.remove_css_class("tocando")
            if hasattr(self, "hero_card"):
                self.hero_card.remove_css_class("tocando")
            if d.get("status") == "Paused":
                self.eq_lbl.set_text(" ▂ ")

        self._sincronizar_eq()
        self._render_cabecalho()

        # Carregamento seguro de arte com proteção contra corrida (Bug 2)
        art_alvo = d.get("art", "")
        if art_alvo != self._art_uri:
            self._art_uri = art_alvo
            carregar_art(art_alvo, lambda tex, cor, u=art_alvo:
                         self._art_uri == u and self._pintar_capa(tex, cor, u))

    def _pintar_capa(self, textura, cor=None, uri_esperada: str = "") -> None:
        if uri_esperada and uri_esperada != self._art_uri:
            return
        if textura is None:
            self._capa_ok = False
            self._art_uri = "\0"  # Permite repetir o download se falhar temporariamente (Bug 2)
            self.capa.set_visible(False)
            if hasattr(self, "hero_capa"):
                self.hero_capa.set_from_icon_name("audio-x-generic-symbolic")
            self._aplicar_cor_dinamica(None)
        else:
            self._capa_ok = True
            self.capa.set_from_paintable(textura)
            if hasattr(self, "hero_capa"):
                self.hero_capa.set_from_paintable(textura)
            self._aplicar_cor_dinamica(cor)
        self._render_cabecalho()

    def _sincronizar_eq(self) -> None:
        status = getattr(self, "_status", "")
        tocando = (self._tem_player and status == "Playing"
                   and not getattr(self, "_mudo", False)
                   and not getattr(self, "_hud_ativo", False)
                   and not getattr(self, "_notif_ativa", False)
                   and not getattr(self, "_fullscreen", False))
        pausado = (self._tem_player and status == "Paused" and not getattr(self, "_mudo", False))
        mudo = bool(getattr(self, "_mudo", False))

        if hasattr(self, "eq_area"):
            self.eq_area.definir_estado(ativo=tocando, pausado=pausado, mudo=mudo)

        if tocando and not self._eq_timer:
            self._eq_timer = GLib.timeout_add(140, self._tick_eq)
        elif not tocando and self._eq_timer:
            GLib.source_remove(self._eq_timer)
            self._eq_timer = 0
            if pausado:
                self.eq_lbl.set_text(" ▂ ")
            elif mudo:
                self.eq_lbl.set_text("───")

    def _pulsar_troca_faixa(self) -> None:
        if self._expandido or getattr(self, "_hud_ativo", False) or getattr(self, "_notif_ativa", False):
            return
        largura_normal = self._largura_alvo(False)
        largura_pulso = min(LARGURA, largura_normal + 12)
        # Pop curto estilo Dynamic Island: resposta rápida e ligeiro bounce
        self._animar_tamanho(largura_pulso, self._altura_alvo(False), resposta=0.22, amortecimento=0.72)
        if getattr(self, "_faixa_pulso_timer", 0):
            GLib.source_remove(self._faixa_pulso_timer)
        self._faixa_pulso_timer = GLib.timeout_add(300, self._terminar_pulso_faixa)

    def _terminar_pulso_faixa(self) -> bool:
        self._faixa_pulso_timer = 0
        if not self._expandido and not getattr(self, "_hud_ativo", False) and not getattr(self, "_notif_ativa", False):
            # Retorna suavemente com a mola assentando no tamanho normal
            self._animar_tamanho(self._largura_alvo(False), self._altura_alvo(False), resposta=0.32, amortecimento=0.90)
        return False

    def _ao_clicar_focar_player(self) -> None:
        d = self.mpris.dados() if self.mpris else None
        player = (d.get("player", "") if d else "").lower()
        log(f"Focando reprodutor: {player}")
        de = self.mpris.desktop_entry() if self.mpris else ""
        if "spotify" in player or de.lower() == "spotify":
            subprocess.Popen(["hyprctl", "dispatch", "focuswindow", "class:Spotify"],
                             env=self.hypr._ambiente)
        elif de:
            subprocess.Popen(["hyprctl", "dispatch", "focuswindow", f"class:(?i){de}"],
                             env=self.hypr._ambiente)
        elif player:
            cmd = player.split(".")[-1]
            if cmd.startswith("instance"):
                parts = player.split(".")
                for p in reversed(parts):
                    if not p.startswith("instance") and p not in ("MediaPlayer2", "org", "mpris"):
                        cmd = p
                        break
            subprocess.Popen(["hyprctl", "dispatch", "focuswindow", f"class:(?i){cmd}"],
                             env=self.hypr._ambiente)

    def _ao_scroll_player(self, _ctrl, _dx: float, dy: float) -> bool:
        if not self.mpris:
            return False
        log(f"Scroll no player: dy={dy}")
        delta = -0.05 if dy > 0 else 0.05
        novo_vol = self.mpris.ajustar_volume(delta)
        if novo_vol is not None:
            self._mostrar_hud_player_volume(novo_vol)
        return True

    def _mostrar_hud_player_volume(self, vol: float) -> None:
        if not self._pronto or self._expandido:
            return
        if getattr(self, "_notif_timer", 0):
            GLib.source_remove(self._notif_timer)
            self._notif_timer = 0
            self.notif_box.set_visible(False)
            self._notif_ativa = False

        self._hud_ativo = True
        self._render_cabecalho()

        pct = int(round(vol * 100))
        self.hud_icone.remove_css_class("mudo")
        if pct == 0:
            self.hud_icone.set_text("󰝟")
        elif pct < 50:
            self.hud_icone.set_text("󰕿")
        else:
            self.hud_icone.set_text("󰕾")

        self.hud_pct.set_text(f"{pct}%")

        alvo_frac = min(1.0, max(0.0, vol))
        self._mola_vol.ir_para(alvo_frac)
        if not getattr(self, "_tick_vol_id", 0):
            self._ult_t_vol = None
            try:
                self._tick_vol_id = self.win.add_tick_callback(self._passo_mola_vol)
            except Exception:
                self._tick_vol_id = 0
                self.hud_barra.set_fraction(alvo_frac)

        self._animar_tamanho(260, self._altura_alvo(False), resposta=0.25, amortecimento=0.82)

        if self._hud_timer:
            GLib.source_remove(self._hud_timer)
        self._hud_timer = GLib.timeout_add(1500, self._esconder_hud_volume)
        return True

    def _ao_scroll_sessoes(self, _ctrl, _dx: float, dy: float) -> bool:
        if not self._expandido:
            return False
        agora = time.monotonic()
        if agora - self._ultimo_scroll_sessao < 0.22:
            return True
        self._ultimo_scroll_sessao = agora

        ordem = ["midia", "produtividade", "sistema", "historico"]
        atual = self.stack.get_visible_child_name()
        if atual not in ordem:
            atual = "midia" if self._tem_player else "produtividade"
        idx = ordem.index(atual)

        if dy > 0:  # Scroll para baixo -> próxima sessão
            prox = ordem[(idx + 1) % len(ordem)]
        else:       # Scroll para cima -> sessão anterior
            prox = ordem[(idx - 1) % len(ordem)]

        self._mudar_pagina(prox)
        return True

    def _mudar_pagina(self, nome: str) -> None:
        if not hasattr(self, "stack"):
            return
        if nome == "historico":
            self._carregar_historico_notificacoes()
        self.stack.set_visible_child_name(nome)
        self._atualizar_indicador_pagina(nome)
        if self._expandido and not getattr(self, "_notif_ativa", False):
            self._animar_tamanho(self._largura_alvo(True), self._altura_alvo(True),
                                 resposta=0.30, amortecimento=0.85)

    def _ao_mudar_kde(self) -> None:
        if hasattr(self, "btn_phone") and self._expandido:
            self._mostrar_sistema()

    def _ao_clicar_limpar_historico(self, *_) -> None:
        self._historico_cache = []
        self._renderizar_itens_historico([])
        if self._expandido:
            self._animar_tamanho(self._largura_alvo(True), self._altura_alvo(True),
                                 resposta=0.30, amortecimento=0.85)

        def _worker():
            limpar_historico_notificacoes()

        threading.Thread(target=_worker, daemon=True).start()

    def _carregar_historico_notificacoes(self) -> None:
        if not hasattr(self, "box_lista_historico"):
            return

        # 1. Renderiza o cache instantaneamente (zero bloqueio da UI)
        if hasattr(self, "_historico_cache"):
            self._renderizar_itens_historico(self._historico_cache)

        # 2. Busca o histórico atualizado em thread de fundo
        if getattr(self, "_carregando_historico", False):
            return
        self._carregando_historico = True

        def _worker():
            itens = ler_historico_notificacoes(limite=4)

            def _aplicar():
                self._carregando_historico = False
                self._historico_cache = itens
                # Só re-renderiza se a página visível ainda for histórico
                if hasattr(self, "stack") and self.stack.get_visible_child_name() == "historico":
                    self._renderizar_itens_historico(itens)
                    if self._expandido and not getattr(self, "_notif_ativa", False):
                        self._animar_tamanho(self._largura_alvo(True), self._altura_alvo(True),
                                             resposta=0.30, amortecimento=0.85)

            GLib.idle_add(_aplicar)

        threading.Thread(target=_worker, daemon=True).start()

    def _renderizar_itens_historico(self, itens: list[dict]) -> None:
        if not hasattr(self, "box_lista_historico"):
            return
        while child := self.box_lista_historico.get_first_child():
            self.box_lista_historico.remove(child)

        if not itens:
            vazio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            vazio_box.set_halign(Gtk.Align.CENTER)
            vazio_box.set_valign(Gtk.Align.CENTER)
            vazio_box.add_css_class("notif-hist-empty-box")

            lbl_vazio_ico = Gtk.Label(label="󰂛")
            lbl_vazio_ico.add_css_class("notif-hist-empty-ico")
            lbl_vazio_txt = Gtk.Label(label="Nenhuma notificação no histórico")
            lbl_vazio_txt.add_css_class("notif-hist-empty-txt")

            vazio_box.append(lbl_vazio_ico)
            vazio_box.append(lbl_vazio_txt)
            self.box_lista_historico.append(vazio_box)
            if hasattr(self, "lbl_hist_count"):
                self.lbl_hist_count.set_text("0")
            return

        if hasattr(self, "lbl_hist_count"):
            self.lbl_hist_count.set_text(str(len(itens)))

        for notif in itens:
            item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            item_box.add_css_class("notif-hist-card")

            linha_topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            app_lbl = Gtk.Label(label=notif["app"])
            app_lbl.add_css_class("notif-hist-app")
            app_lbl.set_halign(Gtk.Align.START)

            linha_topo.append(app_lbl)

            titulo_lbl = Gtk.Label(label=notif["titulo"])
            titulo_lbl.add_css_class("notif-hist-title")
            titulo_lbl.set_halign(Gtk.Align.START)
            titulo_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            titulo_lbl.set_max_width_chars(36)

            corpo_lbl = Gtk.Label(label=notif["corpo"])
            corpo_lbl.add_css_class("notif-hist-body")
            corpo_lbl.set_halign(Gtk.Align.START)
            corpo_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            corpo_lbl.set_max_width_chars(38)

            item_box.append(linha_topo)
            item_box.append(titulo_lbl)
            if notif["corpo"].strip():
                item_box.append(corpo_lbl)

            self.box_lista_historico.append(item_box)

    def _atualizar_indicador_pagina(self, nome: str) -> None:
        if not hasattr(self, "_dots"):
            return
        for n, dot in self._dots.items():
            if n == nome:
                dot.add_css_class("ativo")
            else:
                dot.remove_css_class("ativo")

    def _ao_click_barra_vol(self, _gesture, _n, x: float, _y: float) -> None:
        largura = self.sys_barra_vol.get_width()
        if largura > 0:
            pct = int(max(0, min(100, round((x / largura) * 100))))
            self.volume.definir(pct)

    def _ao_drag_barra_vol(self, gesture, offset_x: float, _offset_y: float) -> None:
        ok, x0, _ = gesture.get_start_point()
        if ok:
            largura = self.sys_barra_vol.get_width()
            if largura > 0:
                pct = int(max(0, min(100, round(((x0 + offset_x) / largura) * 100))))
                self.volume.definir(pct)

    def _ao_abrir_spotify(self) -> None:
        subprocess.Popen(["hyprctl", "dispatch", "exec", "spotify"], env=self.hypr._ambiente)

    def _criar_ponto(self, ws_id: int):
        ponto = Gtk.Box()
        ponto.add_css_class("ws-dot")
        ponto.set_cursor_from_name("pointer")
        ponto.set_valign(Gtk.Align.CENTER)
        ponto.ws_id = ws_id
        g = Gtk.GestureClick()
        g.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        g.connect("released", lambda gesture, _n, _x, _y, p=ponto: self._ao_clicar_ws(gesture, getattr(p, "ws_id", 1)))
        ponto.add_controller(g)
        return ponto

    def _ao_clicar_ws(self, gesture, ws_id: int) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        subprocess.Popen(["hyprctl", "dispatch", "workspace", str(ws_id)],
                         env=self.hypr._ambiente)

    def _ao_drag_barra_inicio(self, _gesture, start_x: float, _start_y: float) -> None:
        d = self.mpris.dados() if self.mpris else None
        if not (d and d.get("pode_seek", False) and d.get("duracao", 0) > 0):
            return
        self._arrastando_barra = True
        self._atualizar_seek(start_x, commit=False)

    def _ao_drag_barra_atualizar(self, gesture, offset_x: float, _offset_y: float) -> None:
        if not getattr(self, "_arrastando_barra", False):
            return
        ok, x0, _ = gesture.get_start_point()
        if ok:
            self._atualizar_seek(x0 + offset_x, commit=False)

    def _ao_drag_barra_fim(self, gesture, offset_x: float, _offset_y: float) -> None:
        if not getattr(self, "_arrastando_barra", False):
            return
        ok, x0, _ = gesture.get_start_point()
        if ok:
            self._atualizar_seek(x0 + offset_x, commit=True)
        self._arrastando_barra = False

    def _atualizar_seek(self, x: float, commit: bool = False) -> None:
        d = self.mpris.dados() if self.mpris else None
        if not (d and d.get("pode_seek", False) and d.get("duracao", 0) > 0):
            return
        largura = self.barra.get_width()
        if largura <= 0:
            return
        fracao = max(0.0, min(1.0, x / largura))
        self.barra.set_fraction(fracao)
        alvo_us = int(fracao * d["duracao"])
        if hasattr(self, "tempo_atual"):
            self.tempo_atual.set_text(mmss(alvo_us))
        if hasattr(self, "tempo_total"):
            self.tempo_total.set_text(mmss(d["duracao"]))
        if commit:
            self.mpris.seek(alvo_us)

    def _ao_scroll(self, ctrl, _dx: float, dy: float) -> bool:
        if self._expandido:
            return self._ao_scroll_sessoes(ctrl, _dx, dy)
        if dy < 0:
            self.volume.ajustar(+2)
        elif dy > 0:
            self.volume.ajustar(-2)
        return True

    def _tick_relogio(self) -> bool:
        if getattr(self, "_fullscreen", False):
            return True
        agora_min = time.strftime("%H:%M")
        if agora_min != getattr(self, "_ultimo_minuto_relogio", None):
            self._ultimo_minuto_relogio = agora_min
            self.relogio.set_text(agora_min)
            data_str = time.strftime("%A, %d de %B").capitalize()
            if hasattr(self, "idle_data"):
                self.idle_data.set_markup(f"<span foreground='#cba6f7'>󰸗</span>  {data_str}")
            elif hasattr(self, "data"):
                self.data.set_text(data_str)

            hoje = datetime.date.today()
            if getattr(self, "_ultimo_dia_cal", None) != hoje and hasattr(self, "cal_box"):
                self._construir_mini_calendario()

            if hasattr(self, "idle_uptime"):
                self._atualizar_uptime()

        return True

    def _construir_mini_calendario(self) -> None:
        hoje = datetime.date.today()
        self._ultimo_dia_cal = hoje

        while child := self.cal_box.get_first_child():
            self.cal_box.remove(child)

        nomes_dias = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        inicio_semana = hoje - datetime.timedelta(days=hoje.weekday())

        for i in range(7):
            d = inicio_semana + datetime.timedelta(days=i)
            is_hoje = (d == hoje)

            dia_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            dia_box.add_css_class("cal-dia")
            if is_hoje:
                dia_box.add_css_class("dia-hoje")

            lbl_nome = Gtk.Label(label=nomes_dias[i])
            lbl_nome.add_css_class("dia-nome")

            lbl_num = Gtk.Label(label=str(d.day))
            lbl_num.add_css_class("dia-num")

            dia_box.append(lbl_nome)
            dia_box.append(lbl_num)
            self.cal_box.append(dia_box)

    def _atualizar_uptime(self) -> None:
        try:
            with open("/proc/uptime", "r") as f:
                segundos = float(f.readline().split()[0])
            horas = int(segundos // 3600)
            minutos = int((segundos % 3600) // 60)
            txt = f"{horas}h {minutos:02d}m up" if horas > 0 else f"{minutos}m up"
            self.idle_uptime.set_markup(f"<span foreground='#a6e3a1'>󱑂</span>  {txt}")
        except Exception:
            self.idle_uptime.set_text("")

    def _atualizar_clima_async(self, forcar: bool = False) -> None:
        agora = time.monotonic()
        if not forcar and (agora - getattr(self, "_clima_ts", 0.0) < 1800):
            return

        def worker():
            try:
                cidade = CONFIG["geral"].get("cidade", "Araraquara")
                cidade_encoded = urllib.parse.quote(cidade)
                req = urllib.request.Request(
                    f"https://wttr.in/{cidade_encoded}?format=%t;%C",
                    headers={"User-Agent": "curl/7.88.1"}
                )
                with urllib.request.urlopen(req, timeout=3.5) as resp:
                    raw = resp.read().decode("utf-8").strip()
                GLib.idle_add(self._aplicar_clima, raw)
            except Exception as e:
                log(f"Falha ao obter clima: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _aplicar_clima(self, raw: str) -> bool:
        self._clima_ts = time.monotonic()
        if not raw or ";" not in raw:
            return False
        partes = raw.split(";", 1)
        temp = partes[0].replace("+", "").strip()
        cond = partes[1].strip()
        c_low = cond.lower()

        if any(k in c_low for k in ("clear", "sunny", "limpo", "ensolarado")):
            icone, cond_pt = "󰖐", "Ensolarado" if "sun" in c_low else "Limpo"
        elif any(k in c_low for k in ("partly cloudy", "parcialmente")):
            icone, cond_pt = "󰖕", "Parcialmente nublado"
        elif any(k in c_low for k in ("overcast", "cloudy", "nublado")):
            icone, cond_pt = "󰖑", "Nublado"
        elif any(k in c_low for k in ("thunder", "tempestade", "trovoada")):
            icone, cond_pt = "󰖓", "Trovoada"
        elif any(k in c_low for k in ("rain", "drizzle", "shower", "chuva")):
            icone, cond_pt = "󰖖", "Chuva"
        elif any(k in c_low for k in ("snow", "neve")):
            icone, cond_pt = "󰖘", "Neve"
        elif any(k in c_low for k in ("fog", "mist", "neblina", "névoa")):
            icone, cond_pt = "󰖑", "Neblina"
        else:
            icone, cond_pt = "󰖐", cond

        self.idle_clima.set_markup(
            f"<span foreground='#fab387'>{icone}</span>  {GLib.markup_escape_text(f'{temp} {cond_pt}')}"
        )
        return False

    def _tick_posicao(self) -> bool:
        if not self._tem_player or getattr(self, "_arrastando_barra", False):
            return True
        if getattr(self, "_fullscreen", False):
            return True
        barra_visivel = hasattr(self, "pilula_progresso") and self.pilula_progresso.get_visible()
        if not self._expandido and not barra_visivel:
            return True

        d = self.mpris.dados()
        if d and d["status"] != "Stopped":
            pos = self.mpris.posicao_estimada()
            if d["duracao"] > 0:
                agora = time.monotonic()
                resync_int = float(CONFIG["mpris"].get("resync_intervalo_s", 5.0))
                if d["status"] == "Playing" and (agora - getattr(self.mpris, "_ultimo_sync_d_bus", 0.0) > resync_int):
                    self.mpris.sincronizar_posicao()

                if getattr(self, "_arrastando_barra", False):
                    return True
                frac = min(1.0, max(0.0, pos / d["duracao"]))
                if hasattr(self, "pilula_progresso") and self.pilula_progresso.get_visible():
                    self.pilula_progresso.set_fraction(frac)
                if self._expandido:
                    self.barra.set_fraction(frac)
                    if hasattr(self, "tempo_atual"):
                        self.tempo_atual.set_text(mmss(pos))
                    if hasattr(self, "tempo_total"):
                        self.tempo_total.set_text(mmss(d["duracao"]))
            else:
                # Transmissão ao vivo / sem duração conhecida (Bug 5)
                if hasattr(self, "pilula_progresso") and self.pilula_progresso.get_visible():
                    self.pilula_progresso.set_fraction(0.0)
                if self._expandido:
                    self.barra.set_fraction(0.0)
                    if hasattr(self, "tempo_atual"):
                        self.tempo_atual.set_text(mmss(pos) if pos > 0 else "")
                    if hasattr(self, "tempo_total"):
                        self.tempo_total.set_text("Ao vivo")
        return True

    def _tick_sistema(self) -> bool:
        if getattr(self, "_fullscreen", False):
            return True
        self._atualizar_estado_dnd(forcar_hud=False)
        self._checar_gravador()
        if not self._expandido:
            return True                    # recolhida: a linha de sistema está escondida
        self._cpu = self.sistema.cpu()
        self._mem = self.sistema.mem()
        self._mostrar_sistema()
        return True

    def _checar_gravador(self) -> None:
        """Detecta processos de gravação de tela (wf-recorder) em thread de background."""
        if getattr(self, "_checando_gravador", False):
            return
        self._checando_gravador = True

        def _worker():
            try:
                found = False
                for pid in os.listdir("/proc"):
                    if pid.isdigit():
                        try:
                            with open(f"/proc/{pid}/comm", "r") as f:
                                if f.read().strip() == "wf-recorder":
                                    found = True
                                    break
                        except Exception:
                            pass
                def _aplicar():
                    self._checando_gravador = False
                    if found != getattr(self, "_rec_ativo", False):
                        self._rec_ativo = found
                        if found:
                            self.adicionar_atividade(
                                "gravacao", icon="󰻃", text="Gravando tela",
                                subtext="wf-recorder ativo", color="#f38ba8"
                            )
                        else:
                            self.remover_atividade("gravacao")
                        self._render_cabecalho()
                    return False
                GLib.idle_add(_aplicar)
            except Exception:
                self._checando_gravador = False

        threading.Thread(target=_worker, daemon=True).start()

    def _ao_mudar_mic(self, ativo: bool) -> None:
        """Callback acionado pelo evento de captura de áudio do pactl."""
        self._mic_ativo = ativo
        self._render_cabecalho()

    def _ao_fullscreen(self, ativo: bool) -> None:
        """Oculta a ilha apenas em tela cheia real (jogos, vídeos F11), mantendo visível ao maximizar."""
        if not ativo:
            self._fullscreen = False
            self.win.set_visible(True)
            self._tick_relogio()
            self.atualizar()
            return

        self._verificar_e_aplicar_fullscreen()

    def _verificar_e_aplicar_fullscreen(self) -> bool:
        """Garante que janelas maximizadas (modo 1) não escondam a Dynamic Island de forma assíncrona."""
        def _fazer():
            try:
                w = json.loads(subprocess.run(
                    ["hyprctl", "activewindow", "-j"], capture_output=True,
                    text=True, timeout=0.3, env=self.hypr._ambiente).stdout)
                # fullscreen: 0 = normal, 1 = maximizado, 2 = tela cheia, 3 = ambos
                fs_mode = int(w.get("fullscreen", 0))

                mon_ilha_id = getattr(self.hypr, "monitor_id", None)
                if mon_ilha_id is None:
                    try:
                        mons = json.loads(subprocess.run(
                            ["hyprctl", "monitors", "-j"], capture_output=True,
                            text=True, timeout=0.3, env=self.hypr._ambiente).stdout)
                        for m in mons:
                            if m.get("name") == MONITOR:
                                mon_ilha_id = m.get("id")
                                self.hypr.monitor_id = mon_ilha_id
                                break
                    except Exception:
                        pass

                janela_mon = w.get("monitor")
                mesmo_monitor = (mon_ilha_id is None or janela_mon is None or janela_mon == mon_ilha_id)
                true_fs = (fs_mode >= 2 and mesmo_monitor)

                def _aplicar():
                    self._fullscreen = true_fs
                    if true_fs:
                        if self._expandido:
                            self.expandir(False)
                        self.win.set_visible(False)
                    else:
                        self.win.set_visible(True)
                    return False

                GLib.idle_add(_aplicar)
            except Exception as e:
                log(f"Erro ao verificar modo de tela cheia: {e}")
                GLib.idle_add(lambda: self.win.set_visible(True))

        threading.Thread(target=_fazer, daemon=True).start()
        return False

    # -----------------------------------------------------------------------
    # API de Live Activities (Atividades ao Vivo)
    # -----------------------------------------------------------------------
    def adicionar_atividade(self, id: str, icon: str = "󰔟", text: str = "",
                            subtext: str = "", progress: float | None = None,
                            color: str = "#cba6f7", ttl: int = 0) -> None:
        ant = self._atividades.get(id)
        if ant and ant.get("timer_id"):
            GLib.source_remove(ant["timer_id"])

        timer_id = 0
        if ttl > 0:
            timer_id = GLib.timeout_add_seconds(ttl, lambda: (self.remover_atividade(id), False)[1])

        self._atividades[id] = {
            "id": id,
            "icon": icon or "󰔟",
            "text": text,
            "subtext": subtext,
            "progress": progress,
            "color": color,
            "ttl": ttl,
            "timer_id": timer_id,
            "inicio": time.monotonic()
        }
        self._atualizar_banner_atividade()
        self._render_cabecalho()
        if not self._expandido:
            self._animar_tamanho(self._largura_alvo(False), self._altura_alvo(False), ms=200)

    def remover_atividade(self, id: str) -> None:
        ativ = self._atividades.pop(id, None)
        if ativ and ativ.get("timer_id"):
            GLib.source_remove(ativ["timer_id"])
        self._atualizar_banner_atividade()
        self._render_cabecalho()
        if not self._expandido:
            self._animar_tamanho(self._largura_alvo(False), self._altura_alvo(False), ms=200)

    def limpar_atividades(self) -> None:
        for ativ in list(self._atividades.values()):
            if ativ.get("timer_id"):
                GLib.source_remove(ativ["timer_id"])
        self._atividades.clear()
        self._atualizar_banner_atividade()
        self._render_cabecalho()
        if not self._expandido:
            self._animar_tamanho(self._largura_alvo(False), self._altura_alvo(False), ms=200)

    def _encerrar_atividade_ativa(self) -> None:
        if self._atividades:
            top_id = list(self._atividades.keys())[-1]
            if top_id == "gravacao":
                try:
                    subprocess.run(["pkill", "-SIGINT", "wf-recorder"], capture_output=True)
                except Exception:
                    pass
            self.remover_atividade(top_id)

    def _atualizar_banner_atividade(self) -> None:
        if not hasattr(self, "hero_activity_banner"):
            return
        if not self._atividades:
            self.hero_activity_banner.set_visible(False)
            return
        ativ = list(self._atividades.values())[-1]
        self.hero_activity_icon.set_text(ativ.get("icon", "󰔟"))
        self.hero_activity_title.set_text(ativ.get("text", "Atividade"))
        sub = ativ.get("subtext", "")
        self.hero_activity_subtext.set_text(sub)
        self.hero_activity_subtext.set_visible(bool(sub))
        prog = ativ.get("progress")
        if prog is not None:
            try:
                frac = max(0.0, min(1.0, float(prog)))
                self.hero_activity_progresso.set_fraction(frac)
                self.hero_activity_progresso.set_visible(True)
            except Exception:
                self.hero_activity_progresso.set_visible(False)
        else:
            self.hero_activity_progresso.set_visible(False)
        self.hero_activity_banner.set_visible(True)

    def _tick_eq(self) -> bool:
        if not self._tem_player or getattr(self, "_hud_ativo", False) or getattr(self, "_notif_ativa", False):
            self._eq_timer = 0
            return False
        if getattr(self, "_fullscreen", False) or getattr(self, "_mudo", False):
            self._eq_timer = 0
            if getattr(self, "_mudo", False):
                self.eq_lbl.set_text("───")
            return False
        st = getattr(self.mpris, "_status", None)
        if st is None and self.mpris:
            d = self.mpris.dados()
            st = d.get("status") if d else None
        if st != "Playing":
            self._eq_timer = 0
            if st == "Paused":
                self.eq_lbl.set_text(" ▂ ")
            return False
        self._eq_idx = (self._eq_idx + 1) % len(EQ_FRAMES)
        self.eq_lbl.set_text(EQ_FRAMES[self._eq_idx])
        return True

    def _passo_mola_vol(self, _widget, clock) -> bool:
        """Atualiza a barra de volume com mola física fluida a 144Hz."""
        t = (clock.get_frame_time() / 1e6) if clock is not None else time.monotonic()
        dt = 0.0 if getattr(self, "_ult_t_vol", None) is None else t - self._ult_t_vol
        self._ult_t_vol = t
        assentou = self._mola_vol.passo(min(dt, 1.0 / 30.0))
        frac = max(0.0, min(1.0, self._mola_vol.x))
        if hasattr(self, "hud_barra"):
            self.hud_barra.set_fraction(frac)
        if hasattr(self, "sys_barra_vol"):
            self.sys_barra_vol.set_fraction(frac)
        if assentou:
            self._tick_vol_id = 0
            self._ult_t_vol = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _mostrar_hud_volume(self) -> None:
        if not self._pronto or self._expandido:
            return

        # Se houver notificação ativa, cancela
        if getattr(self, "_notif_timer", 0):
            GLib.source_remove(self._notif_timer)
            self._notif_timer = 0
            self.notif_box.set_visible(False)
            self._notif_ativa = False

        self._hud_ativo = True
        self._render_cabecalho()

        mudo = self._mudo
        vol = self._vol or 0
        if mudo:
            self.hud_icone.set_text("󰖁")
            self.hud_icone.add_css_class("mudo")
            self.hud_pct.set_text("Mudo")
        else:
            self.hud_icone.remove_css_class("mudo")
            if vol > 66:
                self.hud_icone.set_text("󰕾")
            elif vol > 33:
                self.hud_icone.set_text("󰖀")
            else:
                self.hud_icone.set_text("󰕿")
            self.hud_pct.set_text(f"{vol}%")

        alvo_frac = 0.0 if mudo else min(1.0, max(0.0, vol / 100.0))
        self._mola_vol.ir_para(alvo_frac)
        if not getattr(self, "_tick_vol_id", 0):
            self._ult_t_vol = None
            try:
                self._tick_vol_id = self.win.add_tick_callback(self._passo_mola_vol)
            except Exception:
                self._tick_vol_id = 0
                self.hud_barra.set_fraction(alvo_frac)

        self._animar_tamanho(260, self._altura_alvo(False), resposta=0.25, amortecimento=0.82)

        if self._hud_timer:
            GLib.source_remove(self._hud_timer)
        self._hud_timer = GLib.timeout_add(1500, self._esconder_hud_volume)

    def _esconder_hud_volume(self) -> bool:
        self._hud_ativo = False
        self._hud_timer = 0
        self._render_cabecalho()
        if not self._expandido:
            self._aplicar_conteudo()
        self._sincronizar_eq()
        return False

    # -- Manipulação e Interação de Notificações ----------------------------
    def _ao_drag_notif_inicio(self, _gesture, _start_x, _start_y) -> None:
        self._arrastou = False
        self._notif_arrastando = True
        if getattr(self, "_notif_timer", 0):
            GLib.source_remove(self._notif_timer)
            self._notif_timer = 0

    def _ao_drag_notif_atualizar(self, _gesture, offset_x, offset_y) -> None:
        if abs(offset_y) > 6 or abs(offset_x) > 6:
            self._arrastou = True
        # Se arrastou para baixo mais de 10px, expande o cartão detalhado
        if offset_y > 10 and not self._expandido:
            if getattr(self, "_notif_ativa", False):
                self._expandir_notificacao_detalhe()
            else:
                self.expandir(True)

    def _ao_drag_notif_fim(self, _gesture, offset_x, offset_y) -> None:
        if abs(offset_y) > 6 or abs(offset_x) > 6:
            self._arrastou = True
            GLib.timeout_add(150, lambda: setattr(self, "_arrastou", False))
        else:
            self._arrastou = False

        self._notif_arrastando = False
        if offset_y < -15 and self._expandido:
            if getattr(self, "_notif_ativa", False):
                self._esconder_hud_notif()
            else:
                self.expandir(False)
        elif not self._dentro() and getattr(self, "_notif_ativa", False):
            if getattr(self, "_notif_timer", 0):
                GLib.source_remove(self._notif_timer)
            self._notif_timer = GLib.timeout_add(1800, self._esconder_hud_notif)

    def _expandir_notificacao_detalhe(self) -> None:
        if not getattr(self, "_notif_ativa", False):
            return
        if getattr(self, "_notif_timer", 0):
            GLib.source_remove(self._notif_timer)
            self._notif_timer = 0
        self._notif_expandida = True
        if hasattr(self, "stack"):
            self.stack.set_visible_child_name("notificacao")
        if hasattr(self, "page_dots_box"):
            self.page_dots_box.set_visible(False)
        self.expandir(True)

    def _ao_copiar_notificacao(self, *_) -> None:
        dados = getattr(self, "_notif_dados", {})
        resumo = dados.get("summary", "")
        corpo = dados.get("body", "")
        texto = f"{resumo}\n{corpo}".strip() if (resumo and corpo) else (resumo or corpo or "")
        if texto:
            try:
                subprocess.run(["wl-copy", texto], check=True, timeout=0.5)
                self.btn_notif_copiar.set_label("󰄬 Copiado!")
                GLib.timeout_add(1500, lambda: self.btn_notif_copiar.set_label("󰅍 Copiar"))
            except Exception as e:
                log(f"Falha ao copiar texto da notificação: {e}")

    def _mostrar_hud_notif(self, app: str, summary: str, body: str = "",
                           urgency: str = "NORMAL", duracao_ms: int = 3500) -> None:
        if not self._pronto:
            return

        # Se DND estiver ativo e não for crítico nem alerta de DND, suprime
        if self._dnd_ativo and urgency.upper() not in ("CRITICAL", "DND"):
            return

        # Cancela timers pendentes
        if self._hud_timer:
            GLib.source_remove(self._hud_timer)
            self._hud_timer = 0
            self.hud_box.set_visible(False)
            self._hud_ativo = False

        if getattr(self, "_notif_timer", 0):
            GLib.source_remove(self._notif_timer)
            self._notif_timer = 0

        self._notif_ativa = True
        self._notif_expandida = False

        urg = urgency.upper()
        if urg == "CRITICAL":
            duracao_ms = max(duracao_ms, 5000)

        app_limpo = limpar_texto_notif(app) or "Notificação"
        sum_limpo = limpar_texto_notif(summary)
        body_limpo = limpar_texto_notif(body)

        self._notif_dados = {
            "app": app_limpo,
            "summary": sum_limpo,
            "body": body_limpo,
            "urgency": urg,
            "time": time.strftime("%H:%M"),
        }

        self.notif_icone.remove_css_class("critico")
        self.notif_icone.remove_css_class("dnd")

        icone_prefixo = "󰂚"
        if urg == "CRITICAL":
            icone_prefixo = "󰅚"
            self.notif_icone.set_text("󰅚")
            self.notif_icone.add_css_class("critico")
        elif urg == "DND":
            icone_prefixo = "󰂛" if self._dnd_ativo else "󰂚"
            self.notif_icone.set_text(icone_prefixo)
            self.notif_icone.add_css_class("dnd")
        else:
            self.notif_icone.set_text("󰂚")

        # Dados do banner compacto
        self.notif_app.set_text(self._notif_dados["app"])
        texto = self._notif_dados["summary"]
        if self._notif_dados["body"]:
            texto = f"{texto}: {self._notif_dados['body']}" if texto else self._notif_dados["body"]
        self.notif_texto.set_text(texto or "—")

        # Dados do cartão detalhado (Hero)
        self.notif_detalhe_app.set_text(f"{icone_prefixo}  {self._notif_dados['app']}")
        self.notif_detalhe_tempo.set_text(self._notif_dados["time"])
        self.notif_detalhe_titulo.set_text(self._notif_dados["summary"] or self._notif_dados["app"])
        self.notif_detalhe_corpo.set_text(self._notif_dados["body"] or "Sem conteúdo adicional.")
        self.btn_notif_copiar.set_label("󰅍 Copiar")

        # Largura adaptativa estilo Apple Dynamic Island baseada na extensão do conteúdo
        comp_texto = len(self._notif_dados["app"]) + len(texto or "")
        largura_alvo = min(430, max(320, 140 + int(comp_texto * 4.2)))
        self._notif_largura_alvo = largura_alvo

        self._render_cabecalho()

        # Brilho luminoso da borda estilo Apple OLED Dynamic Island
        self.pilula.add_css_class("notificando")
        if urg == "CRITICAL":
            self.pilula.add_css_class("notificando-critico")

        if not self._expandido:
            # Efeito de surgimento horizontal (<-->): mola elástica com overshoot sutil (~5.4%)
            # resposta = 0.38s, amortecimento = 0.68
            self._animar_tamanho(largura_alvo, self._altura_alvo(False), resposta=0.38, amortecimento=0.68)
        else:
            # Se já estava aberta, mostra o cartão detalhado
            if hasattr(self, "stack"):
                self.stack.set_visible_child_name("notificacao")
            if hasattr(self, "page_dots_box"):
                self.page_dots_box.set_visible(False)
            self._animar_tamanho(380, self._altura_alvo(True), resposta=0.36, amortecimento=0.75)

        # Inicia auto-dismiss (a menos que cursor já esteja sobre a ilha)
        if not self._dentro():
            self._notif_timer = GLib.timeout_add(duracao_ms, self._esconder_hud_notif)

    def _esconder_hud_notif(self) -> bool:
        if not getattr(self, "_notif_ativa", False):
            return False

        self._notif_ativa = False
        self._notif_expandida = False
        if getattr(self, "_notif_timer", 0):
            GLib.source_remove(self._notif_timer)
            self._notif_timer = 0

        # Remove brilho e classes CSS de notificação
        self.pilula.remove_css_class("notificando")
        self.pilula.remove_css_class("notificando-critico")

        if hasattr(self, "page_dots_box"):
            self.page_dots_box.set_visible(True)

        self._render_cabecalho()

        if self._expandido and not self._fixado and not self._dentro():
            self.expandir(False)
        elif not self._expandido:
            self._aplicar_conteudo()
            # Fechamento elástico estilo Apple Dynamic Island (-><-):
            # Contração fluida com amortecimento firme (zeta=0.92, resposta=0.28s) sem repique
            largura_retorno = self._largura_alvo(False)
            altura_retorno = self._altura_alvo(False)
            self._animar_tamanho(largura_retorno, altura_retorno, resposta=0.28, amortecimento=0.92)
        else:
            if hasattr(self, "stack"):
                inicial = "midia" if self._tem_player else "produtividade"
                self._mudar_pagina(inicial)
            self._aplicar_conteudo()
            self._animar_tamanho(self._largura_alvo(True), self._altura_alvo(True), resposta=0.32, amortecimento=0.85)

        self._sincronizar_eq()
        return False

    def _proxy_dunst(self):
        """Proxy da interface de controle do dunst (org.dunstproject.cmd0).

        A propriedade `paused` é `emits-change` (conferido com busctl
        introspect, dunst 1.13), então o estado do Não Perturbe chega por
        PropertiesChanged — antes era um `dunstctl is-paused` a cada 2 s, até
        com a ilha recolhida. Se o dunst reiniciar, o GDBusProxy acompanha o
        novo dono do nome e recarrega o cache sozinho.
        """
        try:
            p = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.dunstproject.cmd0", None)
        except GLib.Error as e:
            log(f"dunst via D-Bus indisponível ({e.message}): DND por polling")
            return None
        p.connect("g-properties-changed",
                  lambda *_: self._atualizar_estado_dnd(forcar_hud=False))
        p.connect("notify::g-name-owner",
                  lambda *_: self._atualizar_estado_dnd(forcar_hud=False))
        return p

    def _dnd_dbus(self) -> bool | None:
        """`paused` do cache do proxy; None se o dunst não respondeu."""
        if self._dunst is None:
            return None
        v = self._dunst.get_cached_property("paused")
        return None if v is None else bool(v.unpack())

    def _checar_dnd(self) -> bool:
        via_dbus = self._dnd_dbus()
        if via_dbus is not None:
            return via_dbus
        try:
            r = subprocess.run(["dunstctl", "is-paused"], capture_output=True, text=True, timeout=0.3)
            return r.stdout.strip().lower() == "true"
        except Exception:
            return False

    def _atualizar_estado_dnd(self, forcar_hud: bool = False) -> None:
        dnd = self._checar_dnd()
        if dnd != self._dnd_ativo or forcar_hud:
            self._dnd_ativo = dnd
            if hasattr(self, "b_dnd"):
                if dnd:
                    self.b_dnd.add_css_class("dnd-ativo")
                else:
                    self.b_dnd.remove_css_class("dnd-ativo")
            if hasattr(self, "relogio"):
                self._render_cabecalho()
            if forcar_hud:
                status = "Ativado" if dnd else "Desativado"
                self._mostrar_hud_notif(
                    app="Não Perturbe",
                    summary=status,
                    body="",
                    urgency="DND",
                    duracao_ms=1800
                )

    def _ao_clicar_dnd(self, *_) -> None:
        novo = not self._dnd_ativo
        feito = False
        if self._dunst is not None and self._dnd_dbus() is not None:
            try:
                # nome com ponto: o GDBusProxy separa interface e método
                self._dunst.call_sync(
                    "org.freedesktop.DBus.Properties.Set",
                    GLib.Variant("(ssv)", ("org.dunstproject.cmd0", "paused",
                                           GLib.Variant("b", novo))),
                    Gio.DBusCallFlags.NONE, 500, None)
                # o cache só muda quando o sinal chegar; o HUD já sai certo
                self._dunst.set_cached_property("paused", GLib.Variant("b", novo))
                feito = True
            except GLib.Error as e:
                log(f"falha ao alternar DND via D-Bus: {e.message}")
        if not feito:
            try:
                subprocess.run(["dunstctl", "set-paused", "toggle"], timeout=0.5)
            except Exception:
                pass
        self._atualizar_estado_dnd(forcar_hud=True)

    def _iniciar_ipc(self) -> None:
        sig = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        sock_path = f"{sig}/island.sock"
        try:
            if os.path.exists(sock_path):
                os.unlink(sock_path)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(sock_path)
            s.listen(5)
            s.setblocking(False)
            self._sock = s
            self._sock_path = sock_path
            GLib.io_add_watch(s.fileno(), GLib.IO_IN, self._ao_conexao_ipc)
            log(f"IPC da ilha iniciado em {sock_path} (SOCK_STREAM)")
        except Exception as e:
            log(f"Falha ao iniciar socket IPC: {e}")

    def _ao_conexao_ipc(self, _fd, _cond) -> bool:
        if not self._sock:
            return False
        try:
            conn, _ = self._sock.accept()
            conn.settimeout(1.0)
            threading.Thread(target=self._atender_cliente_ipc, args=(conn,), daemon=True).start()
        except Exception as e:
            log(f"Erro ao aceitar conexão IPC: {e}")
        return True

    def _atender_cliente_ipc(self, conn: socket.socket) -> None:
        with conn:
            try:
                data = conn.recv(8192)
                if not data:
                    return
                texto = data.decode("utf-8").strip()
                if texto.startswith("{") and texto.endswith("}"):
                    msg = json.loads(texto)
                else:
                    partes = texto.split(maxsplit=1)
                    acao = partes[0]
                    arg = partes[1] if len(partes) > 1 else ""
                    msg = {"acao": acao, "nome": arg, "argumento": arg}

                evento = threading.Event()
                resp = {"status": "ok"}

                def _processar():
                    nonlocal resp
                    try:
                        resp = self._executar_comando_ipc(msg)
                    except Exception as ex:
                        resp = {"status": "erro", "mensagem": str(ex)}
                    finally:
                        evento.set()
                    return False

                GLib.idle_add(_processar)
                evento.wait(timeout=1.5)
                conn.sendall(json.dumps(resp).encode("utf-8") + b"\n")
            except Exception as e:
                try:
                    conn.sendall(json.dumps({"status": "erro", "mensagem": str(e)}).encode("utf-8") + b"\n")
                except Exception:
                    pass

    def _executar_comando_ipc(self, msg: dict) -> dict:
        acao = msg.get("acao") or msg.get("action", "")
        if acao == "toggle":
            self._ao_clicar()
            return {"status": "ok", "expandido": bool(self._expandido), "fixado": bool(self._fixado)}
        elif acao == "expand":
            self.expandir(True)
            return {"status": "ok", "expandido": True}
        elif acao == "collapse":
            self.expandir(False)
            return {"status": "ok", "expandido": False}
        elif acao == "pin":
            modo = msg.get("modo", "toggle")
            if modo == "on":
                self._fixado = True
            elif modo == "off":
                self._fixado = False
            else:
                self._fixado = not self._fixado
            if self._fixado:
                self.pilula.add_css_class("fixado")
                self.expandir(True)
            else:
                self.pilula.remove_css_class("fixado")
            return {"status": "ok", "fixado": bool(self._fixado)}
        elif acao == "state":
            pagina = self.stack.get_visible_child_name() if hasattr(self, "stack") else "midia"
            d_mpris = self.mpris.dados() if self.mpris else {}
            return {
                "status": "ok",
                "dados": {
                    "expandido": bool(self._expandido),
                    "fixado": bool(self._fixado),
                    "pagina": pagina,
                    "mpris": {
                        "titulo": d_mpris.get("titulo", ""),
                        "artista": d_mpris.get("artista", ""),
                        "status": d_mpris.get("status", "Stopped"),
                    },
                    "volume": self._vol,
                    "dnd": bool(self._dnd_ativo),
                }
            }
        elif acao == "page":
            nome = msg.get("nome") or msg.get("name", "midia")
            self._mudar_pagina(nome)
            return {"status": "ok", "pagina": nome}
        elif acao == "media":
            cmd = msg.get("comando", "play-pause")
            mapa = {
                "play-pause": "PlayPause",
                "play": "Play",
                "pause": "Pause",
                "next": "Next",
                "prev": "Previous",
                "shuffle": "shuffle",
                "repeat": "repeat",
            }
            if cmd in ("shuffle", "repeat"):
                if cmd == "shuffle" and self.mpris:
                    self.mpris.alternar_shuffle()
                elif cmd == "repeat" and self.mpris:
                    self.mpris.alternar_loop()
            else:
                c = mapa.get(cmd, cmd)
                if self.mpris:
                    self.mpris.comando(c)
            return {"status": "ok"}
        elif acao == "notify":
            self._mostrar_hud_notif(
                app=msg.get("app", "Sistema"),
                summary=msg.get("summary") or msg.get("titulo", ""),
                body=msg.get("body") or msg.get("corpo", ""),
                urgency=msg.get("urgency") or msg.get("urgencia", "NORMAL"),
                duracao_ms=int(msg.get("duration") or msg.get("duracao_ms", 3500)),
            )
            return {"status": "ok"}
        elif acao in ("dnd", "dnd_toggle"):
            self._ao_clicar_dnd()
            return {"status": "ok", "dnd": bool(self._dnd_ativo)}
        elif acao == "scroll":
            dy = float(msg.get("dy", 1.0))
            self._ao_scroll_sessoes(None, 0.0, dy)
            return {"status": "ok"}
        elif acao == "activity":
            op = msg.get("operacao", "adicionar")
            if op == "remover":
                self.remover_atividade(str(msg.get("id", "padrao")))
            else:
                self.adicionar_atividade(
                    id=str(msg.get("id", "padrao")),
                    icon=str(msg.get("icon") or msg.get("icone", "󰔟")),
                    text=str(msg.get("text") or msg.get("texto", "")),
                    subtext=str(msg.get("subtext") or msg.get("subtexto", "")),
                    progress=msg.get("progress") if msg.get("progress") is not None else msg.get("progresso"),
                    color=str(msg.get("color", "#cba6f7")),
                    ttl=int(msg.get("ttl", 0)),
                )
            return {"status": "ok"}
        elif acao == "activity_end":
            self.remover_atividade(str(msg.get("id", "padrao")))
            return {"status": "ok"}
        elif acao == "activity_clear":
            self.limpar_atividades()
            return {"status": "ok"}
        return {"status": "ok"}

    def _ao_mudar_volume(self) -> None:
        """Chamado pelo Volume a cada evento de sink (e uma vez na subida)."""
        ant_vol = self._vol
        ant_mudo = self._mudo
        self._vol = self.volume.valor
        self._mudo = self.volume.mudo
        if hasattr(self, "eq_area"):
            self.eq_area.atualizar_sink()
        if self._expandido:
            self._mostrar_sistema()
        if self._pronto and (ant_vol != self._vol or ant_mudo != self._mudo):
            self._mostrar_hud_volume()
        self._sincronizar_eq()

    def _mostrar_sistema(self) -> None:
        """Atualiza as barras e valores de CPU, RAM, Volume, Saída de Áudio e Quick Toggles."""
        # CPU & Temperatura
        t = self.sistema.temp_cpu()
        if t is not None:
            self._temp = t
        temp_str = f" • {self._temp}°C" if getattr(self, "_temp", None) else ""
        cpu_frac = max(0.0, min(1.0, self._cpu / 100.0))
        if hasattr(self, "sys_barra_cpu"):
            self.sys_barra_cpu.set_fraction(cpu_frac)
        if hasattr(self, "sys_val_cpu"):
            self.sys_val_cpu.set_text(f"{self._cpu}%{temp_str}")

        # RAM
        mem_frac = max(0.0, min(1.0, self._mem / 100.0))
        if hasattr(self, "sys_barra_mem"):
            self.sys_barra_mem.set_fraction(mem_frac)
        if hasattr(self, "sys_val_mem"):
            self.sys_val_mem.set_text(f"{self._mem}%")

        # Volume
        vol = self._vol if self._vol is not None else 0
        mudo = self._mudo
        vol_frac = max(0.0, min(1.0, vol / 100.0))
        if hasattr(self, "sys_barra_vol"):
            self.sys_barra_vol.set_fraction(vol_frac)
        if hasattr(self, "sys_val_vol"):
            self.sys_val_vol.set_text("Mudo" if mudo else f"{vol}%")
        if hasattr(self, "sys_icone_vol"):
            if mudo:
                self.sys_icone_vol.set_text("󰖁")
                self.sys_icone_vol.add_css_class("mudo")
            else:
                self.sys_icone_vol.remove_css_class("mudo")
                if vol > 66:
                    self.sys_icone_vol.set_text("󰕾")
                elif vol > 33:
                    self.sys_icone_vol.set_text("󰖀")
                else:
                    self.sys_icone_vol.set_text("󰕿")

        # Seletor de Saída de Áudio (Fones vs Monitor)
        if hasattr(self, "btn_audio_sink"):
            sink = self.volume.sink_atual()
            if sink == "monitor":
                self.btn_audio_sink.set_label("󰓃 Monitor")
                self.btn_audio_sink.set_tooltip_text("Saída: Monitor 27G4 (Clique para alternar para os Fones)")
            else:
                self.btn_audio_sink.set_label("󰋋 Fones")
                self.btn_audio_sink.set_tooltip_text("Saída: Fones de Ouvido (Clique para alternar para o Monitor 27G4)")

        # Quick Toggles: Hotspot
        if hasattr(self, "btn_hotspot"):
            if self.rede.hotspot_ativo():
                self.btn_hotspot.add_css_class("ativo-hotspot")
            else:
                self.btn_hotspot.remove_css_class("ativo-hotspot")

        # Quick Toggles: WARP VPN
        if hasattr(self, "btn_vpn"):
            if self.rede.vpn_ativa():
                self.btn_vpn.add_css_class("ativo-vpn")
            else:
                self.btn_vpn.remove_css_class("ativo-vpn")

        # Quick Toggles: AdGuard
        if hasattr(self, "btn_adguard"):
            if self.rede.adguard_ativo():
                self.btn_adguard.add_css_class("ativo-adguard")
            else:
                self.btn_adguard.remove_css_class("ativo-adguard")

        # Quick Toggles: Celular / KDE Connect
        if hasattr(self, "btn_phone"):
            nome_disp = self.kde.dispositivo_nome or "Celular"
            nome_curto = nome_disp.split()[0]
            if self.kde.conectado and self.kde.carga is not None:
                chg = self.kde.carga
                is_chg = self.kde.carregando
                ico = "󰂄" if is_chg else ("󰁹" if chg >= 90 else ("󰂀" if chg >= 50 else "󰁽"))
                self.btn_phone.set_label(f"󰄡  {nome_curto}: {chg}% {ico}")
                if is_chg:
                    self.btn_phone.add_css_class("charging")
                else:
                    self.btn_phone.remove_css_class("charging")
                self.btn_phone.set_tooltip_text(f"{nome_disp} (KDE Connect) - {chg}% ({'Carregando' if is_chg else 'Bateria'})")
            else:
                self.btn_phone.set_label(f"󰄡  {nome_curto} Off")
                self.btn_phone.remove_css_class("charging")
                self.btn_phone.set_tooltip_text(f"{nome_disp} desconectado no KDE Connect")

        # Fallback de compatibilidade
        if hasattr(self, "vol_lbl") and self.vol_lbl is not getattr(self, "sys_val_vol", None):
            icone = "󰖁" if self._mudo else "󰕿"
            self.vol_lbl.set_text(f"{icone} {vol}%")
        if hasattr(self, "proc_lbl"):
            self.proc_lbl.set_text(f"󰻠 {self._cpu}%   󰍛 {self._mem}%")

    def _ao_clicar_volume(self, *_args) -> None:
        self.volume.alternar_mudo()

    def _ao_clicar_processos(self, *_args) -> None:
        caminho = os.path.expanduser("~/.config/hypr/scripts/task_manager.sh")
        if os.path.isfile(caminho):
            subprocess.Popen([caminho], env=self.hypr._ambiente)

    def _ao_clicar_screenshot(self, *_) -> None:
        self.expandir(False)
        caminho = os.path.expanduser("~/.config/hypr/scripts/screenshot.sh")
        if os.path.isfile(caminho):
            subprocess.Popen([caminho, "area"], env=self.hypr._ambiente)

    def _ao_clicar_clipboard(self, *_) -> None:
        self.expandir(False)
        caminho = os.path.expanduser("~/.config/hypr/scripts/clipboard.sh")
        if os.path.isfile(caminho):
            subprocess.Popen([caminho], env=self.hypr._ambiente)

    def _ao_clicar_trocar_player(self, *_) -> None:
        if self.mpris:
            self.mpris.alternar_proximo_player()

    def _ao_clicar_power(self, *_) -> None:
        self.expandir(False)
        caminho = os.path.expanduser("~/.config/hypr/scripts/powermenu.sh")
        if os.path.isfile(caminho):
            subprocess.Popen([caminho], env=self.hypr._ambiente)

    def _atualizar_ws(self, ativo, existentes) -> None:
        """Redesenha os pontos das áreas de trabalho.

        1..5 são os atalhos reais (Super+1..5); um workspace fora dessa faixa
        que exista no monitor da ilha ganha um ponto extra em vez de sumir.
        """
        ids = list(range(1, 6))
        for w in existentes or ():
            if w not in ids:
                ids.append(w)
        while len(self._pontos) < len(ids):
            ws_id = ids[len(self._pontos)]
            ponto = self._criar_ponto(ws_id)
            self.dots.append(ponto)
            self._pontos.append(ponto)
        for i, ponto in enumerate(self._pontos):
            visivel = i < len(ids)
            ponto.set_visible(visivel)
            if visivel:
                ws_num = ids[i]
                ponto.ws_id = ws_num
                ponto.remove_css_class("ativo")
                ponto.remove_css_class("ocupado")
                if ws_num == ativo:
                    ponto.add_css_class("ativo")
                elif ws_num in (existentes or ()):
                    ponto.add_css_class("ocupado")

    def _atualizar_janela(self, titulo: str, classe: str = "") -> None:
        t = (titulo or "").strip()
        t_limpo = t.replace("_", " ") if t else "Área de Trabalho"
        self.janela.set_text(t_limpo)
        c_low = (classe or "").lower()
        if any(k in c_low for k in ("kitty", "terminal", "alacritty", "foot", "wezterm")):
            icone = "󰆍"
        elif any(k in c_low for k in ("code", "cursor", "vsc", "nvim", "vim")):
            icone = "󰨞"
        elif any(k in c_low for k in ("firefox", "chrome", "zen", "brave", "chromium")):
            icone = "󰈹"
        elif "spotify" in c_low:
            icone = "󰓇"
        elif any(k in c_low for k in ("pdf", "zathura", "document", "storyteller")):
            icone = "󰈦"
        elif any(k in c_low for k in ("thunar", "nautilus", "dolphin", "files")):
            icone = "󰉋"
        else:
            icone = "󰖲"
        self.janela_icone.set_text(icone)

        # Só verifica fullscreen se a classe da janela mudou ou se já estávamos em tela cheia
        classe_mudou = (getattr(self, "_ultima_classe_janela", None) != classe)
        self._ultima_classe_janela = classe
        if classe_mudou or getattr(self, "_fullscreen", False):
            self._verificar_e_aplicar_fullscreen()


def main() -> int:
    global DEBUG, DUMP, TESTE_CONTROLE, MONITOR
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--dump", action="store_true", help="imprime tamanhos e sai")
    ap.add_argument("--testar-controle", action="store_true",
                    help="dispara o play/pause sozinho (teste)")
    ap.add_argument("--monitor", metavar="CONECTOR", default=MONITOR,
                    help="monitor onde a ilha vive (ex.: DP-2); vazio = do foco")
    args = ap.parse_args()
    DEBUG = args.debug
    DUMP = args.dump
    TESTE_CONTROLE = args.testar_controle
    MONITOR = args.monitor

    app = Gtk.Application(application_id="dev.bruno.Island")
    estado = {"ilha": None}

    def ativar(a):
        if estado["ilha"] is None:
            estado["ilha"] = Ilha(a)

    app.connect("activate", ativar)
    return app.run([])


if __name__ == "__main__":
    sys.exit(main())


