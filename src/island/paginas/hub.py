"""Página de Produtividade (Daily Hub): Calendário, Clima, Janela Ativa, Uptime e Presets de Timer."""

from __future__ import annotations
import datetime
import os
import subprocess
import time
import urllib.request
import json
from typing import Optional
from gi.repository import Gtk, Pango, GLib
from ..fontes.hypr import FonteHypr


class PaginaHub:
    nome = "hub"
    titulo = "Produtividade (Daily Hub)"
    icone = "󰸗"

    def __init__(self, fonte_hypr: FonteHypr, cidade_clima: str = "Recife") -> None:
        self.hypr = fonte_hypr
        self.cidade_clima = cidade_clima
        self.widget_raiz: Optional[Gtk.Box] = None
        self._clima_ts = 0.0

    def construir(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_name("page-produtividade")

        # 1. Topo: Data por extenso à esquerda + Clima à direita
        self.lbl_data = Gtk.Label()
        self.lbl_data.set_name("idle-data")
        self.lbl_data.set_xalign(0)
        self.lbl_data.set_hexpand(True)

        self.lbl_clima = Gtk.Label(label="󰖐 Carregando...")
        self.lbl_clima.set_name("idle-clima")
        self.lbl_clima.set_cursor_from_name("pointer")
        self.lbl_clima.set_tooltip_text(f"Clima em {self.cidade_clima} (clique para atualizar)")
        g_clima = Gtk.GestureClick()
        g_clima.connect("released", lambda *_: self.atualizar_clima_async(forcar=True))
        self.lbl_clima.add_controller(g_clima)

        topo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        topo.append(self.lbl_data)
        topo.append(self.lbl_clima)

        # 2. Mini Calendário Semanal (7 dias)
        self.cal_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.cal_box.set_name("cal-semana")
        self.cal_box.set_halign(Gtk.Align.CENTER)
        self._construir_mini_calendario()

        # 3. Presets Rápidos de Timer Pomodoro (25/5 e 50/10)
        timer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        timer_box.set_halign(Gtk.Align.CENTER)
        timer_box.set_margin_top(2)

        lbl_timer_tag = Gtk.Label(label="󰔟 Foco:")
        lbl_timer_tag.add_css_class("timer-tag")

        btn_pomodoro = Gtk.Button(label="25 min")
        btn_pomodoro.add_css_class("timer-btn")
        btn_pomodoro.set_cursor_from_name("pointer")
        btn_pomodoro.connect("clicked", lambda *_: self._iniciar_timer_preset(25, "Foco Pomodoro"))

        btn_short_break = Gtk.Button(label="5 min")
        btn_short_break.add_css_class("timer-btn")
        btn_short_break.set_cursor_from_name("pointer")
        btn_short_break.connect("clicked", lambda *_: self._iniciar_timer_preset(5, "Pausa Curta"))

        btn_deep_work = Gtk.Button(label="50 min")
        btn_deep_work.add_css_class("timer-btn")
        btn_deep_work.set_cursor_from_name("pointer")
        btn_deep_work.connect("clicked", lambda *_: self._iniciar_timer_preset(50, "Trabalho Profundo"))

        timer_box.append(lbl_timer_tag)
        timer_box.append(btn_pomodoro)
        timer_box.append(btn_short_break)
        timer_box.append(btn_deep_work)

        # 4. Rodapé: Janela Ativa à esquerda + Uptime à direita
        self.lbl_janela_icone = Gtk.Label(label="󰖲")
        self.lbl_janela_icone.set_name("janela-icone")

        self.lbl_janela = Gtk.Label(label="")
        self.lbl_janela.set_name("janela")
        self.lbl_janela.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl_janela.set_max_width_chars(24)
        self.lbl_janela.set_xalign(0)
        self.lbl_janela.set_hexpand(True)

        self.lbl_uptime = Gtk.Label(label="")
        self.lbl_uptime.set_name("idle-uptime")

        rodape = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        rodape.append(self.lbl_janela_icone)
        rodape.append(self.lbl_janela)
        rodape.append(self.lbl_uptime)

        box.append(topo)
        box.append(self.cal_box)
        box.append(timer_box)
        box.append(rodape)

        self.widget_raiz = box
        self._atualizar_textos()
        return box

    def ao_mostrar(self) -> None:
        self._atualizar_textos()
        self.atualizar_clima_async()

    def ao_esconder(self) -> None:
        pass

    def disponivel(self) -> bool:
        return True

    def _atualizar_textos(self) -> None:
        if not self.widget_raiz:
            return
        data_str = time.strftime("%A, %d de %B").capitalize()
        self.lbl_data.set_markup(f"<span foreground='#cba6f7'>󰸗</span>  {data_str}")

        # Uptime
        try:
            with open("/proc/uptime", "r") as f:
                segundos = float(f.readline().split()[0])
            horas = int(segundos // 3600)
            minutos = int((segundos % 3600) // 60)
            txt = f"{horas}h {minutos:02d}m up" if horas > 0 else f"{minutos}m up"
            self.lbl_uptime.set_markup(f"<span foreground='#a6e3a1'>󱑂</span>  {txt}")
        except Exception:
            self.lbl_uptime.set_text("")

        # Janela ativa
        tit = self.hypr.janela_titulo
        cls = self.hypr.janela_classe
        self.lbl_janela.set_text(tit or cls or "Área de Trabalho")

    def _construir_mini_calendario(self) -> None:
        while child := self.cal_box.get_first_child():
            self.cal_box.remove(child)

        hoje = datetime.date.today()
        nomes_dias = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        inicio_semana = hoje - datetime.timedelta(days=hoje.weekday())

        for i in range(7):
            d = inicio_semana + datetime.timedelta(days=i)
            dia_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            dia_box.add_css_class("cal-dia")
            if d == hoje:
                dia_box.add_css_class("dia-hoje")

            lbl_nome = Gtk.Label(label=nomes_dias[i])
            lbl_nome.add_css_class("dia-nome")
            lbl_num = Gtk.Label(label=str(d.day))
            lbl_num.add_css_class("dia-num")

            dia_box.append(lbl_nome)
            dia_box.append(lbl_num)
            self.cal_box.append(dia_box)

    def _iniciar_timer_preset(self, minutos: int, titulo: str) -> None:
        subprocess.Popen([
            os.path.expanduser("~/.local/bin/island-activity"),
            "timer",
            f"--minutes={minutos}",
            f"--text={titulo}"
        ])

    def atualizar_clima_async(self, forcar: bool = False) -> None:
        agora = time.monotonic()
        if not forcar and (agora - self._clima_ts < 1800):
            return

        def _worker():
            try:
                url = f"https://wttr.in/{self.cidade_clima}?format=j1"
                req = urllib.request.Request(url, headers={"User-Agent": "hypr-dynamic-island/1.0"})
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    dados = json.loads(resp.read().decode())
                cond = dados["current_condition"][0]
                temp = f"{cond['temp_C']}°C"
                desc = cond["weatherDesc"][0]["value"]
                self._clima_ts = time.monotonic()
                GLib.idle_add(lambda: self.lbl_clima.set_markup(
                    f"<span foreground='#fab387'>󰖐</span>  {temp} {desc}"
                ))
            except Exception:
                pass

        import threading
        threading.Thread(target=_worker, daemon=True).start()
