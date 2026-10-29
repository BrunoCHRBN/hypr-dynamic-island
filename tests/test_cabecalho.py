"""Testes unitários para o gerenciador de slots do cabeçalho da Dynamic Island."""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from island.core.atividade import Atividade, SlotItem, PRIORIDADE_MIDIA, PRIORIDADE_HUD, PRIORIDADE_TIMER
from island.core.cena import resolver_cena
from island.ui.cabecalho import CabecalhoSlots


class TestCabecalho(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Inicializa GTK sem abrir janelas
        Gtk.init()

    def test_aplicar_cena_repouso(self):
        cabecalho = CabecalhoSlots()
        cena = resolver_cena([])
        cabecalho.aplicar_cena(cena)

        self.assertFalse(cabecalho.box_leading.get_visible())
        self.assertFalse(cabecalho.box_center.get_visible())
        self.assertFalse(cabecalho.box_trailing.get_visible())
        self.assertFalse(cabecalho.barra_progresso.get_visible())

    def test_aplicar_cena_simples(self):
        cabecalho = CabecalhoSlots()
        midia = Atividade(
            id="midia",
            prioridade=PRIORIDADE_MIDIA,
            leading=SlotItem(tipo="icone", valor="󰎆"),
            center=SlotItem(tipo="texto", valor="Música Ativa"),
            trailing=SlotItem(tipo="equalizador", valor=" ▂▃"),
            progresso=0.5,
        )
        cena = resolver_cena([midia])
        cabecalho.aplicar_cena(cena)

        self.assertTrue(cabecalho.box_leading.get_visible())
        self.assertTrue(cabecalho.box_center.get_visible())
        self.assertTrue(cabecalho.box_trailing.get_visible())
        self.assertTrue(cabecalho.barra_progresso.get_visible())
        self.assertAlmostEqual(cabecalho.barra_progresso.get_fraction(), 0.5)

    def test_aplicar_cena_transitoria_hud(self):
        cabecalho = CabecalhoSlots()
        hud = Atividade(
            id="hud_volume",
            prioridade=PRIORIDADE_HUD,
            transitoria=True,
            leading=SlotItem(tipo="icone", valor="󰕾"),
            center=SlotItem(tipo="texto", valor="Volume 70%"),
            progresso=0.7,
        )
        cena = resolver_cena([hud])
        cabecalho.aplicar_cena(cena)

        self.assertTrue(cabecalho.box_leading.get_visible())
        self.assertTrue(cabecalho.box_center.get_visible())
        self.assertTrue(cabecalho.barra_progresso.get_visible())
        self.assertAlmostEqual(cabecalho.barra_progresso.get_fraction(), 0.7)


if __name__ == "__main__":
    unittest.main()
