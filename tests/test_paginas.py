"""Testes unitários para o protocolo Pagina e ciclo de vida dos módulos de páginas."""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from island.paginas.base import Pagina
from island.paginas.midia import PaginaMidia
from island.paginas.hub import PaginaHub
from island.paginas.controles import PaginaControles
from island.paginas.historico import PaginaHistorico
from island.fontes.mpris import FonteMpris
from island.fontes.sistema import FonteSistema
from island.fontes.volume import FonteVolume
from island.fontes.rede import FonteRede
from island.fontes.kde import FonteKde
from island.fontes.hypr import FonteHypr


class TestPaginas(unittest.TestCase):

    def setUp(self):
        self.fonte_mpris = FonteMpris()
        self.fonte_sistema = FonteSistema()
        self.fonte_volume = FonteVolume()
        self.fonte_rede = FonteRede()
        self.fonte_kde = FonteKde()
        self.fonte_hypr = FonteHypr()

    def test_conformidade_com_protocolo_pagina(self):
        """Todas as telas devem aderir estritamente ao protocolo Pagina."""
        pag_midia = PaginaMidia(self.fonte_mpris)
        pag_hub = PaginaHub(self.fonte_hypr)
        pag_controles = PaginaControles(self.fonte_sistema, self.fonte_volume, self.fonte_rede, self.fonte_kde)
        pag_hist = PaginaHistorico()

        for pag in (pag_midia, pag_hub, pag_controles, pag_hist):
            self.assertIsInstance(pag, Pagina)
            self.assertTrue(hasattr(pag, "nome"))
            self.assertTrue(hasattr(pag, "titulo"))
            self.assertTrue(hasattr(pag, "icone"))
            self.assertTrue(callable(pag.construir))
            self.assertTrue(callable(pag.ao_mostrar))
            self.assertTrue(callable(pag.ao_esconder))
            self.assertTrue(callable(pag.disponivel))

    def test_ciclo_de_vida_subscricao_fontes(self):
        """ao_mostrar e ao_esconder devem registrar e desregistrar ouvintes nas fontes refcounted."""
        pag_controles = PaginaControles(self.fonte_sistema, self.fonte_volume, self.fonte_rede, self.fonte_kde)

        self.assertFalse(self.fonte_sistema.esta_ativa)
        self.assertEqual(self.fonte_sistema.total_assinantes, 0)

        pag_controles.ao_mostrar()
        self.assertTrue(self.fonte_sistema.esta_ativa)
        self.assertEqual(self.fonte_sistema.total_assinantes, 1)

        pag_controles.ao_esconder()
        self.assertFalse(self.fonte_sistema.esta_ativa)
        self.assertEqual(self.fonte_sistema.total_assinantes, 0)


if __name__ == "__main__":
    unittest.main()
