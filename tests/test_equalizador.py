"""Testes unitários para EqualizadorArea e AnelProgresso."""

import unittest
import sys
import os
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from island.ui.equalizador import EqualizadorArea, AnelProgresso


class TestEqualizadorEAnel(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def test_equalizador_inicializacao(self):
        eq = EqualizadorArea()
        self.assertEqual(len(eq._molas), 4)
        self.assertFalse(eq._ativo)
        self.assertFalse(eq._pausado)

    def test_equalizador_transicao_estados(self):
        eq = EqualizadorArea()
        # Ativa o EQ
        eq.definir_estado(ativo=True, pausado=False, mudo=False)
        self.assertTrue(eq._ativo)
        self.assertFalse(eq._pausado)
        self.assertTrue(eq._capturador._rodando)

        # Pausa o EQ (deve parar capturador de áudio)
        eq.definir_estado(ativo=True, pausado=True, mudo=False)
        self.assertTrue(eq._pausado)
        self.assertFalse(eq._capturador._rodando)
        for m in eq._molas:
            self.assertEqual(m.alvo, 0.20)

        # Mudo (deve parar capturador de áudio)
        eq.definir_estado(ativo=True, pausado=False, mudo=True)
        self.assertTrue(eq._mudo)
        self.assertFalse(eq._capturador._rodando)
        for m in eq._molas:
            self.assertEqual(m.alvo, 0.10)

        # Desativa o EQ completamente
        eq.definir_estado(ativo=False)
        self.assertFalse(eq._ativo)
        self.assertFalse(eq._capturador._rodando)

    def test_equalizador_atualizar_sink(self):
        eq = EqualizadorArea()
        # Não deve falhar quando chamado
        eq.atualizar_sink()
        self.assertTrue(hasattr(eq, "_capturador"))

    def test_anel_progresso(self):
        anel = AnelProgresso(raio=7.0, largura_linha=2.0)
        self.assertFalse(anel.get_visible())

        anel.definir_progresso(0.5)
        self.assertTrue(anel.get_visible())
        self.assertEqual(anel._progresso, 0.5)
        self.assertEqual(anel._mola.alvo, 0.5)

        # Progresso None esconde
        anel.definir_progresso(None)
        self.assertFalse(anel.get_visible())
        self.assertEqual(anel._progresso, 0.0)


if __name__ == "__main__":
    unittest.main()
