"""Classe base abstrata para fontes de dados desacopladas com contagem de referências."""

from __future__ import annotations
import threading
from typing import Callable, Any, Set


class Fonte:
    """Fonte de dados com subscrição reativa e gerenciamento automático de ciclo de vida.
    
    A fonte só consome I/O, timers ou eventos D-Bus quando há pelo menos um ouvinte
    ativo (contador de referências > 0). Quando todos os ouvintes se desinscrevem,
    a fonte entra em modo dormente para economizar CPU e energia.
    """

    def __init__(self) -> None:
        self._ouvintes: Set[Callable[..., None]] = set()
        self._lock = threading.Lock()
        self._ativa = False

    @property
    def esta_ativa(self) -> bool:
        with self._lock:
            return self._ativa

    @property
    def total_assinantes(self) -> int:
        with self._lock:
            return len(self._ouvintes)

    def assinar(self, callback: Callable[..., None]) -> None:
        """Registra um ouvinte. Se for o primeiro, ativa a fonte."""
        deve_ativar = False
        with self._lock:
            self._ouvintes.add(callback)
            if len(self._ouvintes) == 1 and not self._ativa:
                self._ativa = True
                deve_ativar = True

        if deve_ativar:
            self.ao_ativar()

    def desassinar(self, callback: Callable[..., None]) -> None:
        """Remove um ouvinte. Se não restarem ouvintes, desativa a fonte."""
        deve_desativar = False
        with self._lock:
            self._ouvintes.discard(callback)
            if len(self._ouvintes) == 0 and self._ativa:
                self._ativa = False
                deve_desativar = True

        if deve_desativar:
            self.ao_desativar()

    def notificar(self, *args: Any, **kwargs: Any) -> None:
        """Notifica todos os ouvintes registrados."""
        with self._lock:
            copia_ouvintes = list(self._ouvintes)

        for ouvinte in copia_ouvintes:
            try:
                ouvinte(*args, **kwargs)
            except Exception:
                pass

    def ao_ativar(self) -> None:
        """Gancho chamado quando o primeiro ouvinte se registra."""
        pass

    def ao_desativar(self) -> None:
        """Gancho chamado quando o último ouvinte se desinscreve."""
        pass
