"""Testes unitários para CapturadorEspectro (análise espectral FFT e integração)."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from island.fontes.espectro import CapturadorEspectro


class TestCapturadorEspectroMatematica(unittest.TestCase):
    """Testa os fundamentos matemáticos da FFT, janelamento e bandas de frequência."""

    def setUp(self):
        self.taxa = 8000
        self.n = 256
        self.capturador = CapturadorEspectro(taxa=self.taxa, n_amostras=self.n)

    def test_bit_reversal_e_tamanho(self):
        """Verifica se a tabela de reversão de bits cobre perfeitamente 0..N-1."""
        self.assertEqual(len(self.capturador._rev), self.n)
        self.assertEqual(set(self.capturador._rev), set(range(self.n)))

    def test_janela_hanning(self):
        """Verifica os coeficientes da janela de Hanning pré-calculada."""
        w = self.capturador._window
        self.assertEqual(len(w), self.n)
        self.assertAlmostEqual(w[0], 0.0, places=4)
        self.assertAlmostEqual(w[self.n - 1], 0.0, places=4)
        meio = self.n // 2
        self.assertAlmostEqual(w[meio], 1.0, delta=0.01)

    def test_fft_senoide_conhecida_250hz(self):
        """Verifica se a FFT detecta com precisão uma frequência pura de 250 Hz (bin 8)."""
        freq = 250.0  # 250 Hz / (8000 / 256) = bin 8
        sinal = [math.sin(2.0 * math.pi * freq * i / self.taxa) for i in range(self.n)]
        windowed = [sinal[i] * self.capturador._window[i] for i in range(self.n)]
        spec = self.capturador._fft(windowed)
        mags = [abs(spec[k]) for k in range(self.n // 2)]

        bin_pico = max(range(len(mags)), key=lambda k: mags[k])
        self.assertEqual(bin_pico, 8)

    def test_fft_senoide_conhecida_1000hz(self):
        """Verifica se a FFT detecta com precisão uma frequência de 1000 Hz (bin 32)."""
        freq = 1000.0  # 1000 Hz / (8000 / 256) = bin 32
        sinal = [math.sin(2.0 * math.pi * freq * i / self.taxa) for i in range(self.n)]
        windowed = [sinal[i] * self.capturador._window[i] for i in range(self.n)]
        spec = self.capturador._fft(windowed)
        mags = [abs(spec[k]) for k in range(self.n // 2)]

        bin_pico = max(range(len(mags)), key=lambda k: mags[k])
        self.assertEqual(bin_pico, 32)


class TestCapturadorEspectroLifecycle(unittest.TestCase):
    """Testa o ciclo de vida, estado inicial, encerramento e robustez do CapturadorEspectro."""

    def test_estado_inicial(self):
        capturador = CapturadorEspectro()
        self.assertFalse(capturador._rodando)
        self.assertFalse(capturador.rodando)
        self.assertFalse(capturador.ativo_com_som)
        bandas = capturador.obter_bandas()
        self.assertEqual(len(bandas), 4)
        self.assertEqual(bandas, (0.16, 0.16, 0.16, 0.16))

    def test_obter_sink_monitor_retorna_monitor(self):
        capturador = CapturadorEspectro()
        sink = capturador._obter_sink_monitor()
        self.assertTrue(sink.endswith(".monitor") or sink == "@DEFAULT_MONITOR@")

    def test_parar_reseta_bandas(self):
        capturador = CapturadorEspectro()
        capturador.parar()
        self.assertFalse(capturador._rodando)
        self.assertFalse(capturador.ativo_com_som)
        self.assertEqual(capturador.obter_bandas(), (0.0, 0.0, 0.0, 0.0))

    def test_idempotencia_encerrar_proc(self):
        capturador = CapturadorEspectro()
        # Não deve lançar exceções se chamado repetidamente sem processo ativo
        capturador._encerrar_proc()
        capturador._encerrar_proc()
        capturador.parar()
        self.assertIsNone(capturador._proc)


if __name__ == "__main__":
    unittest.main()
