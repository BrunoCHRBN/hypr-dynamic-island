"""Testes unitários para o servidor de IPC e protocolo JSON-RPC."""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gi.repository import GLib
from island.core.ipc import ServidorIPC


class TestIPC(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loop = GLib.MainLoop()
        cls.loop_thread = threading.Thread(target=cls.loop.run, daemon=True)
        cls.loop_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.loop.quit()

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.sock_path = os.path.join(self.tmp_dir, "test_island.sock")
        self.recebido = []

        def handler(req):
            self.recebido.append(req)
            acao = req.get("acao")
            if acao == "toggle":
                return {"status": "ok", "expandido": True}
            elif acao == "page":
                return {"status": "ok", "pagina": req.get("argumento") or req.get("nome")}
            return {"status": "erro", "mensagem": "desconhecido"}

        self.server = ServidorIPC(manipulador=handler)
        self.server.iniciar(caminho=self.sock_path)
        time.sleep(0.05)

    def tearDown(self):
        self.server.parar()
        if os.path.exists(self.tmp_dir):
            try:
                os.rmdir(self.tmp_dir)
            except Exception:
                pass

    def _enviar(self, mensagem: str) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(self.sock_path)
            s.sendall(mensagem.encode("utf-8") + b"\n")
            resp = s.recv(4096).decode("utf-8").strip()
            return json.loads(resp)

    def test_json_rpc_toggle(self):
        req = json.dumps({"acao": "toggle"})
        resp = self._enviar(req)
        self.assertEqual(resp.get("status"), "ok")
        self.assertTrue(resp.get("expandido"))
        self.assertEqual(len(self.recebido), 1)
        self.assertEqual(self.recebido[0].get("acao"), "toggle")

    def test_legado_toggle(self):
        resp = self._enviar("toggle")
        self.assertEqual(resp.get("status"), "ok")
        self.assertTrue(resp.get("expandido"))
        self.assertEqual(self.recebido[-1].get("acao"), "toggle")

    def test_legado_page_argumento(self):
        resp = self._enviar("page midia")
        self.assertEqual(resp.get("status"), "ok")
        self.assertEqual(resp.get("pagina"), "midia")
        self.assertEqual(self.recebido[-1].get("acao"), "page")
        self.assertEqual(self.recebido[-1].get("argumento"), "midia")

    def test_comando_invalido(self):
        resp = self._enviar("desconhecido_xyz")
        self.assertEqual(resp.get("status"), "erro")


if __name__ == "__main__":
    unittest.main()
