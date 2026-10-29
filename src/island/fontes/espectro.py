"""Captura e análise espectral de áudio em tempo real via monitor do PipeWire."""

from __future__ import annotations
import math
import os
import struct
import subprocess
import threading
from typing import Optional, Tuple


class CapturadorEspectro:
    """Captura o stream de áudio do monitor do PipeWire e calcula magnitudes de 4 bandas espectrais."""

    def __init__(self, taxa: int = 8000, n_amostras: int = 256) -> None:
        self.taxa = taxa
        self.n = n_amostras
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._rodando = False
        self._bandas: Tuple[float, float, float, float] = (0.16, 0.16, 0.16, 0.16)
        self._lock = threading.Lock()
        self._peak_tracker = 0.20
        self._ativo_com_som = False
        self._sink_atual: Optional[str] = None

        # Pré-computa janela Hanning
        self._window = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (self.n - 1)) for i in range(self.n)]

        # Pré-computa bit-reversal para FFT
        bits = int(math.log2(self.n))
        self._rev = [0] * self.n
        for i in range(self.n):
            r = 0
            for j in range(bits):
                if (i >> j) & 1:
                    r |= 1 << (bits - 1 - j)
            self._rev[i] = r

        # Pré-computa twiddle factors da FFT Cooley-Tukey
        self._twiddles = {}
        step = 2
        while step <= self.n:
            half = step // 2
            self._twiddles[step] = [
                math.e ** (-2j * math.pi * k / step) for k in range(half)
            ]
            step <<= 1

    def _fft(self, x: list[float]) -> list[complex]:
        """FFT iterativa Cooley-Tukey in-place em tempo O(N log N)."""
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

    def _obter_sink_monitor(self) -> Optional[str]:
        """Obtém o nome do sink padrão atual para capturar o áudio do desktop."""
        try:
            res = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=0.3
            )
            if res.returncode == 0 and res.stdout.strip():
                return f"{res.stdout.strip()}.monitor"
        except Exception:
            pass
        return None

    def iniciar(self, sink_monitor: Optional[str] = None) -> None:
        """Inicia a captura em segundo plano se ainda não estiver ativa."""
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
        cmd = [
            "pw-record",
            "--rate", str(self.taxa),
            "--channels", "1",
            "--format", "s16",
            "--latency", "32ms",
            "--target", target,
            "-",
        ]
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

                # 4 Bandas:
                # b0: 62Hz - 220Hz (bins 2-7) - Graves/Kick/Bass
                # b1: 250Hz - 700Hz (bins 8-22) - Médios-graves/Voz
                # b2: 720Hz - 1750Hz (bins 23-55) - Médios-agudos/Ataque
                # b3: 1750Hz - 3600Hz (bins 56-115) - Agudos/Pratos/Hi-hats
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
        """Encerra a captura de áudio e descarrega processos e recursos."""
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

    def obter_bandas(self) -> Tuple[float, float, float, float]:
        """Retorna os níveis normalizados [0.16..1.0] das 4 bandas em formato thread-safe."""
        with self._lock:
            return self._bandas

    @property
    def ativo_com_som(self) -> bool:
        with self._lock:
            return self._ativo_com_som
