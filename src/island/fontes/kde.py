"""Fonte de monitoramento reativo do KDE Connect (Bateria e Conectividade do celular)."""

from __future__ import annotations
import subprocess
import threading
from typing import Optional, Dict, Any
from gi.repository import Gio, GLib
from .base import Fonte


class FonteKde(Fonte):
    def __init__(self) -> None:
        super().__init__()
        self.dispositivo_nome = "Celular"
        self.conectado = False
        self.carga: Optional[int] = None
        self.carregando = False
        self._dev_id: Optional[str] = None
        self._bus: Optional[Gio.DBusConnection] = None
        self._sub_ids: list[int] = []

    def ao_ativar(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            sub1 = self._bus.signal_subscribe(
                "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
                self._ao_name_owner_changed
            )
            sub2 = self._bus.signal_subscribe(
                "org.kde.kdeconnect", "org.kde.kdeconnect.daemon", None,
                "/modules/kdeconnect", None, Gio.DBusSignalFlags.NONE,
                lambda *_: self._iniciar()
            )
            self._sub_ids.extend([sub1, sub2])
            self._iniciar()
        except Exception:
            pass

    def ao_desativar(self) -> None:
        if self._bus:
            for s_id in self._sub_ids:
                try:
                    self._bus.signal_unsubscribe(s_id)
                except Exception:
                    pass
            self._sub_ids.clear()

    def dados(self) -> Dict[str, Any]:
        return {
            "conectado": self.conectado,
            "dispositivo": self.dispositivo_nome,
            "carga": self.carga,
            "carregando": self.carregando
        }

    def _ao_name_owner_changed(self, _conn, _sender, _path, _iface, _signal, params, _user_data=None) -> None:
        try:
            nome, _old, new_owner = params.unpack()
            if nome == "org.kde.kdeconnect":
                if new_owner:
                    self._iniciar()
                else:
                    self.conectado = False
                    self.carga = None
                    self._dev_id = None
                    self.notificar("status", self.dados())
        except Exception:
            pass

    def _iniciar(self) -> None:
        try:
            if not self._bus:
                self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = self._bus.call_sync(
                "org.kde.kdeconnect",
                "/modules/kdeconnect",
                "org.kde.kdeconnect.daemon",
                "devices",
                GLib.Variant("(bb)", (True, True)),
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None
            )
            devs = res.unpack()[0]
            if devs:
                self._dev_id = devs[0]
                self.conectado = True
                try:
                    res_nome = self._bus.call_sync(
                        "org.kde.kdeconnect",
                        f"/modules/kdeconnect/devices/{self._dev_id}",
                        "org.freedesktop.DBus.Properties",
                        "Get",
                        GLib.Variant("(ss)", ("org.kde.kdeconnect.device", "name")),
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None
                    )
                    self.dispositivo_nome = res_nome.unpack()[0]
                except Exception:
                    pass

                try:
                    res_bat = self._bus.call_sync(
                        "org.kde.kdeconnect",
                        f"/modules/kdeconnect/devices/{self._dev_id}/battery",
                        "org.freedesktop.DBus.Properties",
                        "GetAll",
                        GLib.Variant("(s)", ("org.kde.kdeconnect.device.battery",)),
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None
                    )
                    props = res_bat.unpack()[0]
                    self.carga = props.get("charge")
                    self.carregando = props.get("isCharging", False)
                except Exception:
                    pass

                sub_bat = self._bus.signal_subscribe(
                    "org.kde.kdeconnect",
                    "org.kde.kdeconnect.device.battery",
                    None,
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._ao_sinal_bateria
                )
                self._sub_ids.append(sub_bat)
                self.notificar("status", self.dados())
            else:
                self.conectado = False
                self.carga = None
                self.notificar("status", self.dados())
        except Exception:
            self.conectado = False

    def _ao_sinal_bateria(self, _conn, _sender, _path, _iface, sinal, params) -> None:
        try:
            if sinal == "refreshed":
                is_chg, chg = params.unpack()
                self.carregando = is_chg
                self.carga = chg
                self.notificar("bateria", self.dados())
            elif sinal == "PropertiesChanged":
                _iface_name, changed, _inv = params.unpack()
                if "charge" in changed:
                    self.carga = changed["charge"]
                if "isCharging" in changed:
                    self.carregando = changed["isCharging"]
                self.notificar("bateria", self.dados())
        except Exception:
            pass

    def tocar_ou_abrir(self) -> None:
        def _fazer():
            try:
                if self._bus and self._dev_id:
                    self._bus.call_sync(
                        "org.kde.kdeconnect",
                        f"/modules/kdeconnect/devices/{self._dev_id}/findmyphone",
                        "org.kde.kdeconnect.device.findmyphone",
                        "ring",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None
                    )
            except Exception:
                subprocess.Popen(["kdeconnect-app"])
        threading.Thread(target=_fazer, daemon=True).start()
