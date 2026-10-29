"""Fonte de reprodução de mídia via protocolo MPRIS (org.mpris.MediaPlayer2)."""

from __future__ import annotations
import colorsys
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any, List, Tuple
from gi.repository import Gio, GLib
from .base import Fonte

CAMINHO_MPRIS = "/org/mpris/MediaPlayer2"
IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"
CACHE_ART = os.path.expanduser("~/.cache/island/art")


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


class FonteMpris(Fonte):
    """Fonte reativa de players MPRIS com desvio zero e interpolação de posição."""

    def __init__(self) -> None:
        super().__init__()
        self.bus: Optional[Gio.DBusConnection] = None
        self.proxy: Optional[Gio.DBusProxy] = None
        self.nome: Optional[str] = None
        self._nomes_cache: Optional[List[str]] = None
        self._proxies: Dict[str, Gio.DBusProxy] = {}
        self._fixado_manual: Optional[str] = None
        self._ident: Optional[Tuple[Tuple[str, str], str]] = None
        self._desktop_entry_cache: Dict[str, str] = {}
        self._pos_base_us: int = 0
        self._pos_timestamp: float = time.monotonic()
        self._playback_rate: float = 1.0
        self._status: str = "Stopped"
        self._duracao: int = 0
        self._ultimo_sync_d_bus: float = 0.0
        self._sub_ids: List[int] = []

    def ao_ativar(self) -> None:
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            s1 = self.bus.signal_subscribe(
                "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
                self._ao_name_owner_changed
            )
            s2 = self.bus.signal_subscribe(
                None, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                CAMINHO_MPRIS, IFACE_PLAYER, Gio.DBusSignalFlags.NONE,
                self._ao_props_qualquer_player
            )
            self._sub_ids.extend([s1, s2])
            self.escolher()
        except Exception:
            pass

    def ao_desativar(self) -> None:
        if self.bus:
            for s_id in self._sub_ids:
                try:
                    self.bus.signal_unsubscribe(s_id)
                except Exception:
                    pass
            self._sub_ids.clear()

    def _ao_name_owner_changed(self, _conn, _sender, _path, _iface, _signal, params, _user_data=None) -> None:
        try:
            nome, _old, _new = params.unpack()
            if nome.startswith("org.mpris.MediaPlayer2."):
                self._nomes_cache = None
                if not _new:
                    self._proxies.pop(nome, None)
                    self._desktop_entry_cache.pop(nome, None)
                self.escolher()
        except Exception:
            pass

    def _ao_props_qualquer_player(self, _conn, _sender, _path, _iface, _signal, params, _user_data=None) -> None:
        try:
            changed = params.unpack()[1]
            if "PlaybackStatus" in changed:
                self.escolher()
        except Exception:
            pass

    def _nomes(self) -> List[str]:
        if self._nomes_cache is not None:
            return self._nomes_cache
        if not self.bus:
            return []
        try:
            r = self.bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "ListNames", None, GLib.VariantType.new("(as)"),
                Gio.DBusCallFlags.NONE, 250, None
            )
            self._nomes_cache = [n for n in r.unpack()[0] if n.startswith("org.mpris.MediaPlayer2.")]
        except Exception:
            self._nomes_cache = []
        return self._nomes_cache

    def _proxy(self, nome: str) -> Optional[Gio.DBusProxy]:
        if nome in self._proxies:
            return self._proxies[nome]
        if not self.bus:
            return None
        try:
            p = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None, nome, CAMINHO_MPRIS,
                IFACE_PLAYER, None
            )
            self._proxies[nome] = p
            return p
        except Exception:
            return None

    @staticmethod
    def _prop(proxy: Optional[Gio.DBusProxy], nome: str) -> Any:
        if not proxy:
            return None
        try:
            v = proxy.get_cached_property(nome)
            return v.unpack() if v is not None else None
        except Exception:
            return None

    def escolher(self) -> bool:
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
            candidatos.append(((manual, ordem, 0 if tem else 1, 0 if nome == self.nome else 1), nome, p))
        candidatos.sort(key=lambda c: c[0])

        if not candidatos:
            if self.proxy is not None:
                self.proxy, self.nome = None, None
                self._fixado_manual = None
                self.notificar("mudar", None)
            return False

        _, nome, p = candidatos[0]
        if nome != self.nome:
            self.proxy, self.nome = p, nome
            p.connect("g-properties-changed", self._ao_mudar_propriedades)
            p.connect("g-signal", self._ao_sinal_player)
            self._status = self._prop(p, "PlaybackStatus") or "Stopped"
            md = self._prop(p, "Metadata") or {}
            self._duracao = int(md.get("mpris:length") or 0)
            self.sincronizar_posicao(forcar=True)
            self.notificar("mudar", self.dados())
        return False

    def _ao_sinal_player(self, _proxy, _sender_name, signal_name, parameters) -> None:
        if signal_name == "Seeked":
            try:
                pos_us = int(parameters.unpack()[0])
                self._pos_base_us = pos_us
                self._pos_timestamp = time.monotonic()
                self._ultimo_sync_d_bus = self._pos_timestamp
                self.notificar("seeked", pos_us)
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
        self.notificar("mudar", self.dados())

    def sincronizar_posicao(self, forcar: bool = False) -> None:
        def _cb(pos):
            estimado = self.posicao_estimada()
            desvio_us = abs(pos - estimado)
            if forcar or desvio_us > 1_500_000 or self._status != "Playing":
                self._pos_base_us = pos
                self._pos_timestamp = time.monotonic()
            self._ultimo_sync_d_bus = time.monotonic()
            self.notificar("posicao", self.posicao_estimada())
        self.posicao(_cb)

    def posicao_estimada(self) -> int:
        if self._status != "Playing":
            return max(0, self._pos_base_us)
        delta_s = time.monotonic() - self._pos_timestamp
        estimado = int(self._pos_base_us + (delta_s * 1_000_000 * self._playback_rate))
        if self._duracao > 0:
            return min(self._duracao, max(0, estimado))
        return max(0, estimado)

    def desktop_entry(self) -> str:
        if not self.nome or not self.bus:
            return ""
        if self.nome in self._desktop_entry_cache:
            return self._desktop_entry_cache[self.nome]
        try:
            r = self.bus.call_sync(
                self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.mpris.MediaPlayer2", "DesktopEntry")),
                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 200, None
            )
            val = str(r.unpack()[0] or "").strip()
        except Exception:
            val = ""
        self._desktop_entry_cache[self.nome] = val
        return val

    def identidade(self, titulo: str = "") -> str:
        if not self.nome or not self.bus:
            return ""
        chave = (self.nome, titulo)
        if self._ident is not None and self._ident[0] == chave:
            return self._ident[1]
        try:
            r = self.bus.call_sync(
                self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.mpris.MediaPlayer2", "Identity")),
                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 200, None
            )
            valor = str(r.unpack()[0] or "").strip()
        except Exception:
            valor = ""
        self._ident = (chave, valor)
        return valor

    def dados(self) -> Optional[Dict[str, Any]]:
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

    def ajustar_volume(self, delta: float) -> Optional[float]:
        p = self.proxy
        if p is None or not self.nome or not self.bus or not delta:
            return None
        atual = self._prop(p, "Volume")
        if atual is not None:
            novo = max(0.0, min(1.0, float(atual) + delta))
            self.bus.call(
                self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Set",
                GLib.Variant("(ssv)", (IFACE_PLAYER, "Volume", GLib.Variant("d", novo))),
                None, Gio.DBusCallFlags.NONE, 300, None, None
            )
            return novo
        return None

    def posicao(self, cb) -> None:
        if self.proxy is None or not self.nome or not self.bus:
            return
        self.bus.call(
            self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (IFACE_PLAYER, "Position")),
            GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 300, None,
            lambda b, res: self._pos_resp(b, res, cb)
        )

    def _pos_resp(self, bus, res, cb) -> None:
        try:
            cb(int(bus.call_finish(res).unpack()[0]))
        except Exception:
            pass

    def comando(self, metodo: str) -> None:
        if self.proxy is None:
            return
        self.proxy.call(metodo, None, Gio.DBusCallFlags.NONE, 300, None, None)

    def play_pause(self) -> None:
        self.comando("PlayPause")

    def proxima(self) -> None:
        self.comando("Next")

    def anterior(self) -> None:
        self.comando("Previous")

    def seek(self, alvo_us: int) -> None:
        if self.proxy is None or not self.nome:
            return
        if not bool(self._prop(self.proxy, "CanSeek")):
            return
        self._pos_base_us = int(alvo_us)
        self._pos_timestamp = time.monotonic()
        self._ultimo_sync_d_bus = self._pos_timestamp
        md = self._prop(self.proxy, "Metadata") or {}
        trackid = md.get("mpris:trackid")

        def _fallback_relativo():
            def aplica_seek(pos_atual):
                diff = int(alvo_us - pos_atual)
                try:
                    self.proxy.call("Seek", GLib.Variant("(x)", (diff,)), Gio.DBusCallFlags.NONE, 300, None, None)
                except Exception:
                    pass
            self.posicao(aplica_seek)

        if isinstance(trackid, str) and trackid.startswith("/") and not trackid.endswith("/NoTrack"):
            def _ao_set_position(proxy, res):
                try:
                    proxy.call_finish(res)
                except Exception:
                    _fallback_relativo()

            self.proxy.call(
                "SetPosition", GLib.Variant("(ox)", (trackid, int(alvo_us))),
                Gio.DBusCallFlags.NONE, 300, None, _ao_set_position
            )
        else:
            _fallback_relativo()

    def alternar_shuffle(self) -> None:
        if self.proxy is None or not self.nome or not self.bus:
            return
        atual = bool(self._prop(self.proxy, "Shuffle"))
        novo = not atual
        self.bus.call(
            self.nome, CAMINHO_MPRIS, "org.freedesktop.DBus.Properties", "Set",
            GLib.Variant("(ssv)", (IFACE_PLAYER, "Shuffle", GLib.Variant("b", novo))),
            None, Gio.DBusCallFlags.NONE, 300, None, None
        )

    def alternar_loop(self) -> None:
        if self.proxy is None or not self.nome or not self.bus:
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
            None, Gio.DBusCallFlags.NONE, 300, None, None
        )

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
