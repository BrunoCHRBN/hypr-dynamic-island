"""Fonte de métricas do sistema (CPU, RAM, Temperatura) com amostragem sob demanda."""

from __future__ import annotations
import glob
import os
import threading
import time
from typing import Tuple, Optional
from .base import Fonte


class FonteSistema(Fonte):
    def __init__(self, intervalo_s: float = 2.0) -> None:
        super().__init__()
        self.intervalo_s = intervalo_s
        self._ant = self._leitura_cpu()
        self._caminho_temp: Optional[str] = None
        self._descobrir_sensor_temp()
        self._thread: Optional[threading.Thread] = None
        self._parar_evento = threading.Event()
        self._ultimo_cpu = 0
        self._ultimo_mem = 0
        self._ultimo_temp: Optional[int] = None

    def ao_ativar(self) -> None:
        self._parar_evento.clear()
        self._thread = threading.Thread(target=self._loop_monitoramento, daemon=True)
        self._thread.start()

    def ao_desativar(self) -> None:
        self._parar_evento.set()
        self._thread = None

    def _loop_monitoramento(self) -> None:
        while not self._parar_evento.is_set():
            c = self.cpu()
            m = self.mem()
            t = self.temp_cpu()
            self._ultimo_cpu = c
            self._ultimo_mem = m
            self._ultimo_temp = t
            self.notificar(c, m, t)
            self._parar_evento.wait(self.intervalo_s)

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

    def temp_cpu(self) -> Optional[int]:
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
    def _leitura_cpu() -> Tuple[int, int]:
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
            return sum(v), v[3] + (v[4] if len(v) > 4 else 0)
        except Exception:
            return 0, 0

    def cpu(self) -> int:
        total, ocioso = self._leitura_cpu()
        dt, do = total - self._ant[0], ocioso - self._ant[1]
        self._ant = (total, ocioso)
        return 0 if dt <= 0 else round(100 * (dt - do) / dt)

    @staticmethod
    def mem() -> int:
        info = {}
        try:
            with open("/proc/meminfo") as f:
                for linha in f:
                    k, v = linha.split(":", 1)
                    info[k] = int(v.split()[0])
            return round(100 * (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"])
        except Exception:
            return 0
