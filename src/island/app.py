"""Aplicação principal da Dynamic Island para Hyprland.

Orquestra fontes reativas, engine de cena, física de molas a 144Hz,
cabeçalho em 3 slots e páginas modulares via GTK4 Layer Shell.
"""

from __future__ import annotations
import math
import os
import signal
import sys
import time
from typing import Optional, Dict, Any, List

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from island.core.atividade import (
        Atividade,
        SlotItem,
        GerenciadorAtividades,
        PRIORIDADE_HUD,
        PRIORIDADE_NOTIFICACAO,
        PRIORIDADE_TIMER,
        PRIORIDADE_MIDIA,
        PRIORIDADE_IDLE,
    )
    from island.core.cena import Cena, resolver_cena
    from island.core.config import carregar_config
    from island.core.mola import Mola
    from island.core.ipc import ServidorIPC, SOCKET_PATH

    from island.fontes.mpris import FonteMpris
    from island.fontes.volume import FonteVolume
    from island.fontes.sistema import FonteSistema
    from island.fontes.rede import FonteRede
    from island.fontes.kde import FonteKde
    from island.fontes.hypr import FonteHypr

    from island.ui.cabecalho import CabecalhoSlots
    from island.paginas.midia import PaginaMidia
    from island.paginas.hub import PaginaHub
    from island.paginas.controles import PaginaControles
    from island.paginas.historico import PaginaHistorico
else:
    from .core.atividade import (
        Atividade,
        SlotItem,
        GerenciadorAtividades,
        PRIORIDADE_HUD,
        PRIORIDADE_NOTIFICACAO,
        PRIORIDADE_TIMER,
        PRIORIDADE_MIDIA,
        PRIORIDADE_IDLE,
    )
    from .core.cena import Cena, resolver_cena
    from .core.config import carregar_config
    from .core.mola import Mola
    from .core.ipc import ServidorIPC, SOCKET_PATH

    from .fontes.mpris import FonteMpris
    from .fontes.volume import FonteVolume
    from .fontes.sistema import FonteSistema
    from .fontes.rede import FonteRede
    from .fontes.kde import FonteKde
    from .fontes.hypr import FonteHypr

    from .ui.cabecalho import CabecalhoSlots
    from .paginas.midia import PaginaMidia
    from .paginas.hub import PaginaHub
    from .paginas.controles import PaginaControles
    from .paginas.historico import PaginaHistorico


def monitor_por_nome(nome: str) -> Optional[Gdk.Monitor]:
    display = Gdk.Display.get_default()
    if display is None:
        return None
    monitors = display.get_monitors()
    for i in range(monitors.get_n_items()):
        m = monitors.get_item(i)
        if m and m.get_connector() == nome:
            return m
    return None


