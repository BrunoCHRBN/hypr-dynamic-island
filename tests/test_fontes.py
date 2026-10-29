"""Testes unitários para o sistema de fontes desacopladas com contagem de referências."""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from island.fontes.base import Fonte
from island.fontes.sistema import FonteSistema


class FonteTeste(Fonte):
    def __init__(self):
        super().__init__()
        self.ativada_count = 0
        self.desativada_count = 0

    def ao_ativar(self):
        self.ativada_count += 1

    def ao_desativar(self):
        self.desativada_count += 1


class TestFontes(unittest.TestCase):

    def test_refcount_ativacao_e_desativacao(self):
        fonte = FonteTeste()
        self.assertFalse(fonte.esta_ativa)
        self.assertEqual(fonte.total_assinantes, 0)

        # Primeiro ouvinte registra -> ativa
        cb1 = lambda *args: None
        fonte.assinar(cb1)
        self.assertTrue(fonte.esta_ativa)
        self.assertEqual(fonte.total_assinantes, 1)
        self.assertEqual(fonte.ativada_count, 1)

        # Segundo ouvinte registra -> continua ativa sem reativar
        cb2 = lambda *args: None
        fonte.assinar(cb2)
        self.assertTrue(fonte.esta_ativa)
        self.assertEqual(fonte.total_assinantes, 2)
        self.assertEqual(fonte.ativada_count, 1)

        # Remove primeiro ouvinte -> continua ativa
        fonte.desassinar(cb1)
        self.assertTrue(fonte.esta_ativa)
        self.assertEqual(fonte.total_assinantes, 1)
        self.assertEqual(fonte.desativada_count, 0)

        # Remove segundo ouvinte -> desativa (refcount 0)
        fonte.desassinar(cb2)
        self.assertFalse(fonte.esta_ativa)
        self.assertEqual(fonte.total_assinantes, 0)
        self.assertEqual(fonte.desativada_count, 1)

    def test_notificacao_ouvintes(self):
        fonte = FonteTeste()
        recebido = []

        def ouvinte(dado):
            recebido.append(dado)

        fonte.assinar(ouvinte)
        fonte.notificar("evento_teste")
        self.assertEqual(recebido, ["evento_teste"])

        fonte.desassinar(ouvinte)
        fonte.notificar("outro_evento")
        # Não deve receber nada após desassinar
        self.assertEqual(recebido, ["evento_teste"])

    def test_fonte_sistema_leitura(self):
        fonte_sis = FonteSistema(intervalo_s=10.0)
        # Leituras brutas de /proc devem funcionar sem lançar exceções
        cpu = fonte_sis.cpu()
        mem = fonte_sis.mem()
        self.assertIsInstance(cpu, int)
        self.assertIsInstance(mem, int)
        self.assertGreaterEqual(cpu, 0)
        self.assertGreaterEqual(mem, 0)


if __name__ == "__main__":
    unittest.main()
