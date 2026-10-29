"""Servidor de IPC bidirecional baseado em UNIX domain socket e JSON-RPC."""

from __future__ import annotations
import json
import os
import socket
import threading
from typing import Callable, Dict, Any, Optional
from gi.repository import GLib

SOCKET_PATH = f"/run/user/{os.getuid()}/island.sock"


class ServidorIPC:
    """Servidor de socket UNIX com suporte a JSON-RPC e fallback para comandos legados."""

    def __init__(self, manipulador: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        self.manipulador = manipulador
        self._servidor: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._ativo = False
        self._caminho: Optional[str] = None

    def iniciar(self, caminho: str = SOCKET_PATH) -> None:
        self._caminho = caminho
        if os.path.exists(caminho):
            try:
                os.unlink(caminho)
            except Exception:
                pass

        self._servidor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._servidor.bind(caminho)
        self._servidor.listen(5)
        self._ativo = True

        self._thread = threading.Thread(target=self._loop_servidor, daemon=True)
        self._thread.start()

    def parar(self, caminho: Optional[str] = None) -> None:
        self._ativo = False
        caminho_alvo = caminho or self._caminho or SOCKET_PATH
        if self._servidor:
            try:
                self._servidor.close()
            except Exception:
                pass
            self._servidor = None
        if os.path.exists(caminho_alvo):
            try:
                os.unlink(caminho_alvo)
            except Exception:
                pass

    def _loop_servidor(self) -> None:
        while self._ativo and self._servidor:
            try:
                conn, _ = self._servidor.accept()
                threading.Thread(target=self._atender_cliente, args=(conn,), daemon=True).start()
            except Exception:
                break

    def _atender_cliente(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(2.0)
                dados_brutos = conn.recv(8192).decode("utf-8").strip()
                if not dados_brutos:
                    return

                # Tenta parsear como JSON estruturado
                if dados_brutos.startswith("{") and dados_brutos.endswith("}"):
                    requisicao = json.loads(dados_brutos)
                else:
                    # Fallback para comandos de texto legados (ex: "toggle", "page midia")
                    partes = dados_brutos.split(maxsplit=1)
                    acao = partes[0]
                    arg = partes[1] if len(partes) > 1 else ""
                    requisicao = {"acao": acao, "argumento": arg}

                resposta = self._despachar_requisicao(requisicao)
                resposta_bytes = (json.dumps(resposta) + "\n").encode("utf-8")
                conn.sendall(resposta_bytes)
            except Exception as e:
                try:
                    conn.sendall(json.dumps({"status": "erro", "mensagem": str(e)}).encode("utf-8") + b"\n")
                except Exception:
                    pass

    def _despachar_requisicao(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Executa o manipulador na thread principal da UI se necessário via Event."""
        evento = threading.Event()
        resultado: Dict[str, Any] = {"status": "erro", "mensagem": "Timeout"}

        def _executar():
            nonlocal resultado
            try:
                resultado = self.manipulador(req)
            except Exception as e:
                resultado = {"status": "erro", "mensagem": str(e)}
            evento.set()
            return False

        GLib.idle_add(_executar)
        evento.wait(timeout=1.5)
        return resultado
