"""Modelo canônico de Atividades com priorização e ciclo de vida."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any, Dict, List, Callable

# Prioridades canônicas (maior número = maior precedência)
PRIORIDADE_HUD = 90
PRIORIDADE_NOTIFICACAO = 80
PRIORIDADE_PRIVACIDADE = 70
PRIORIDADE_TIMER = 50
PRIORIDADE_GRAVACAO = 40
PRIORIDADE_MIDIA = 30
PRIORIDADE_IDLE = 10


@dataclass
class SlotItem:
    """Conteúdo destinado a um dos slots visuais do cabeçalho (leading, center ou trailing)."""
    tipo: str               # "texto", "icone", "markup", "capa", "equalizador", "widget"
    valor: Any = None       # String de texto, markup Pango, objeto Texture ou widget callback
    css_class: str = ""
    tooltip: str = ""


@dataclass
class Atividade:
    """Unidade atômica de informação que disputa espaço na Dynamic Island."""
    id: str
    prioridade: int
    ttl_ms: int = 0                          # >0: transitória, expira automaticamente
    transitoria: bool = False                # Se True, cobre a ilha por completo temporariamente
    leading: Optional[SlotItem] = None
    center: Optional[SlotItem] = None
    trailing: Optional[SlotItem] = None
    progresso: Optional[float] = None        # Fração de progresso (0.0 a 1.0)
    largura_alvo: int = 260
    cor_acento: Optional[tuple[int, int, int]] = None
    dados: Dict[str, Any] = field(default_factory=dict)


class GerenciadorAtividades:
    """Gerencia a lista de atividades ativas com notificações para ouvintes."""

    def __init__(self) -> None:
        self._atividades: Dict[str, Atividade] = {}
        self._observadores: List[Callable[[], None]] = []

    def observar(self, callback: Callable[[], None]) -> None:
        self._observadores.append(callback)

    def _notificar(self) -> None:
        for obs in self._observadores:
            try:
                obs()
            except Exception:
                pass

    def adicionar(self, atividade: Atividade) -> None:
        self._atividades[atividade.id] = atividade
        self._notificar()

    def remover(self, id_atividade: str) -> Optional[Atividade]:
        removida = self._atividades.pop(id_atividade, None)
        if removida:
            self._notificar()
        return removida

    def obter(self, id_atividade: str) -> Optional[Atividade]:
        return self._atividades.get(id_atividade)

    def todas(self) -> List[Atividade]:
        """Retorna todas as atividades ordenadas por prioridade decrescente."""
        return sorted(self._atividades.values(), key=lambda a: a.prioridade, reverse=True)

    def limpar(self) -> None:
        if self._atividades:
            self._atividades.clear()
            self._notificar()
