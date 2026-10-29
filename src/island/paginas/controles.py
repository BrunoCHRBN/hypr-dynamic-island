"""Página de Controles e Métricas do Sistema: Volume, Sinks, Quick Toggles Dinâmicos e Dock."""

from __future__ import annotations
import os
import subprocess
from typing import Optional, List, Dict, Any
from gi.repository import Gtk, GLib
from ..fontes.sistema import FonteSistema
from ..fontes.volume import FonteVolume
from ..fontes.rede import FonteRede
from ..fontes.kde import FonteKde


class PaginaControles:
    nome = "controles"
    titulo = "Central de Controle & Sistema"
    icone = "󰒃"

    def __init__(
        self,
        fonte_sistema: FonteSistema,
        fonte_volume: FonteVolume,
        fonte_rede: FonteRede,
        fonte_kde: FonteKde,
        toggles_config: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.sistema = fonte_sistema
        self.volume = fonte_volume
        self.rede = fonte_rede
        self.kde = fonte_kde
        self.toggles_config = toggles_config or []
        self.widget_raiz: Optional[Gtk.Box] = None
        self._toggle_buttons: List[tuple[Gtk.Button, Dict[str, Any]]] = []
        self._ouvindo = False

    def construir(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_name("page-sistema")

        # 1. CPU
        self.sys_icone_cpu = Gtk.Label(label="󰻠")
        self.sys_icone_cpu.add_css_class("sys-icone")
        self.sys_icone_cpu.add_css_class("cpu")

        self.sys_nome_cpu = Gtk.Label(label="CPU")
        self.sys_nome_cpu.add_css_class("sys-nome")

        self.sys_barra_cpu = Gtk.ProgressBar()
        self.sys_barra_cpu.add_css_class("sys-barra")
        self.sys_barra_cpu.add_css_class("cpu")
        self.sys_barra_cpu.set_hexpand(True)

        self.sys_val_cpu = Gtk.Label(label="0%")
        self.sys_val_cpu.add_css_class("sys-val")
        self.sys_val_cpu.set_xalign(1.0)

        linha_cpu = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        linha_cpu.add_css_class("sys-row")
        linha_cpu.append(self.sys_icone_cpu)
        linha_cpu.append(self.sys_nome_cpu)
        linha_cpu.append(self.sys_barra_cpu)
        linha_cpu.append(self.sys_val_cpu)
        linha_cpu.set_cursor_from_name("pointer")
        linha_cpu.set_tooltip_text("Uso do processador (clique para abrir gerenciador de tarefas)")
        g_cpu = Gtk.GestureClick()
        g_cpu.connect("released", lambda *_: self._abrir_processos())
        linha_cpu.add_controller(g_cpu)

        # 2. RAM
        self.sys_icone_mem = Gtk.Label(label="󰍛")
        self.sys_icone_mem.add_css_class("sys-icone")
        self.sys_icone_mem.add_css_class("mem")

        self.sys_nome_mem = Gtk.Label(label="RAM")
        self.sys_nome_mem.add_css_class("sys-nome")

        self.sys_barra_mem = Gtk.ProgressBar()
        self.sys_barra_mem.add_css_class("sys-barra")
        self.sys_barra_mem.add_css_class("mem")
        self.sys_barra_mem.set_hexpand(True)

        self.sys_val_mem = Gtk.Label(label="0%")
        self.sys_val_mem.add_css_class("sys-val")
        self.sys_val_mem.set_xalign(1.0)

        linha_mem = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        linha_mem.add_css_class("sys-row")
        linha_mem.append(self.sys_icone_mem)
        linha_mem.append(self.sys_nome_mem)
        linha_mem.append(self.sys_barra_mem)
        linha_mem.append(self.sys_val_mem)
        linha_mem.set_cursor_from_name("pointer")
        linha_mem.set_tooltip_text("Uso de memória RAM (clique para abrir gerenciador de tarefas)")
        g_mem = Gtk.GestureClick()
        g_mem.connect("released", lambda *_: self._abrir_processos())
        linha_mem.add_controller(g_mem)

        # 3. Volume
        self.sys_icone_vol = Gtk.Label(label="󰕿")
        self.sys_icone_vol.add_css_class("sys-icone")
        self.sys_icone_vol.add_css_class("vol")
        self.sys_icone_vol.set_cursor_from_name("pointer")
        self.sys_icone_vol.set_tooltip_text("Alternar mudo")
        g_mute = Gtk.GestureClick()
        g_mute.connect("released", lambda *_: self.volume.alternar_mudo())
        self.sys_icone_vol.add_controller(g_mute)

        self.sys_nome_vol = Gtk.Label(label="Vol")
        self.sys_nome_vol.add_css_class("sys-nome")

        self.sys_barra_vol = Gtk.ProgressBar()
        self.sys_barra_vol.add_css_class("sys-barra")
        self.sys_barra_vol.add_css_class("vol")
        self.sys_barra_vol.set_hexpand(True)
        self.sys_barra_vol.set_cursor_from_name("pointer")

        g_vol_click = Gtk.GestureClick()
        g_vol_click.connect("released", self._ao_click_barra_vol)
        self.sys_barra_vol.add_controller(g_vol_click)

        self.sys_val_vol = Gtk.Label(label="0%")
        self.sys_val_vol.add_css_class("sys-val")
        self.sys_val_vol.set_xalign(1.0)

        self.btn_audio_sink = Gtk.Button(label="󰋋 Fones")
        self.btn_audio_sink.add_css_class("btn-sink")
        self.btn_audio_sink.set_cursor_from_name("pointer")
        self.btn_audio_sink.set_tooltip_text("Clique para alternar saída de áudio (Fones / Monitor)")
        self.btn_audio_sink.connect("clicked", lambda *_: self.volume.alternar_sink())

        linha_vol = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        linha_vol.add_css_class("sys-row")
        linha_vol.append(self.sys_icone_vol)
        linha_vol.append(self.sys_nome_vol)
        linha_vol.append(self.sys_barra_vol)
        linha_vol.append(self.sys_val_vol)
        linha_vol.append(self.btn_audio_sink)

        sep_1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep_1.add_css_class("sys-sep")

        # 4. Quick Toggles Grid Dinâmico
        self.grid_toggles = Gtk.Grid()
        self.grid_toggles.set_column_spacing(6)
        self.grid_toggles.set_row_spacing(6)
        self.grid_toggles.set_column_homogeneous(True)
        self.grid_toggles.set_margin_top(2)
        self.grid_toggles.set_margin_bottom(2)

        # Botão Celular (KDE Connect)
        self.btn_phone = Gtk.Button(label="󰄡 Celular")
        self.btn_phone.add_css_class("quick-toggle")
        self.btn_phone.add_css_class("phone-chip")
        self.btn_phone.set_cursor_from_name("pointer")
        self.btn_phone.connect("clicked", lambda *_: self.kde.tocar_ou_abrir())
        self.grid_toggles.attach(self.btn_phone, 0, 0, 1, 1)

        # Adiciona toggles configuráveis
        col = 1
        row = 0
        self._toggle_buttons: List[tuple[Gtk.Button, Dict[str, Any]]] = []
        for t_cfg in self.toggles_config:
            btn = Gtk.Button(label=f"{t_cfg.get('icone', '')} {t_cfg.get('nome', '')}")
            btn.add_css_class("quick-toggle")
            btn.set_cursor_from_name("pointer")
            btn.connect("clicked", lambda *_, cfg=t_cfg: self._executar_toggle(cfg))
            self.grid_toggles.attach(btn, col, row, 1, 1)
            self._toggle_buttons.append((btn, t_cfg))
            col += 1
            if col > 1:
                col = 0
                row += 1

        sep_2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep_2.add_css_class("sys-sep")

        # 5. Dock de Ações Rápidas
        dock = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dock.set_name("acoes-box")
        dock.set_halign(Gtk.Align.CENTER)

        btn_screenshot = Gtk.Button()
        btn_screenshot.set_child(Gtk.Image.new_from_icon_name("camera-photo-symbolic"))
        btn_screenshot.add_css_class("acao-btn")
        btn_screenshot.set_tooltip_text("Captura de tela")
        btn_screenshot.connect("clicked", lambda *_: subprocess.Popen(["grimblast", "copy", "area"]))

        btn_clipboard = Gtk.Button()
        btn_clipboard.set_child(Gtk.Image.new_from_icon_name("edit-paste-symbolic"))
        btn_clipboard.add_css_class("acao-btn")
        btn_clipboard.set_tooltip_text("Área de Transferência")
        btn_clipboard.connect("clicked", lambda *_: subprocess.Popen(["cliphist-rofi"]))

        btn_power = Gtk.Button()
        btn_power.set_child(Gtk.Image.new_from_icon_name("system-shutdown-symbolic"))
        btn_power.add_css_class("acao-btn")
        btn_power.add_css_class("power")
        btn_power.set_tooltip_text("Menu de Energia")
        btn_power.connect("clicked", lambda *_: subprocess.Popen(["wlogout"]))

        dock.append(btn_screenshot)
        dock.append(btn_clipboard)
        dock.append(btn_power)

        box.append(linha_cpu)
        box.append(linha_mem)
        box.append(linha_vol)
        box.append(sep_1)
        box.append(self.grid_toggles)
        box.append(sep_2)
        box.append(dock)

        self.widget_raiz = box
        return box

    def ao_mostrar(self) -> None:
        if not self._ouvindo:
            self.sistema.assinar(self._ao_evento_sistema)
            self.volume.assinar(self._ao_evento_volume)
            self.kde.assinar(self._ao_evento_kde)
            self._ouvindo = True
        self._atualizar_toggles()
        self._atualizar_volume_ui()

    def ao_esconder(self) -> None:
        if self._ouvindo:
            self.sistema.desassinar(self._ao_evento_sistema)
            self.volume.desassinar(self._ao_evento_volume)
            self.kde.desassinar(self._ao_evento_kde)
            self._ouvindo = False

    def disponivel(self) -> bool:
        return True

    def _ao_evento_sistema(self, cpu: int, mem: int, temp: Optional[int]) -> None:
        GLib.idle_add(lambda: self._atualizar_metricas(cpu, mem, temp))

    def _ao_evento_volume(self, evento: str, dados: any) -> None:
        GLib.idle_add(self._atualizar_volume_ui)

    def _ao_evento_kde(self, evento: str, dados: any) -> None:
        GLib.idle_add(self._atualizar_celular_ui)

    def _atualizar_metricas(self, cpu: int, mem: int, temp: Optional[int]) -> None:
        if not self.widget_raiz:
            return
        self.sys_barra_cpu.set_fraction(max(0.0, min(1.0, cpu / 100.0)))
        txt_cpu = f"{cpu}% ({temp}°C)" if temp else f"{cpu}%"
        self.sys_val_cpu.set_text(txt_cpu)

        self.sys_barra_mem.set_fraction(max(0.0, min(1.0, mem / 100.0)))
        self.sys_val_mem.set_text(f"{mem}%")

    def _atualizar_volume_ui(self) -> None:
        if not self.widget_raiz:
            return
        vol = self.volume.valor or 0
        mudo = self.volume.mudo
        self.sys_barra_vol.set_fraction(max(0.0, min(1.0, vol / 100.0)))
        self.sys_val_vol.set_text("Mudo" if mudo else f"{vol}%")
        sink = self.volume.sink_atual()
        self.btn_audio_sink.set_label("󰋋 Fones" if sink == "fones" else "󰍹 Monitor")

    def _atualizar_celular_ui(self) -> None:
        if not self.widget_raiz:
            return
        d = self.kde.dados()
        if d.get("conectado"):
            carga = d.get("carga")
            txt = f"󰄡 {carga}%" if carga is not None else "󰄡 Celular"
            self.btn_phone.set_label(txt)
            self.btn_phone.add_css_class("ativo")
        else:
            self.btn_phone.set_label("󰄡 Celular")
            self.btn_phone.remove_css_class("ativo")

    def _atualizar_toggles(self) -> None:
        for btn, cfg in self._toggle_buttons:
            cmd = cfg.get("estado", "")
            if cmd:
                try:
                    res = subprocess.run(cmd, shell=True, capture_output=True, timeout=1.0)
                    if res.returncode == 0:
                        btn.add_css_class("ativo")
                    else:
                        btn.remove_css_class("ativo")
                except Exception:
                    pass

    def _executar_toggle(self, cfg: Dict[str, Any]) -> None:
        cmd_estado = cfg.get("estado", "")
        cmd_ligar = cfg.get("ligar", "")
        cmd_desligar = cfg.get("desligar", "")

        def _worker():
            try:
                ativo = False
                if cmd_estado:
                    ativo = (subprocess.run(cmd_estado, shell=True, capture_output=True, timeout=1.0).returncode == 0)
                cmd_alvo = cmd_desligar if ativo else cmd_ligar
                if cmd_alvo:
                    subprocess.run(cmd_alvo, shell=True, capture_output=True, timeout=6.0)
            except Exception:
                pass
            GLib.idle_add(self._atualizar_toggles)

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _ao_click_barra_vol(self, _gesture, _n, x: float, _y: float) -> None:
        largura = self.sys_barra_vol.get_width()
        if largura > 0:
            pct = int(round(max(0.0, min(1.0, x / largura)) * 100))
            self.volume.definir(pct)

    def _abrir_processos(self) -> None:
        caminho = os.path.expanduser("~/.config/hypr/scripts/task_manager.sh")
        if os.path.isfile(caminho):
            subprocess.Popen([caminho])
        else:
            subprocess.Popen(["btop"])
