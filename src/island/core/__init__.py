"""Módulo central da engine da Dynamic Island."""

from .atividade import (
    Atividade,
    SlotItem,
    GerenciadorAtividades,
    PRIORIDADE_HUD,
    PRIORIDADE_NOTIFICACAO,
    PRIORIDADE_PRIVACIDADE,
    PRIORIDADE_TIMER,
    PRIORIDADE_GRAVACAO,
    PRIORIDADE_MIDIA,
    PRIORIDADE_IDLE,
)
from .cena import Cena, resolver_cena
from .config import carregar_config, CONFIG_PADRAO
from .mola import Mola
from .ipc import ServidorIPC, SOCKET_PATH

__all__ = [
    "Atividade",
    "SlotItem",
    "GerenciadorAtividades",
    "Cena",
    "resolver_cena",
    "carregar_config",
    "CONFIG_PADRAO",
    "Mola",
    "ServidorIPC",
    "SOCKET_PATH",
    "PRIORIDADE_HUD",
    "PRIORIDADE_NOTIFICACAO",
    "PRIORIDADE_PRIVACIDADE",
    "PRIORIDADE_TIMER",
    "PRIORIDADE_GRAVACAO",
    "PRIORIDADE_MIDIA",
    "PRIORIDADE_IDLE",
]
