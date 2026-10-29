"""Função pura de resolução de cena para a Dynamic Island."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List
from .atividade import Atividade, SlotItem, PRIORIDADE_HUD


@dataclass
class Cena:
    """Representação resolvida e imutável do que a interface deve renderizar neste instante."""
    modo: str                                # "repouso", "transitoria", "simples", "dupla"
    largura: int
    altura: int
    leading: Optional[SlotItem] = None
    center: Optional[SlotItem] = None
    trailing: Optional[SlotItem] = None
    progresso: Optional[float] = None
    cor_acento: Optional[tuple[int, int, int]] = None
    atividade_primaria: Optional[Atividade] = None
    atividade_secundaria: Optional[Atividade] = None
    expandido: bool = False
    fixado: bool = False


def resolver_cena(
    atividades: List[Atividade],
    expandido: bool = False,
    fixado: bool = False,
    altura_base: int = 34,
    altura_expandida: int = 220,
    largura_maxima: int = 440,
    largura_idle: int = 200,
) -> Cena:
    """Resolve deterministicamente a cena com base nas atividades ativas.
    
    Esta é uma função puramente computacional, sem efeitos colaterais de UI ou I/O,
    tornando o estado da ilha 100% previsível e testável.
    """
    altura = altura_expandida if expandido else altura_base

    # Caso 1: Nenhuma atividade ativa -> Cena de repouso (workspaces, relógio)
    if not atividades:
        return Cena(
            modo="repouso",
            largura=largura_maxima if expandido else largura_idle,
            altura=altura,
            leading=None,
            center=None,
            trailing=None,
            progresso=None,
            cor_acento=None,
            atividade_primaria=None,
            atividade_secundaria=None,
            expandido=expandido,
            fixado=fixado,
        )

    # Ordena decrescente por prioridade
    ordenadas = sorted(atividades, key=lambda a: a.prioridade, reverse=True)
    primaria = ordenadas[0]

    # Caso 2: Atividade transitória de cobertura total (ex: HUD de volume)
    if primaria.transitoria or primaria.prioridade >= PRIORIDADE_HUD:
        return Cena(
            modo="transitoria",
            largura=primaria.largura_alvo,
            altura=altura_base,      # HUD transitório não expande
            leading=primaria.leading,
            center=primaria.center,
            trailing=primaria.trailing,
            progresso=primaria.progresso,
            cor_acento=primaria.cor_acento,
            atividade_primaria=primaria,
            atividade_secundaria=None,
            expandido=False,
            fixado=fixado,
        )

    # Caso 3: Apenas 1 atividade ativa ou modo expandido
    if len(ordenadas) == 1 or expandido:
        largura = largura_maxima if expandido else primaria.largura_alvo
        return Cena(
            modo="simples",
            largura=largura,
            altura=altura,
            leading=primaria.leading,
            center=primaria.center,
            trailing=primaria.trailing,
            progresso=primaria.progresso,
            cor_acento=primaria.cor_acento,
            atividade_primaria=primaria,
            atividade_secundaria=None,
            expandido=expandido,
            fixado=fixado,
        )

    # Caso 4: Duas atividades ativas simultâneas (ex: Mídia + Timer)
    secundaria = ordenadas[1]
    trailing_item = secundaria.trailing or secundaria.leading
    largura_combinada = max(primaria.largura_alvo, 300)

    return Cena(
        modo="dupla",
        largura=largura_combinada,
        altura=altura,
        leading=primaria.leading,
        center=primaria.center,
        trailing=trailing_item,
        progresso=primaria.progresso,
        cor_acento=primaria.cor_acento,
        atividade_primaria=primaria,
        atividade_secundaria=secundaria,
        expandido=False,
        fixado=fixado,
    )
