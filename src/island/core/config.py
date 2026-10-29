"""Carregador de configuração com validação, padrões e suporte a Quick Toggles dinâmicos."""

from __future__ import annotations
import os
import tomllib
from typing import Dict, Any, List

CONFIG_PADRAO: Dict[str, Any] = {
    "geral": {
        "monitor": "DP-2",
        "margem_topo": 8,
        "animacao_ms": 220,
        "opacidade_expandida": 0.94,
    },
    "clima": {
        "cidade": "Recife",
        "intervalo_min": 30,
    },
    "rede": {
        "vpn_conexao": "Cloudflare-WARP",
        "adguard_url": "http://127.0.0.1:3000",
    },
    "audio": {
        "sink_monitor": "hdmi-stereo",
        "sink_fones": "analog-stereo",
    },
    "mpris": {
        "resync_intervalo_s": 5.0,
    },
    "toggles": [
        {
            "nome": "VPN",
            "icone": "󰖂",
            "estado": "nmcli -t -f NAME connection show --active | grep -q Cloudflare-WARP",
            "ligar": "nmcli connection up Cloudflare-WARP",
            "desligar": "nmcli connection down Cloudflare-WARP",
        },
        {
            "nome": "Hotspot",
            "icone": "󰤨",
            "estado": "nmcli -t -f NAME connection show --active | grep -q Hotspot",
            "ligar": "nmcli connection up Hotspot",
            "desligar": "nmcli connection down Hotspot",
        }
    ]
}


def carregar_config(caminho: str | None = None) -> Dict[str, Any]:
    """Carrega configuração de arquivo TOML mesclando com os padrões."""
    caminho_final = caminho or os.path.expanduser("~/.config/island/config.toml")
    cfg = dict(CONFIG_PADRAO)

    if os.path.isfile(caminho_final):
        try:
            with open(caminho_final, "rb") as f:
                dados_lidos = tomllib.load(f)
                for secao, valores in dados_lidos.items():
                    if isinstance(valores, dict) and secao in cfg:
                        cfg[secao] = {**cfg[secao], **valores}
                    else:
                        cfg[secao] = valores
        except Exception:
            pass

    return cfg
