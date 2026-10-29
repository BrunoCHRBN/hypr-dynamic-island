"""Fonte de eventos do compositor Hyprland via UNIX socket (.socket2.sock)."""

from __future__ import annotations
import json
import os
import subprocess
import threading
from typing import Optional, List, Tuple, Dict, Any
from .base import Fonte


class FonteHypr(Fonte):
    EVENTOS_WS = (
        "workspace", "focusedmon", "moveworkspace",
        "createworkspace", "destroyworkspace",
        "monitoradded", "monitorremoved"
    )

    def __init__(self, monitor: str = "DP-2") -> None:
        super().__init__()
        self.monitor = monitor
        self.monitor_id: Optional[int] = None
        self.workspace_ativo: Optional[int] = None
        self.workspaces_existentes: List[int] = []
        self.janela_titulo: str = ""
        self.janela_classe: str = ""
        self.fullscreen: bool = False
        self._thread_sub: Optional[threading.Thread] = None
        self._parar_evento = threading.Event()
        self._ambiente = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}

    def _caminho_socket(self) -> str:
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return f"{rt}/hypr/{sig}/.socket2.sock"

    def ao_ativar(self) -> None:
        self._parar_evento.clear()
        self.reler_workspaces()
        self.reler_janela()
        self._thread_sub = threading.Thread(target=self._loop_socket, daemon=True)
        self._thread_sub.start()

    def ao_desativar(self) -> None:
        self._parar_evento.set()
        self._thread_sub = None

    def reler_janela(self) -> Tuple[str, str]:
        try:
            w = json.loads(subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True, text=True, timeout=0.5, env=self._ambiente
            ).stdout)
            tit = (w.get("title") or "").strip()
            cls = (w.get("class") or "").strip()
            self.janela_titulo, self.janela_classe = tit, cls
            self.notificar("janela", {"titulo": tit, "classe": cls})
            return tit, cls
        except Exception:
            return "", ""

    def reler_workspaces(self) -> None:
        try:
            ws = json.loads(subprocess.run(
                ["hyprctl", "workspaces", "-j"],
                capture_output=True, text=True, timeout=0.5, env=self._ambiente
            ).stdout)
            mons = json.loads(subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True, text=True, timeout=0.5, env=self._ambiente
            ).stdout)
        except Exception:
            return

        ativo = None
        for m in mons:
            if m.get("name") == self.monitor:
                ativo = m.get("activeWorkspace", {}).get("id")
                self.monitor_id = m.get("id")

        existentes = sorted({w["id"] for w in ws if w.get("monitor") == self.monitor and w.get("id", 0) > 0})
        self.workspace_ativo = ativo
        self.workspaces_existentes = existentes
        self.notificar("workspaces", {"ativo": ativo, "existentes": existentes})

    def _loop_socket(self) -> None:
        import socket
        caminho = self._caminho_socket()
        if not os.path.exists(caminho):
            return

        while not self._parar_evento.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(2.0)
                    s.connect(caminho)
                    buffer = ""
                    while not self._parar_evento.is_set():
                        try:
                            dados = s.recv(4096).decode("utf-8", errors="ignore")
                            if not dados:
                                break
                            buffer += dados
                            while "\n" in buffer:
                                linha, buffer = buffer.split("\n", 1)
                                linha = linha.strip()
                                if not linha:
                                    continue
                                evento, _, payload = linha.partition(">>")
                                if evento in self.EVENTOS_WS:
                                    self.reler_workspaces()
                                elif evento == "activewindow":
                                    classe, _, titulo = payload.partition(",")
                                    self.janela_titulo = titulo
                                    self.janela_classe = classe
                                    self.notificar("janela", {"titulo": titulo, "classe": classe})
                                elif evento == "fullscreen":
                                    is_fs = (payload.strip() == "1")
                                    self.fullscreen = is_fs
                                    self.notificar("fullscreen", {"ativo": is_fs})
                        except socket.timeout:
                            continue
            except Exception:
                # Se cair ou hyprland reiniciar, aguarda antes de tentar reconectar
                self._parar_evento.wait(3.0)
