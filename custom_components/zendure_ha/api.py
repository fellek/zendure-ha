"""Zendure Integration api."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import traceback
from base64 import b64decode
from collections.abc import Callable
from datetime import datetime
from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from paho.mqtt import client as mqtt_client
from paho.mqtt import enums as mqtt_enums

from .const import (
    CONF_APPTOKEN,
    CONF_HAKEY,
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
            self.mqtt_msg_cloud
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
                self.mqtt_msg_local
            )

        # Gerätesteuerung
        self.devices: dict[str, ZendureDevice] = {}

    def _setup_mqtt_client(self, client: mqtt_client.Client, srv: str, port: str | int, user: str, psw: str,
                           msg_callback: Callable) -> None:
        """Hilfsmethode, um einen Client einheitlich zu konfigurieren."""
        try:
            client.on_connect = self.mqtt_connect
            client.on_disconnect = self.mqtt_disconnect
            client.on_message = msg_callback  # <-- Wir übergeben die Funktion direkt als Parameter!
            client.suppress_exceptions = True
            client.username_pw_set(user, psw)
            client.connect(srv, int(port))
            client.loop_start()
        except Exception as e:
            _LOGGER.error("Unable to setup MQTT client for %s: %s", srv, e)

    @staticmethod
    async def api_ha(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any] | None:
        """Connect to the Zendure API."""
        session = async_get_clientsession(hass)

        if (token := data.get(CONF_APPTOKEN)) is not None and len(token) > 1:
            try:
                base64_url = b64decode(str(token)).decode("utf-8")
                api_url, app_key = base64_url.rsplit(".", 1)
            except Exception as e:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_zendure_token") from e
        else:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_zendure_token")

        try:
            timestamp = int(datetime.now().timestamp())
            nonce = str(secrets.randbelow(90000) + 10000)

            sign_params = {"appKey": app_key, "timestamp": timestamp, "nonce": nonce}
            body_str = "".join(f"{k}{v}" for k, v in sorted(sign_params.items()))

            # SHA1 bleibt erstmal, da es die Zendure API so verlangt
            sign_str = f"{CONF_HAKEY}{body_str}{CONF_HAKEY}"
            sha1 = hashlib.sha1()
            sha1.update(sign_str.encode("utf-8"))
            sign = sha1.hexdigest().upper()

            headers = {
                "Content-Type": "application/json",
                "timestamp": str(timestamp),
                "nonce": nonce,
                "clientid": "zenHa",
                "sign": sign,
            }

            result = await session.post(url=f"{api_url}/api/ha/deviceList", json={"appKey": app_key}, headers=headers)
            resp_data = await result.json()

            # --- KORRIGIERTE LOGIK ---
            if resp_data.get("code") != 200:
                _LOGGER.error("Zendure API error: Code %s - Message: %s", resp_data.get("code"), resp_data.get("msg"))
                return None

            if not resp_data.get("success") or not isinstance(resp_data.get("data"), dict):
                _LOGGER.error("Invalid Zendure API response structure")
                return None

            result_data = resp_data["data"]

            if not result_data.get("deviceList"):
                _LOGGER.error("Zendure API does not reply any devices")
                return None

            if not result_data.get("mqtt"):
                _LOGGER.error("Zendure API does not reply any mqtt info")
                return None

            return result_data

        except Exception as e:
            # KEIN f-String beim Logger!
            _LOGGER.error("Unable to connect to Zendure %s!", e)
            _LOGGER.error(traceback.format_exc())
            return None

    @staticmethod
    async def connect(hass: HomeAssistant, data: dict[str, Any], reload: bool) -> dict[str, Any] | None:
        """Connect to the Zendure API and handle storage fallback."""
        try:
            # Ruft jetzt unsere neue, saubere api_ha Methode auf
            devices = await ZendureApi.api_ha(hass, data)
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

    def mqtt_connect(self, client: Any, userdata: Any, _flags: Any, rc: Any, _props: Any) -> None:
        _LOGGER.info("Client %s connected to MQTT broker, return code: %s", userdata, rc)
        if userdata == "zendure":
            for device in self.devices.values():
                if client == device.zendure:
                    client.subscribe(f"iot/{device.prodkey}/{device.deviceId}/#")
                    if self.mqtt_cloud.is_connected():
                        self.mqtt_cloud.unsubscribe(f"/{device.prodkey}/{device.deviceId}/#")
                        self.mqtt_cloud.unsubscribe(f"iot/{device.prodkey}/{device.deviceId}/#")
        else:
            for device in self.devices.values():
                client.subscribe(f"/{device.prodkey}/{device.deviceId}/#")
                client.subscribe(f"iot/{device.prodkey}/{device.deviceId}/#")

    def mqtt_disconnect(self, _client: Any, userdata: Any, _flags: Any, rc: Any, _props: Any) -> None:
        _LOGGER.info("Client %s disconnected from MQTT broker, return code: %s", userdata, rc)

    def mqtt_msg_cloud(self, client: Any, _userdata: Any, msg: Any) -> None:
        if msg.payload is None or not msg.payload:
            return
        try:
            topics = msg.topic.split("/", 3)

            if len(topics) < 4:
                _LOGGER.warning("Invalid MQTT topic format: %s (expected 4 segments)", msg.topic)
                return

            deviceId = topics[2]

            if (device := self.devices.get(deviceId, None)) is not None:
                try:
                    payload = json.loads(msg.payload.decode())
                except json.JSONDecodeError as err:
                    _LOGGER.error("Failed to decode JSON from device %s: %s", deviceId, err)
                    return
                except UnicodeDecodeError as err:
                    _LOGGER.error("Failed to decode payload encoding from device %s: %s", deviceId, err)
                    return

                if "isHA" in payload:
                    return

                if self.mqtt_logging:  # 🔴 FIX: self.mqttLogging -> self.mqtt_logging
                    safe_topic = msg.topic.replace(device.deviceId, device.name).replace(device.snNumber, "snxxx")
                    _LOGGER.info("Topic: %s => %s", safe_topic, payload)

                if device.mqttMessage(topics[3], payload) and device.mqtt != client:
                    device.mqtt = client
                    device.setStatus()

            else:
                _LOGGER.debug("Unknown device: %s => %s", deviceId, msg.topic)

        except Exception:
            _LOGGER.exception("Unexpected error in MQTT cloud message handler")

    def mqtt_msg_local(self, client: Any, _userdata: Any, msg: Any) -> None:
        if msg.payload is None or not msg.payload or len(self.devices) == 0:
            return
        try:
            topics = msg.topic.split("/", 3)

            if len(topics) < 4:
                _LOGGER.warning("Invalid local MQTT topic format: %s (expected 4 segments)", msg.topic)
                return

            deviceId = topics[2]

            if (device := self.devices.get(deviceId, None)) is not None:
                try:
                    payload = json.loads(msg.payload.decode())
                except json.JSONDecodeError as err:
                    _LOGGER.error("Failed to decode JSON from local device %s: %s", deviceId, err)
                    return
                except UnicodeDecodeError as err:
                    _LOGGER.error("Failed to decode local payload encoding from device %s: %s", deviceId, err)
                    return

                if "isHA" in payload:
                    return

                if self.mqtt_logging:  # 🔴 FIX: self.mqttLogging -> self.mqtt_logging
                    safe_topic = msg.topic.replace(device.deviceId, device.name).replace(device.snNumber, "snxxx")
                    _LOGGER.info("Local topic: %s => %s", safe_topic, payload)

                if device.mqttMessage(topics[3], payload):
                    if device.mqtt != client:
                        device.mqtt = client
                        device.setStatus()

                    if device.zendure is not None and device.zendure.is_connected():
                        payload["isHA"] = True
                        device.zendure.publish(msg.topic, json.dumps(payload, default=lambda o: o.__dict__))
            else:
                _LOGGER.debug("Local message from unknown device %s: %s", msg.topic, deviceId)

        except Exception:
            _LOGGER.exception("Unexpected error in MQTT local message handler")

    def mqtt_msg_device(self, _client: Any, _userdata: Any, msg: Any) -> None:
        if msg.payload is None or not msg.payload:
            return
        try:
            topics = msg.topic.split("/", 3)
            if len(topics) < 4:
                return

            deviceId = topics[2]

            if self.devices.get(deviceId, None) is not None and topics[0] == "iot":
                if self.mqtt_local is not None and self.mqtt_local.is_connected():
                    self.mqtt_local.publish(msg.topic, msg.payload)

        except Exception as err:
            _LOGGER.error("Error in mqtt_msg_device: %s", err)