"""Fonte de áudio do sistema (Volume, Mudo, Sink e Microfone) via pactl / wpctl."""

from __future__ import annotations
import os
import subprocess
import threading
from typing import Tuple, Optional, List
from .base import Fonte


class FonteVolume(Fonte):
    def __init__(self, padrao_sink_monitor: str = "hdmi-stereo", padrao_sink_fones: str = "analog-stereo") -> None:
        super().__init__()
        self.padrao_sink_monitor = padrao_sink_monitor
        self.padrao_sink_fones = padrao_sink_fones
        self.valor: Optional[int] = None
        self.mudo: bool = False
        self.mic_ativo: bool = False
        self._sink_cache: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread_sub: Optional[threading.Thread] = None
        self._ambiente = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}

    def ao_ativar(self) -> None:
        self.reler()
        self._sink_cache = self._ler_sink()
        self._checar_mic()
        self._iniciar_subscribe()

    def ao_desativar(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    def _ler(self) -> Tuple[Optional[int], bool]:
        try:
            out = subprocess.run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True, text=True, timeout=1, env=self._ambiente
            ).stdout
            return round(float(out.split()[1]) * 100), "MUTED" in out
        except Exception:
            return None, False

    def _ler_sinks_disponiveis(self) -> List[str]:
        try:
            out = subprocess.run(
                ["pactl", "list", "short", "sinks"],
                capture_output=True, text=True, timeout=0.5, env=self._ambiente
            ).stdout
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
            out = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=0.3, env=self._ambiente
            ).stdout.strip()
            if self.padrao_sink_monitor in out or "hdmi" in out:
                return "monitor"
            return "fones"
        except Exception:
            return "fones"

    def sink_atual(self) -> str:
        if self._sink_cache is None:
            self._sink_cache = self._ler_sink()
        return self._sink_cache

    def alternar_sink(self) -> None:
        atual = self.sink_atual()
        quer_monitor = (atual == "fones")
        padrao_alvo = self.padrao_sink_monitor if quer_monitor else self.padrao_sink_fones
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
                    subprocess.run(
                        ["pactl", "set-default-sink", alvo],
                        capture_output=True, timeout=1.0, env=self._ambiente
                    )
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def alternar_mudo(self) -> None:
        def _fazer():
            try:
                subprocess.run(
                    ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
                    capture_output=True, timeout=1, env=self._ambiente
                )
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def ajustar(self, delta_pct: int) -> None:
        sinal = "+" if delta_pct >= 0 else "-"
        valor = f"{abs(delta_pct)}%{sinal}"
        def _fazer():
            try:
                subprocess.run(
                    ["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", valor],
                    capture_output=True, timeout=1, env=self._ambiente
                )
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def definir(self, pct: int) -> None:
        val = max(0.0, min(1.5, pct / 100.0))
        def _fazer():
            try:
                subprocess.run(
                    ["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", f"{val:.2f}"],
                    capture_output=True, timeout=1, env=self._ambiente
                )
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def reler(self) -> None:
        novo, mudo = self._ler()
        if novo is None:
            return
        if (novo, mudo) != (self.valor, self.mudo):
            self.valor, self.mudo = novo, mudo
            self.notificar("volume", {"valor": novo, "mudo": mudo})

    def _checar_mic(self) -> None:
        def _fazer():
            try:
                out = subprocess.run(
                    ["pactl", "list", "short", "source-outputs"],
                    capture_output=True, text=True, timeout=0.3, env=self._ambiente
                ).stdout
                ativo = bool(out.strip())
                if ativo != self.mic_ativo:
                    self.mic_ativo = ativo
                    self.notificar("mic", {"ativo": ativo})
            except Exception:
                pass
        threading.Thread(target=_fazer, daemon=True).start()

    def _iniciar_subscribe(self) -> None:
        def _worker():
            try:
                self._proc = subprocess.Popen(
                    ["pactl", "subscribe"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    env=self._ambiente
                )
                if not self._proc.stdout:
                    return
                for linha in iter(self._proc.stdout.readline, ""):
                    if not self.esta_ativa:
                        break
                    if "on source-output" in linha:
                        self._checar_mic()
                    elif any(k in linha for k in (" on server", " on card ")):
                        novo_sink = self._ler_sink()
                        if novo_sink != self._sink_cache:
                            self._sink_cache = novo_sink
                            self.notificar("sink", {"sink": novo_sink})
                        self.reler()
                    elif "sink-input" not in linha and any(k in linha for k in (" on sink ", " on server", " on card ")):
                        self.reler()
            except Exception:
                pass

        self._thread_sub = threading.Thread(target=_worker, daemon=True)
        self._thread_sub.start()
