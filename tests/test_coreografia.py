"""Testes unitários para a coreografia de opacidade escalonada e mola de volume."""

import unittest
import sys
import os
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from island.core.mola import Mola


class MockIlhaCoreografia:
    """Mock simplificado que espelha os widgets e a lógica de _coreografar_progresso da Ilha."""

    def __init__(self):
        self._expandido = False
        self.cabecalho = Gtk.Box()
        self.revelador = Gtk.Revealer()
        self.revelador.set_visible(False)
        self.hero_card = Gtk.Box()
        self.hero_topo = Gtk.Box()
        self.hero_activity_banner = Gtk.Box()
        self.linha_progresso = Gtk.Box()
        self.controles = Gtk.Box()
        self.page_dots_box = Gtk.Box()
        self._mola_vol = Mola(0.0, resposta=0.22, amortecimento=0.82)
        self.hud_barra = Gtk.ProgressBar()

    def _coreografar_progresso(self, progresso: float) -> None:
        p = max(0.0, min(1.0, progresso))
        expandindo = bool(self._expandido)

        if hasattr(self, "cabecalho"):
            if expandindo:
                op_cab = max(0.0, min(1.0, 1.0 - (p / 0.25)))
            else:
                op_cab = max(0.0, min(1.0, 1.0 - (p / 0.20)))
            self.cabecalho.set_opacity(op_cab)
            self.cabecalho.set_can_target(op_cab > 0.5)

        if hasattr(self, "revelador"):
            if p > 0.02 and not self.revelador.get_visible():
                self.revelador.set_visible(True)
            elif p <= 0.01 and not expandindo and self.revelador.get_visible():
                self.revelador.set_visible(False)

        if hasattr(self, "hero_card"):
            if expandindo:
                op_hero = max(0.0, min(1.0, (p - 0.22) / 0.65))
                op_topo = max(0.0, min(1.0, (p - 0.30) / 0.40))
                op_prog = max(0.0, min(1.0, (p - 0.38) / 0.40))
                op_ctrl = max(0.0, min(1.0, (p - 0.46) / 0.40))
                op_dots = max(0.0, min(1.0, (p - 0.75) / 0.20))
            else:
                op_hero = max(0.0, min(1.0, (p - 0.15) / 0.60))
                op_dots = max(0.0, min(1.0, (p - 0.80) / 0.15))
                op_ctrl = max(0.0, min(1.0, (p - 0.55) / 0.25))
                op_prog = max(0.0, min(1.0, (p - 0.40) / 0.25))
                op_topo = max(0.0, min(1.0, (p - 0.20) / 0.30))

            self.hero_card.set_opacity(op_hero)

            if hasattr(self, "hero_topo"):
                self.hero_topo.set_opacity(op_topo)
            if hasattr(self, "hero_activity_banner"):
                self.hero_activity_banner.set_opacity(op_topo)
            if hasattr(self, "linha_progresso"):
                self.linha_progresso.set_opacity(op_prog)
            if hasattr(self, "controles"):
                self.controles.set_opacity(op_ctrl)
            if hasattr(self, "page_dots_box"):
                self.page_dots_box.set_opacity(op_dots)