class DynamicIslandApp:
    def __init__(self, monitor_nome: str | None = None) -> None:
        self.config = carregar_config()
        self.monitor_nome = monitor_nome or self.config["geral"].get("monitor", "DP-2")

        # 1. Fontes de Dados Reativas
        self.fonte_mpris = FonteMpris()
        self.fonte_volume = FonteVolume(
            self.config["audio"].get("sink_monitor", "hdmi-stereo"),
            self.config["audio"].get("sink_fones", "analog-stereo")
        )
        self.fonte_sistema = FonteSistema()
        self.fonte_rede = FonteRede(
            self.config["rede"].get("vpn_conexao", "Cloudflare-WARP"),
            self.config["rede"].get("adguard_url", "http://127.0.0.1:3000")
        )
        self.fonte_kde = FonteKde()
        self.fonte_hypr = FonteHypr(self.monitor_nome)

        # 2. Gerenciador de Atividades e Estado
        self.atividades = GerenciadorAtividades()
        self.atividades.observar(self._ao_mudar_atividades)

        self._expandido = False
        self._fixado = False
        self._dnd_ativo = False
        self._fullscreen = False

        # 3. Física de Mola Interrompível (Damped Spring)
        self._mola_w = Mola(200.0, resposta=0.42, amortecimento=0.75)
        self._mola_h = Mola(34.0, resposta=0.42, amortecimento=0.75)
        self._tick_id = 0
        self._ult_frame_time: Optional[float] = None
        self._frame_eq_idx = 0
        self._timer_midia_id = 0

        # 4. Construção da Interface
        self._construir_ui()

        # 5. Conexão de Eventos das Fontes
        self._conectar_fontes()

        # 6. Servidor IPC JSON-RPC
        self.ipc = ServidorIPC(manipulador=self._processar_ipc)
        self.ipc.iniciar()

        # Ativação das fontes principais de segundo plano
        self.fonte_hypr.assinar(self._ao_evento_hypr)
        self.fonte_volume.assinar(self._ao_evento_volume)
        self.fonte_mpris.assinar(self._ao_evento_mpris)

        # Renderização da cena inicial
        GLib.idle_add(self._recalcular_cena)

    def _construir_ui(self) -> None:
        self.win = Gtk.Window()
        self.win.set_title("dynamic-island")

        # Configuração Layer Shell
        Gtk4LayerShell.init_for_window(self.win)
        Gtk4LayerShell.set_layer(self.win, Gtk4LayerShell.Layer.TOP)
        Gtk4LayerShell.set_namespace(self.win, "island")
        Gtk4LayerShell.set_anchor(self.win, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_margin(self.win, Gtk4LayerShell.Edge.TOP, int(self.config["geral"].get("margem_topo", 8)))
        Gtk4LayerShell.set_exclusive_zone(self.win, 0)
        Gtk4LayerShell.set_keyboard_mode(self.win, Gtk4LayerShell.KeyboardMode.NONE)

        mon = monitor_por_nome(self.monitor_nome)
        if mon:
            Gtk4LayerShell.set_monitor(self.win, mon)

        # Container Principal (Pílula)
        self.pilula = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pilula.set_name("ilha")
        self.pilula.set_valign(Gtk.Align.START)
        self.pilula.set_halign(Gtk.Align.CENTER)

        # 1. Cabeçalho em 3 Slots
        self.cabecalho = CabecalhoSlots()
        self.pilula.append(self.cabecalho)

        # 2. Revealer do Conteúdo Expandido
        self.revelador = Gtk.Revealer()
        self.revelador.set_transition_type(Gtk.RevealerTransitionType.NONE)
        self.revelador.set_reveal_child(False)

        # 3. Stack de Páginas Modulares
        self.pag_midia = PaginaMidia(self.fonte_mpris, ao_focar_player=self._focar_player)
        self.pag_hub = PaginaHub(self.fonte_hypr, cidade_clima=self.config["clima"].get("cidade", "Recife"))
        self.pag_controles = PaginaControles(
            self.fonte_sistema, self.fonte_volume, self.fonte_rede, self.fonte_kde,
            toggles_config=self.config.get("toggles", [])
        )
        self.pag_historico = PaginaHistorico()

        self.paginas: Dict[str, Any] = {
            "midia": self.pag_midia,
            "hub": self.pag_hub,
            "controles": self.pag_controles,
            "historico": self.pag_historico,
        }

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(140)
        self.stack.set_hhomogeneous(True)
        self.stack.set_vhomogeneous(False)
        self.stack.set_overflow(Gtk.Overflow.HIDDEN)

        for nome, pag in self.paginas.items():
            self.stack.add_named(pag.construir(), nome)

        # Indicador de Páginas (Page Dots)
        self.page_dots_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.page_dots_box.set_name("page-dots")
        self.page_dots_box.set_halign(Gtk.Align.CENTER)
        self._dots = {}

        for nome in ["midia", "hub", "controles", "historico"]:
            dot = Gtk.Box()
            dot.add_css_class("page-dot")
            dot.set_cursor_from_name("pointer")
            g_dot = Gtk.GestureClick()
            g_dot.connect("released", lambda _g, _n, _x, _y, n=nome: self.mudar_pagina(n))
            dot.add_controller(g_dot)
            self.page_dots_box.append(dot)
            self._dots[nome] = dot

        painel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        painel.set_name("hero-card")
        painel.append(self.stack)
        painel.append(self.page_dots_box)

        self.revelador.set_child(painel)
        self.pilula.append(self.revelador)
        self.win.set_child(self.pilula)

        # Gestos na Pílula
        clique_pilula = Gtk.GestureClick()
        clique_pilula.connect("released", self._ao_clicar_pilula)
        self.cabecalho.add_controller(clique_pilula)

        # Hover Controller
        ctrl_hover = Gtk.EventControllerMotion()
        ctrl_hover.connect("enter", lambda *_: self._ao_hover(True))
        ctrl_hover.connect("leave", lambda *_: self._ao_hover(False))
        self.win.add_controller(ctrl_hover)

        # Carrega folha de estilo CSS
        self._carregar_css()

        self.win.realize()
        self.win.set_size_request(200, 34)
        self.win.set_default_size(200, 34)

    def _carregar_css(self) -> None:
        css_path = os.path.expanduser("~/.config/hypr/scripts/island.css")
        if not os.path.exists(css_path):
            css_path = os.path.join(os.path.dirname(__file__), "../ui/island.css")
        if os.path.exists(css_path):
            prov = Gtk.CssProvider()
            prov.load_from_path(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _conectar_fontes(self) -> None:
        pass

    def _ao_evento_hypr(self, evento: str, dados: any) -> None:
        if evento == "fullscreen":
            self._fullscreen = bool(dados.get("ativo"))
            self.win.set_visible(not self._fullscreen)
        elif evento in ("workspaces", "janela"):
            self._recalcular_cena()

    def _ao_evento_volume(self, evento: str, dados: any) -> None:
        if evento == "volume":
            vol = dados.get("valor", 0)
            mudo = dados.get("mudo", False)
            # Dispara HUD transitório de volume
            txt_vol = "Mudo" if mudo else f"{vol}%"
            ico = "󰖁" if mudo else ("󰕾" if vol > 66 else ("󰖀" if vol > 33 else "󰕿"))
            hud = Atividade(
                id="hud_volume",
                prioridade=PRIORIDADE_HUD,
                ttl_ms=1500,
                transitoria=True,
                leading=SlotItem(tipo="icone", valor=ico, css_class="mudo" if mudo else ""),
                center=SlotItem(tipo="texto", valor=f"Volume {txt_vol}"),
                trailing=SlotItem(tipo="texto", valor=txt_vol),
                progresso=min(1.0, max(0.0, vol / 100.0)),
                largura_alvo=260,
            )
            self.atividades.adicionar(hud)
            GLib.timeout_add(1500, lambda: self.atividades.remover("hud_volume") and False)

    def _ao_evento_mpris(self, evento: str, dados: any) -> None:
        if evento in ("mudar", "seeked"):
            d = self.fonte_mpris.dados()
            if d and (d.get("titulo") or d.get("art")):
                tocando = (d.get("status") == "Playing")
                eq_txt = " ▂▃" if tocando else (" ▂ " if d.get("status") == "Paused" else "")
                ativ_midia = Atividade(
                    id="midia",
                    prioridade=PRIORIDADE_MIDIA,
                    leading=SlotItem(tipo="capa", valor=d.get("art")),
                    center=SlotItem(tipo="texto", valor=d.get("titulo")),
                    trailing=SlotItem(tipo="equalizador", valor=eq_txt, css_class="dinamico"),
                    progresso=0.0 if d.get("duracao", 0) <= 0 else (self.fonte_mpris.posicao_estimada() / d["duracao"]),
                    largura_alvo=340,
                    dados=d,
                )
                self.atividades.adicionar(ativ_midia)
            else:
                self.atividades.remover("midia")
            self._sincronizar_timer_midia()

    def _sincronizar_timer_midia(self) -> None:
        d = self.fonte_mpris.dados()
        tocando = (d and d.get("status") == "Playing" and not self._fullscreen)
        if tocando:
            if not self._timer_midia_id:
                self._timer_midia_id = GLib.timeout_add(140, self._tick_midia)
        else:
            if self._timer_midia_id:
                GLib.source_remove(self._timer_midia_id)
                self._timer_midia_id = 0

    def _tick_midia(self) -> bool:
        d = self.fonte_mpris.dados()
        if not d or d.get("status") != "Playing" or self._fullscreen:
            self._timer_midia_id = 0
            return GLib.SOURCE_REMOVE

        frames = [" ▂▃", "▂▃▅", "▃▅▆", "▅▆▇", "▆▇█", "▇▆▅", "▅▃▂", "▃▂ "]
        self._frame_eq_idx = (self._frame_eq_idx + 1) % len(frames)
        ativ = self.atividades.obter("midia")
        if ativ:
            ativ.trailing = SlotItem(tipo="equalizador", valor=frames[self._frame_eq_idx], css_class="dinamico")
            if d.get("duracao", 0) > 0:
                ativ.progresso = min(1.0, max(0.0, self.fonte_mpris.posicao_estimada() / d["duracao"]))
            self._recalcular_cena()
        return GLib.SOURCE_CONTINUE

    def _ao_mudar_atividades(self) -> None:
        GLib.idle_add(self._recalcular_cena)

    def _recalcular_cena(self) -> None:
        # Se estiver em repouso sem atividades, monta slots padrão
        todas = self.atividades.todas()
        if not todas:
            # Idle: workspaces no leading, janela no center, relógio no trailing
            ws_ativo = self.fonte_hypr.workspace_ativo or 1
            ws_dots = " ".join("●" if w == ws_ativo else "○" for w in (self.fonte_hypr.workspaces_existentes or [1]))
            hora = time.strftime("%H:%M")
            ativ_idle = Atividade(
                id="idle",
                prioridade=PRIORIDADE_IDLE,
                leading=SlotItem(tipo="texto", valor=ws_dots, css_class="ws-dots"),
                center=SlotItem(tipo="texto", valor=self.fonte_hypr.janela_titulo or "Hyprland"),
                trailing=SlotItem(tipo="texto", valor=hora, css_class="relogio"),
                largura_alvo=200,
            )
            todas = [ativ_idle]

        cena = resolver_cena(
            todas,
            expandido=self._expandido,
            fixado=self._fixado,
            altura_base=34,
            altura_expandida=220,
            largura_maxima=440,
            largura_idle=200,
        )

        self.cabecalho.aplicar_cena(cena)
        self._animar_para(cena.largura, cena.altura)

    def _animar_para(self, largura: int, altura: int) -> None:
        self._mola_w.ir_para(largura)
        self._mola_h.ir_para(altura)
        if not self._tick_id:
            self._ult_frame_time = None
            self._tick_id = self.win.add_tick_callback(self._passo_animacao)

    def _passo_animacao(self, _win, clock) -> bool:
        t = clock.get_frame_time() / 1_000_000.0
        if self._ult_frame_time is None:
            dt = 1.0 / 60.0
        else:
            dt = t - self._ult_frame_time
        self._ult_frame_time = t

        ok_w = self._mola_w.passo(dt)
        ok_h = self._mola_h.passo(dt)

        w = round(self._mola_w.x)
        h = round(self._mola_h.x)

        self.pilula.set_size_request(w, h)
        self.win.set_default_size(w, h)

        if ok_w and ok_h:
            w_final = round(self._mola_w.alvo)
            h_final = round(self._mola_h.alvo)
            self.pilula.set_size_request(w_final, h_final)
            self.win.set_default_size(w_final, h_final)
            self._tick_id = 0
            self._ult_frame_time = None
            return GLib.SOURCE_REMOVE

        return GLib.SOURCE_CONTINUE

    def expandir(self, sim: bool) -> None:
        if self._expandido == sim:
            return
        self._expandido = sim
        self.revelador.set_reveal_child(sim)

        # Notifica página atual
        pag_nome = self.stack.get_visible_child_name() or "hub"
        pag = self.paginas.get(pag_nome)
        if pag:
            if sim:
                pag.ao_mostrar()
            else:
                pag.ao_esconder()

        self._recalcular_cena()

    def mudar_pagina(self, nome: str) -> None:
        if nome not in self.paginas:
            return
        ant_nome = self.stack.get_visible_child_name()
        if ant_nome and ant_nome in self.paginas:
            self.paginas[ant_nome].ao_esconder()

        self.stack.set_visible_child_name(nome)
        self.paginas[nome].ao_mostrar()

        # Atualiza dots
        for n, dot in self._dots.items():
            if n == nome:
                dot.add_css_class("ativo")
            else:
                dot.remove_css_class("ativo")

    def _ao_clicar_pilula(self, _gesture, _n, _x, _y) -> None:
        if not self._expandido:
            inicial = "midia" if self.pag_midia.disponivel() else "hub"
            self.mudar_pagina(inicial)
            self.expandir(True)
        elif not self._fixado:
            self.expandir(False)

    def _ao_hover(self, dentro: bool) -> None:
        if not self._fixado:
            if dentro and not self._expandido:
                inicial = "midia" if self.pag_midia.disponivel() else "hub"
                self.mudar_pagina(inicial)
                self.expandir(True)
            elif not dentro and self._expandido:
                self.expandir(False)

    def _focar_player(self) -> None:
        de = self.fonte_mpris.desktop_entry()
        if de:
            import subprocess
            subprocess.Popen(["hyprctl", "dispatch", "focuswindow", f"class:(?i){de}"])

    def _processar_ipc(self, req: Dict[str, Any]) -> Dict[str, Any]:
        acao = req.get("acao") or req.get("action") or ""
        if acao in ("state", "status"):
            m_dados = self.fonte_mpris.dados() or {}
            return {
                "status": "ok",
                "dados": {
                    "expandido": self._expandido,
                    "fixado": self._fixado,
                    "dnd": self._dnd_ativo,
                    "pagina": self.stack.get_visible_child_name(),
                    "volume": self.fonte_volume.valor or 0,
                    "mpris": {
                        "player": m_dados.get("player"),
                        "titulo": m_dados.get("titulo"),
                        "artista": m_dados.get("artista"),
                        "status": m_dados.get("status"),
                    }
                }
            }
        elif acao == "toggle":
            self.expandir(not self._expandido)
            return {"status": "ok", "expandido": self._expandido}
        elif acao == "expand":
            self.expandir(True)
            return {"status": "ok"}
        elif acao == "collapse":
            self.expandir(False)
            return {"status": "ok"}
        elif acao == "pin":
            modo = req.get("modo", "toggle")
            if modo == "on":
                self._fixado = True
            elif modo == "off":
                self._fixado = False
            else:
                self._fixado = not self._fixado
            Gtk4LayerShell.set_keyboard_mode(
                self.win,
                Gtk4LayerShell.KeyboardMode.ON_DEMAND if self._fixado else Gtk4LayerShell.KeyboardMode.NONE
            )
            self._recalcular_cena()
            return {"status": "ok", "fixado": self._fixado}
        elif acao == "page":
            nome = req.get("nome") or req.get("name") or req.get("argumento") or ""
            aliases = {"sistema": "controles", "produtividade": "hub"}
            nome = aliases.get(nome, nome)
            if nome in self.paginas:
                self.mudar_pagina(nome)
                if not self._expandido:
                    self.expandir(True)
                return {"status": "ok", "pagina": nome}
            return {"status": "erro", "mensagem": f"Página '{nome}' não existe"}
        elif acao == "media":
            cmd = req.get("comando") or req.get("command") or ""
            if cmd in ("play-pause", "toggle"):
                self.fonte_mpris.play_pause()
            elif cmd == "next":
                self.fonte_mpris.proxima()
            elif cmd in ("prev", "previous"):
                self.fonte_mpris.anterior()
            elif cmd == "shuffle":
                self.fonte_mpris.alternar_shuffle()
            elif cmd == "repeat":
                self.fonte_mpris.alternar_loop()
            return {"status": "ok"}
        elif acao in ("notify", "notificacao"):
            app = req.get("app", "Sistema")
            titulo = req.get("titulo") or req.get("summary") or req.get("title") or ""
            corpo = req.get("corpo") or req.get("body") or ""
            urgencia = req.get("urgencia") or req.get("urgency") or "normal"
            duracao_ms = int(req.get("duracao_ms") or req.get("duration") or 3500)

            ico = "󰂚"
            app_lower = app.lower()
            if "spot" in app_lower or "music" in app_lower:
                ico = "󰎆"
            elif "mail" in app_lower:
                ico = "󰇮"
            elif "term" in app_lower:
                ico = "󰆍"

            hud = Atividade(
                id="hud_notif",
                prioridade=PRIORIDADE_NOTIFICACAO,
                ttl_ms=duracao_ms,
                transitoria=True,
                leading=SlotItem(tipo="icone", valor=ico, css_class=f"notif-{urgencia.lower()}"),
                center=SlotItem(tipo="texto", valor=titulo),
                trailing=SlotItem(tipo="texto", valor=corpo[:18] if corpo else app),
                largura_alvo=320,
            )
            self.atividades.adicionar(hud)
            GLib.timeout_add(duracao_ms, lambda: self.atividades.remover("hud_notif") and False)
            return {"status": "ok"}
        elif acao in ("dnd", "dnd_toggle"):
            modo = req.get("modo") or req.get("mode") or "toggle"
            if modo == "on":
                self._dnd_ativo = True
            elif modo == "off":
                self._dnd_ativo = False
            else:
                self._dnd_ativo = not self._dnd_ativo

            import subprocess
            try:
                subprocess.Popen(["dunstctl", "set-paused", "true" if self._dnd_ativo else "false"])
            except Exception:
                pass

            ico = "󰂛" if self._dnd_ativo else "󰂚"
            txt = "Não Perturbe Ativado" if self._dnd_ativo else "Não Perturbe Desativado"
            hud = Atividade(
                id="hud_dnd",
                prioridade=PRIORIDADE_HUD,
                ttl_ms=1500,
                transitoria=True,
                leading=SlotItem(tipo="icone", valor=ico, css_class="dnd"),
                center=SlotItem(tipo="texto", valor=txt),
                largura_alvo=260,
            )
            self.atividades.adicionar(hud)
            GLib.timeout_add(1500, lambda: self.atividades.remover("hud_dnd") and False)
            return {"status": "ok", "dnd": self._dnd_ativo}
        elif acao in ("activity", "atividade"):
            op = req.get("operacao") or req.get("operation") or ("adicionar" if req.get("id") else "")
            ativ_id = req.get("id", "")
            if (op == "adicionar" or not op) and ativ_id:
                prog = req.get("progresso") if req.get("progresso") is not None else req.get("progress")
                ativ = Atividade(
                    id=ativ_id,
                    prioridade=PRIORIDADE_TIMER,
                    ttl_ms=int(req.get("ttl", 0)) * 1000,
                    leading=SlotItem(tipo="icone", valor=req.get("icone") or req.get("icon") or "󰔟"),
                    center=SlotItem(tipo="texto", valor=req.get("texto") or req.get("text") or ""),
                    trailing=SlotItem(tipo="texto", valor=req.get("subtexto") or req.get("subtext") or ""),
                    progresso=float(prog) if prog is not None else None,
                )
                self.atividades.adicionar(ativ)
                ttl_s = int(req.get("ttl", 0))
                if ttl_s > 0:
                    GLib.timeout_add_seconds(ttl_s, lambda: self.atividades.remover(ativ_id) and False)
                return {"status": "ok"}
            elif op == "remover" and ativ_id:
                self.atividades.remover(ativ_id)
                return {"status": "ok"}
            elif op == "limpar":
                for a in list(self.atividades.todas()):
                    if a.id not in ("midia", "idle", "hud_volume", "hud_notif"):
                        self.atividades.remover(a.id)
                return {"status": "ok"}
        elif acao == "activity_end":
            ativ_id = req.get("id", "")
            if ativ_id:
                self.atividades.remover(ativ_id)
            return {"status": "ok"}
        elif acao == "activity_clear":
            for a in list(self.atividades.todas()):
                if a.id not in ("midia", "idle", "hud_volume", "hud_notif"):
                    self.atividades.remover(a.id)
            return {"status": "ok"}
        return {"status": "erro", "mensagem": f"Ação desconhecida: {acao}"}


def main() -> int:
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    monitor = sys.argv[1] if len(sys.argv) > 1 else None
    app = DynamicIslandApp(monitor_nome=monitor)
    app.win.present()

    loop = GLib.MainLoop()
    try:
        loop.run()
    except (KeyboardInterrupt, SystemExit):
        app.ipc.parar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
