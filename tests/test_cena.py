"""Testes unitários para o modelo de Atividades e a função pura resolver_cena."""

import unittest
import sys
import os

# Adiciona src ao path para permitir imports de island
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from island.core.atividade import (
    Atividade,
    SlotItem,
    GerenciadorAtividades,
    PRIORIDADE_HUD,
    PRIORIDADE_NOTIFICACAO,
    PRIORIDADE_TIMER,
    PRIORIDADE_MIDIA,
)
from island.core.cena import resolver_cena


class TestCena(unittest.TestCase):

    def test_repouso_sem_atividades(self):
        """Sem atividades ativas, a ilha deve retornar a cena de repouso compacta."""
        cena = resolver_cena([], expandido=False, fixado=False)
        self.assertEqual(cena.modo, "repouso")
        self.assertEqual(cena.largura, 200)
        self.assertEqual(cena.altura, 34)
        self.assertIsNone(cena.leading)
        self.assertIsNone(cena.trailing)
        self.assertIsNone(cena.progresso)

    def test_atividade_simples_midia(self):
        """Com apenas mídia tocando, exibe capa no leading, título no center e equalizador no trailing."""
        midia = Atividade(
            id="midia",
            prioridade=PRIORIDADE_MIDIA,
            leading=SlotItem(tipo="capa", valor="cover.png"),
            center=SlotItem(tipo="texto", valor="Música de Teste"),
            trailing=SlotItem(tipo="equalizador", valor=" ▂▃"),
            progresso=0.45,
            largura_alvo=260,
        )
        cena = resolver_cena([midia], expandido=False)
        self.assertEqual(cena.modo, "simples")
        self.assertEqual(cena.largura, 260)
        self.assertEqual(cena.leading.tipo, "capa")
        self.assertEqual(cena.center.valor, "Música de Teste")
        self.assertEqual(cena.trailing.tipo, "equalizador")
        self.assertEqual(cena.progresso, 0.45)

    def test_atividade_transitoria_hud_cobre_midia(self):
        """Um HUD de volume (prioridade 90) deve sobrepor a mídia (prioridade 30)."""
        midia = Atividade(
            id="midia",
            prioridade=PRIORIDADE_MIDIA,
            leading=SlotItem(tipo="capa", valor="cover.png"),
            center=SlotItem(tipo="texto", valor="Música de Teste"),
            trailing=SlotItem(tipo="equalizador", valor=" ▂▃"),
            largura_alvo=260,
        )
        hud = Atividade(
            id="hud_volume",
            prioridade=PRIORIDADE_HUD,
            ttl_ms=1500,
            transitoria=True,
            leading=SlotItem(tipo="icone", valor="󰕾"),
            center=SlotItem(tipo="texto", valor="Volume 65%"),
            trailing=SlotItem(tipo="texto", valor="65%"),
            progresso=0.65,
            largura_alvo=260,
        )
        cena = resolver_cena([midia, hud], expandido=False)
        self.assertEqual(cena.modo, "transitoria")
        self.assertEqual(cena.atividade_primaria.id, "hud_volume")
        self.assertEqual(cena.leading.valor, "󰕾")
        self.assertEqual(cena.center.valor, "Volume 65%")
        self.assertEqual(cena.progresso, 0.65)

    def test_restauracao_apos_expirar_hud(self):
        """Quando o HUD é removido, a cena volta deterministicamente para a mídia sem congelamento."""
        gerenciador = GerenciadorAtividades()
        midia = Atividade(
            id="midia",
            prioridade=PRIORIDADE_MIDIA,
            leading=SlotItem(tipo="capa", valor="cover.png"),
            center=SlotItem(tipo="texto", valor="Música de Teste"),
            trailing=SlotItem(tipo="equalizador", valor=" ▂▃"),
            largura_alvo=260,
        )
        hud = Atividade(
            id="hud_volume",
            prioridade=PRIORIDADE_HUD,
            ttl_ms=1500,
            transitoria=True,
            leading=SlotItem(tipo="icone", valor="󰕾"),
        )
        gerenciador.adicionar(midia)
        gerenciador.adicionar(hud)

        # Durante o HUD:
        cena_durante = resolver_cena(gerenciador.todas())
        self.assertEqual(cena_durante.modo, "transitoria")
        self.assertEqual(cena_durante.atividade_primaria.id, "hud_volume")

        # Após expirar e ser removido:
        gerenciador.remover("hud_volume")
        cena_apos = resolver_cena(gerenciador.todas())
        self.assertEqual(cena_apos.modo, "simples")
        self.assertEqual(cena_apos.atividade_primaria.id, "midia")
        self.assertEqual(cena_apos.leading.tipo, "capa")

    def test_duas_atividades_simultaneas_midia_e_timer(self):
        """Mídia + Timer ativos simultaneamente devem formar cena 'dupla' com slots compartilhados."""
        midia = Atividade(
            id="midia",
            prioridade=PRIORIDADE_MIDIA,
            leading=SlotItem(tipo="capa", valor="cover.png"),
            center=SlotItem(tipo="texto", valor="Música"),
            trailing=SlotItem(tipo="equalizador", valor=" ▂▃"),
            largura_alvo=260,
        )
        timer = Atividade(
            id="timer",
            prioridade=PRIORIDADE_TIMER,
            leading=SlotItem(tipo="icone", valor="󰔟"),
            center=SlotItem(tipo="texto", valor="Pomodoro"),
            trailing=SlotItem(tipo="texto", valor="14:52"),
            largura_alvo=260,
        )
        cena = resolver_cena([midia, timer], expandido=False)
        self.assertEqual(cena.modo, "dupla")
        # O Timer tem prioridade maior (50 > 30), portanto é o primário
        self.assertEqual(cena.atividade_primaria.id, "timer")
        self.assertEqual(cena.atividade_secundaria.id, "midia")
        # Leading vem do primário, Trailing vem do secundário
        self.assertEqual(cena.leading.valor, "󰔟")
        self.assertEqual(cena.trailing.tipo, "equalizador")
        self.assertGreaterEqual(cena.largura, 300)

    def test_modo_expandido(self):
        """Quando a ilha é expandida pelo usuário, a cena reflete a altura alvo e largura total."""
        midia = Atividade(
            id="midia",
            prioridade=PRIORIDADE_MIDIA,
            leading=SlotItem(tipo="capa", valor="cover.png"),
            center=SlotItem(tipo="texto", valor="Música"),
        )
        cena = resolver_cena([midia], expandido=True)
        self.assertTrue(cena.expandido)
        self.assertEqual(cena.altura, 220)
        self.assertEqual(cena.largura, 440)


if __name__ == "__main__":
    unittest.main()
