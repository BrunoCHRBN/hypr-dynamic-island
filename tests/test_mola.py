"""Testes unitários para a classe Mola (oscilador harmônico amortecido)."""

import unittest
import math
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from island.core.mola import Mola


class TestMola(unittest.TestCase):

    def test_inicializacao(self):
        m = Mola(32.0, resposta=0.42, amortecimento=0.75)
        self.assertEqual(m.x, 32.0)
        self.assertEqual(m.alvo, 32.0)
        self.assertEqual(m.v, 0.0)
        # Já está no alvo
        self.assertTrue(m.passo(0.016))

    def test_convergencia_expansao(self):
        m = Mola(32.0, resposta=0.42, amortecimento=0.75)
        m.ir_para(300.0)
        self.assertFalse(m.passo(0.0))  # Não assentou ainda

        dt = 1.0 / 144.0
        frames = 0
        max_x = 32.0
        while not m.passo(dt):
            frames += 1
            if m.x > max_x:
                max_x = m.x
            self.assertLess(frames, 300, "Mola não convergiu a tempo")

        # Verifica assentamento no alvo
        self.assertEqual(m.x, 300.0)
        self.assertEqual(m.v, 0.0)
        # Overshoot esperado em torno de ~2.4% (entre 1.5% e 3.5%)
        overshoot_pct = ((max_x - 300.0) / (300.0 - 32.0)) * 100.0
        self.assertGreater(overshoot_pct, 1.5)
        self.assertLess(overshoot_pct, 3.5)

    def test_interrupcao_sem_tranco(self):
        """Ao mudar de alvo no meio da trajetória, a velocidade deve ser preservada (C1 contínuo)."""
        m = Mola(32.0, resposta=0.42, amortecimento=0.75)
        m.ir_para(300.0)
        dt = 1.0 / 144.0

        # Avança 15 frames (~104 ms)
        for _ in range(15):
            m.passo(dt)

        vel_antes = m.v
        pos_antes = m.x
        self.assertGreater(vel_antes, 100.0, "Velocidade deve ser positiva durante expansão rápida")

        # Interrupção brusca: usuário sai do hover, alvo volta para 32
        m.reconfigurar(resposta=0.32, amortecimento=0.92)
        m.ir_para(32.0)

        # No próximo passo, a posição não deve teleportar e a velocidade deve desacelerar continuamente
        m.passo(dt)
        self.assertAlmostEqual(m.x, pos_antes + vel_antes * dt, delta=20.0)

        # Deve convergir de volta para 32.0
        frames_retorno = 0
        while not m.passo(dt):
            frames_retorno += 1
            self.assertLess(frames_retorno, 300)

        self.assertEqual(m.x, 32.0)
        self.assertEqual(m.v, 0.0)

    def test_mola_volume_snappy(self):
        """Verifica a convergência rápida e elástica da mola de volume a 144Hz."""
        m_vol = Mola(0.50, resposta=0.18, amortecimento=0.72)
        m_vol.ir_para(0.80)
        dt = 1.0 / 144.0
        frames = 0
        while not m_vol.passo(dt):
            frames += 1
            self.assertLess(frames, 60, "Mola de volume deve assentar em menos de ~300ms a 144Hz")
        self.assertEqual(m_vol.x, 0.80)

    def test_interpolacao_raio_borda(self):
        """Verifica que a fórmula de interpolação do raio mapeia perfeitamente 17px -> 28px."""
        def calc_raio(progresso: float) -> int:
            p = max(0.0, min(1.0, progresso))
            return max(14, min(28, round(17.0 + 11.0 * p)))

        self.assertEqual(calc_raio(0.0), 17)
        self.assertEqual(calc_raio(1.0), 28)
        self.assertEqual(calc_raio(0.5), 22)
        # Monotônico crescente
        valores = [calc_raio(i / 100.0) for i in range(101)]
        for i in range(len(valores) - 1):
            self.assertLessEqual(valores[i], valores[i + 1])

    def test_protecao_engasgo_dt_grande(self):
        """Um congelamento longo (ex: 500ms) não deve explodir a física (clamp em 1/30)."""
        m = Mola(100.0, resposta=0.42, amortecimento=0.75)
        m.ir_para(400.0)
        m.passo(0.5)  # 500ms de uma vez
        self.assertFalse(math.isnan(m.x))
        self.assertFalse(math.isinf(m.x))
        self.assertFalse(math.isnan(m.v))
        self.assertFalse(math.isinf(m.v))
        self.assertLess(m.x, 500.0)


if __name__ == "__main__":
    unittest.main()
