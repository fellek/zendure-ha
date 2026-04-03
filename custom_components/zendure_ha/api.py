"""Zendure Integration api."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from paho.mqtt import client as mqtt_client
from paho.mqtt import enums as mqtt_enums

from . import mqtt_protocol
from .api_auth import api_ha
from .const import (
    CONF_MQTTLOG,
    CONF_MQTTPORT,
    CONF_MQTTPSW,
    CONF_MQTTSERVER,
    CONF_MQTTUSER,
    CONF_WIFIPSW,
    CONF_WIFISSID,
    DOMAIN,
)
from .device import ZendureDevice
from .devices.ace1500 import ACE1500
from .devices.aio2400 import AIO2400
from .devices.hub1200 import Hub1200
from .devices.hub2000 import Hub2000
from .devices.hyper2000 import Hyper2000
from .devices.solarflow800 import SolarFlow800, SolarFlow800Plus, SolarFlow800Pro
from .devices.solarflow1600 import SolarFlow1600
from .devices.solarflow2400 import SolarFlow2400AC, SolarFlow2400AC_Plus, SolarFlow2400Pro
from .devices.superbasev4600 import SuperBaseV4600
from .devices.superbasev6400 import SuperBaseV6400

_LOGGER = logging.getLogger(__name__)

ZENDURE_MANAGER_STORAGE_VERSION = 1
ZENDURE_DEVICES = "devices"


class ZendureApi:
    """Zendure API class."""

    createdevice: dict[str, Callable[[HomeAssistant, str, str, Any], ZendureDevice]] = {
        "ace 1500": ACE1500,
        "aio 2400": AIO2400,
        "solarflow aio zy": AIO2400,
        "hub 1200": Hub1200,
        "solarflow2.0": Hub1200,
        "hub 2000": Hub2000,
        "solarflow hub 2000": Hub2000,
        "hyper 2000": Hyper2000,
        "hyper2000_3.0": Hyper2000,
        "solarflow 800": SolarFlow800,
        "solarflow 800 pro": SolarFlow800Pro,
        "solarflow 800 plus": SolarFlow800Plus,
        "solarflow 1600 ac+": SolarFlow1600,
        "solarflow 2400 ac": SolarFlow2400AC,
        "solarflow 2400 ac+": SolarFlow2400AC_Plus,
        "solarflow 2400 pro": SolarFlow2400Pro,
        "superbase v6400": SuperBaseV6400,
        "superbase v4600": SuperBaseV4600,
    }

    def __init__(self, hass: HomeAssistant, data: Mapping[str, Any], mqtt: Mapping[str, Any]) -> None:
        """Initialize Zendure Api."""
        # Wir speichern hass, falls wir es später für Storage oder Sessions brauchen
        self.hass = hass

        # Allgemeine Config
        self.mqtt_logging: bool = data.get(CONF_MQTTLOG, False)
        self.wifi_ssid: str = data.get(CONF_WIFISSID, "")
        self.wifi_psw: str = data.get(CONF_WIFIPSW, "")

        # --- CLOUD MQTT (Sofort richtig initialisieren!) ---
        url = mqtt.get("url", "")
        self.cloud_server, self.cloud_port = url.rsplit(":", 1) if ":" in url else (url, "1883")

        self.mqtt_cloud = mqtt_client.Client(
            callback_api_version=mqtt_enums.CallbackAPIVersion.VERSION2,
            client_id=mqtt["clientId"],
            clean_session=False,
            userdata="cloud",
            protocol=mqtt_enums.MQTTProtocolVersion.MQTTv31
        )
        self._setup_mqtt_client(
            self.mqtt_cloud,
            self.cloud_server,
            self.cloud_port,
            mqtt["username"],
            mqtt["password"],
            lambda c, u, m: mqtt_protocol.on_msg_cloud(self, c, u, m)
        )

        # --- LOCAL MQTT ---
        self.mqtt_local: mqtt_client.Client | None = None
        self.local_server: str = data.get(CONF_MQTTSERVER, "")
        self.local_port: int = data.get(CONF_MQTTPORT, 1883)
        self.local_user: str = data.get(CONF_MQTTUSER, "")
        self.local_password: str = data.get(CONF_MQTTPSW, "")

        if self.local_server:
            client_id = f"{self.local_user}{secrets.randbelow(10000)}"
            self.mqtt_local = mqtt_client.Client(
                callback_api_version=mqtt_enums.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                clean_session=True,
                userdata="local",
                protocol=mqtt_enums.MQTTProtocolVersion.MQTTv31
            )
            self._setup_mqtt_client(
                self.mqtt_local,
                self.local_server,
                self.local_port,
                self.local_user,
                self.local_password,
                lambda c, u, m: mqtt_protocol.on_msg_local(self, c, u, m)
            )

        # Gerätesteuerung
        self.devices: dict[str, ZendureDevice] = {}

    def _setup_mqtt_client(self, client: mqtt_client.Client, srv: str, port: str | int, user: str, psw: str,
                           msg_callback: Callable) -> None:
        """Hilfsmethode, um einen Client einheitlich zu konfigurieren."""
        try:
            client.on_connect = lambda c, u, f, rc, p: mqtt_protocol.on_connect(self, c, u, f, rc, p)
            client.on_disconnect = lambda c, u, f, rc, p: mqtt_protocol.on_disconnect(self, c, u, f, rc, p)
            client.on_message = msg_callback
            client.suppress_exceptions = True
            client.username_pw_set(user, psw)
            client.connect(srv, int(port))
            client.loop_start()
        except Exception as e:
            _LOGGER.error("Unable to setup MQTT client for %s: %s", srv, e)

    @staticmethod
    async def connect(hass: HomeAssistant, data: dict[str, Any], reload: bool) -> dict[str, Any] | None:
        """Connect to the Zendure API and handle storage fallback."""
        try:
            devices = await api_ha(hass, data)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.error("Failed to connect to Zendure API")
            return None

        # Storage Logik (wird vom Manager benötigt, wenn reload=True ist)
        if reload:
            store = Store(hass, ZENDURE_MANAGER_STORAGE_VERSION, f"{DOMAIN}.storage")
            if devices is None or len(devices) == 0:
                # load configuration from storage
                if (storage := await store.async_load()) and isinstance(storage, dict):
                    devices = storage.get(ZENDURE_DEVICES, {})
            else:
                # Save configuration to storage
                await store.async_save({ZENDURE_DEVICES: devices})

        return devices

    def shutdown(self) -> None:
        """Beendet alle MQTT-Verbindungen sauber (wird von HA aufgerufen)."""
        _LOGGER.info("Shutting down Zendure API connections...")

        if self.mqtt_cloud is not None:
            self.mqtt_cloud.loop_stop()
            self.mqtt_cloud.disconnect()

        if self.mqtt_local is not None:
            self.mqtt_local.loop_stop()
            self.mqtt_local.disconnect()

        # Falls irgendwo noch Device-spezifische MQTT Verbindungen offen sind (auskommentierter Code)
        for device in self.devices.values():
            if hasattr(device, 'zendure') and device.zendure is not None and device.zendure.is_connected():
                device.zendure.loop_stop()
                device.zendure.disconnect()

        _LOGGER.info("Zendure API successfully shut down.")

    @property
    def mqtt_msg_device(self) -> Callable:
        """Return device message callback bound to this api instance."""
        return lambda c, u, m: mqtt_protocol.on_msg_device(self, c, u, m)