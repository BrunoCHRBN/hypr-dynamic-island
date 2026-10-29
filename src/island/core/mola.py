"""Simulação física de molas (damped harmonic oscillator) para animações fluidas."""

from __future__ import annotations
import math


class Mola:
    """Mola física interrompível (oscilador harmônico amortecido).

    Preserva a posição (x) e a velocidade (v) ao mudar de alvo,
    eliminando trancos em reversões bruscas e garantindo continuidade C1.
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
        """Altera o alvo sem zerar a velocidade ou reiniciar a posição."""
        self.alvo = float(alvo)

    def passo(self, dt: float) -> bool:
        """Avança a simulação por dt segundos. Retorna True quando assentou."""
        if dt <= 0:
            return abs(self.x - self.alvo) < 0.25 and abs(self.v) < 1.5
        dt = min(dt, 1.0 / 30.0)  # Protege contra saltos/engasgos de frame
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
