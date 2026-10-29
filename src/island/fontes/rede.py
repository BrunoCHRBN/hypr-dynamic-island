"""Fonte de status e controles de rede (Hotspot, VPN, AdGuard)."""

from __future__ import annotations
import json
import os
import subprocess
import threading
import urllib.request
from typing import Optional, Dict, Any
from .base import Fonte


class FonteRede(Fonte):
    def __init__(self, vpn_conexao: str = "Cloudflare-WARP", adguard_url: str = "http://127.0.0.1:3000") -> None:
        super().__init__()
        self.vpn_conexao = vpn_conexao
        self.adguard_url = adguard_url
        self._hotspot_cache = False
        self._adguard_cache = False
        self._atualizando = False

    def ao_ativar(self) -> None:
        self.atualizar_async()

    def hotspot_ativo(self) -> bool:
        return self._hotspot_cache

    def vpn_ativa(self) -> bool:
        return os.path.exists("/sys/class/net/warp0")

    def adguard_ativo(self) -> bool:
        return self._adguard_cache

    def dados(self) -> Dict[str, Any]:
        return {
            "hotspot": self.hotspot_ativo(),
            "vpn": self.vpn_ativa(),
            "adguard": self.adguard_ativo()
        }

    def atualizar_async(self) -> None:
        if self._atualizando:
            return
        self._atualizando = True

        def _worker():
            # 1. Hotspot
            try:
                r = subprocess.run(
                    ["nmcli", "-t", "-f", "NAME", "con", "show", "--active"],
                    capture_output=True, text=True, timeout=1.0
                )
                hotspot = "Hotspot" in r.stdout.splitlines()
            except Exception:
                hotspot = False

            # 2. AdGuard
            try:
                req = urllib.request.Request(
                    f"{self.adguard_url}/control/status",
                    headers={"User-Agent": "island"}
                )
                with urllib.request.urlopen(req, timeout=0.4) as resp:
                    data = json.loads(resp.read().decode())
                    adguard = bool(data.get("protection_enabled", False))
            except Exception:
                adguard = False

            self._hotspot_cache = hotspot
            self._adguard_cache = adguard
            self._atualizando = False
            self.notificar("status", self.dados())

        threading.Thread(target=_worker, daemon=True).start()

    def alternar_hotspot(self) -> None:
        def _fazer():
            try:
                ativo = self._hotspot_cache
                cmd = ["nmcli", "connection", "down" if ativo else "up", "Hotspot"]
                subprocess.run(cmd, capture_output=True, timeout=6)
            except Exception:
                pass
            self.atualizar_async()
        threading.Thread(target=_fazer, daemon=True).start()

    def alternar_vpn(self) -> None:
        def _fazer():
            try:
                ativo = self.vpn_ativa()
                cmd = ["nmcli", "connection", "down" if ativo else "up", self.vpn_conexao]
                subprocess.run(cmd, capture_output=True, timeout=6)
            except Exception:
                pass
            self.atualizar_async()
        threading.Thread(target=_fazer, daemon=True).start()

    def alternar_adguard(self) -> None:
        def _fazer():
            try:
                ativo = self._adguard_cache
                req = urllib.request.Request(
                    f"{self.adguard_url}/control/protection",
                    data=json.dumps({"enabled": not ativo}).encode(),
                    headers={"Content-Type": "application/json", "User-Agent": "island"}
                )
                urllib.request.urlopen(req, timeout=1.0)
            except Exception:
                pass
            self.atualizar_async()
        threading.Thread(target=_fazer, daemon=True).start()