class TestCoreografiaEVol(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def test_coreografia_expansao(self):
        ilha = MockIlhaCoreografia()
        ilha._expandido = True

        # No início (p = 0.0): cabeçalho opaco, hero invisível
        ilha._coreografar_progresso(0.0)
        self.assertAlmostEqual(ilha.cabecalho.get_opacity(), 1.0)
        self.assertTrue(ilha.cabecalho.get_can_target())
        self.assertAlmostEqual(ilha.hero_card.get_opacity(), 0.0)
        self.assertFalse(ilha.revelador.get_visible())

        # p = 0.15: cabeçalho desaparecendo suavemente, hero ainda invisível
        ilha._coreografar_progresso(0.15)
        self.assertTrue(0.0 < ilha.cabecalho.get_opacity() < 1.0)
        self.assertAlmostEqual(ilha.hero_card.get_opacity(), 0.0)
        self.assertTrue(ilha.revelador.get_visible())

        # p = 0.25: cabeçalho completamente oculto e intocável
        ilha._coreografar_progresso(0.25)
        self.assertAlmostEqual(ilha.cabecalho.get_opacity(), 0.0)
        self.assertFalse(ilha.cabecalho.get_can_target())

        # p = 0.35: hero_topo começou a entrar, controles e dots ainda não
        ilha._coreografar_progresso(0.35)
        self.assertGreater(ilha.hero_topo.get_opacity(), 0.0)
        self.assertAlmostEqual(ilha.linha_progresso.get_opacity(), 0.0)
        self.assertAlmostEqual(ilha.controles.get_opacity(), 0.0)
        self.assertAlmostEqual(ilha.page_dots_box.get_opacity(), 0.0)

        # p = 0.50: topo e linha avançados, controles entrando
        ilha._coreografar_progresso(0.50)
        self.assertGreater(ilha.hero_topo.get_opacity(), 0.4)
        self.assertGreater(ilha.linha_progresso.get_opacity(), 0.2)
        self.assertGreater(ilha.controles.get_opacity(), 0.05)
        self.assertAlmostEqual(ilha.page_dots_box.get_opacity(), 0.0)

        # p = 0.88: quase tudo 100% visível, dots entrando
        ilha._coreografar_progresso(0.88)
        self.assertAlmostEqual(ilha.hero_topo.get_opacity(), 1.0)
        self.assertAlmostEqual(ilha.linha_progresso.get_opacity(), 1.0)
        self.assertAlmostEqual(ilha.controles.get_opacity(), 1.0)
        self.assertGreater(ilha.page_dots_box.get_opacity(), 0.5)

        # p = 1.0: tudo 100% visível
        ilha._coreografar_progresso(1.0)
        self.assertAlmostEqual(ilha.hero_card.get_opacity(), 1.0)
        self.assertAlmostEqual(ilha.page_dots_box.get_opacity(), 1.0)

    def test_coreografia_recolhimento(self):
        ilha = MockIlhaCoreografia()
        ilha._expandido = False

        # p = 0.90: ao recolher, os dots saem primeiro
        ilha._coreografar_progresso(0.90)
        self.assertLess(ilha.page_dots_box.get_opacity(), 1.0)
        self.assertAlmostEqual(ilha.controles.get_opacity(), 1.0)

        # p = 0.70: controles e linha saindo, topo ainda visível
        ilha._coreografar_progresso(0.70)
        self.assertAlmostEqual(ilha.page_dots_box.get_opacity(), 0.0)
        self.assertLess(ilha.controles.get_opacity(), 1.0)
        self.assertAlmostEqual(ilha.hero_topo.get_opacity(), 1.0)

        # p = 0.10: hero saindo, cabeçalho reaparecendo
        ilha._coreografar_progresso(0.10)
        self.assertGreater(ilha.cabecalho.get_opacity(), 0.4)
        self.assertAlmostEqual(ilha.hero_topo.get_opacity(), 0.0)

        # p = 0.00: cabeçalho totalmente restaurado e revelador oculto
        ilha._coreografar_progresso(0.00)
        self.assertAlmostEqual(ilha.cabecalho.get_opacity(), 1.0)
        self.assertTrue(ilha.cabecalho.get_can_target())
        self.assertFalse(ilha.revelador.get_visible())

    def test_mola_volume_fluida(self):
        mola = Mola(0.0, resposta=0.22, amortecimento=0.82)
        mola.ir_para(0.75)

        # Simula alguns passos a 144Hz (6.94ms)
        dt = 0.00694
        for _ in range(20):
            mola.passo(dt)

        # Deve avançar de forma contínua em direção a 0.75
        self.assertGreater(mola.x, 0.40)
        self.assertLess(mola.x, 0.85)

        # Continua até assentar
        while not mola.passo(dt):
            pass
        self.assertAlmostEqual(mola.x, 0.75, places=2)


if __name__ == "__main__":
    unittest.main()
