"""Zendure ZenSDK device: HTTP transport for local SDK-based devices."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .device import ZendureDevice, CONST_HEADER, CONST_TIMEOUT
from .entity import EntityZendure
from . import mqtt_protocol
from .select import ZendureRestoreSelect

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class ZendureZenSdk(ZendureDevice):
    """Zendure Zen SDK class for devices."""

    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, model: str, definition: dict[str, str], parent: str | None = None) -> None:
        """Initialize Device."""
        self.session = async_get_clientsession(hass, verify_ssl=False)
        super().__init__(hass, deviceId, name, model, definition, parent)
        self.connection = ZendureRestoreSelect(self, "connection", {0: "cloud", 2: "zenSDK"}, self.mqttSelect, 0)
        self.httpid = 0

    async def mqttSelect(self, select: Any, _value: Any) -> None:
        # During restore, api is not yet assigned — skip until loadDevices() completes
        if not hasattr(self, "api"):
            _LOGGER.debug("mqttSelect %s skipped: api not yet initialized (restore)", self.name)
            return

        self.mqtt = None
        match select.value:
            case 0:
                self.api.mqtt_cloud.unsubscribe(f"/{self.prodkey}/{self.deviceId}/#")
                self.api.mqtt_cloud.unsubscribe(f"iot/{self.prodkey}/{self.deviceId}/#")

            case 2:
                self.api.mqtt_cloud.unsubscribe(f"/{self.prodkey}/{self.deviceId}/#")
                self.api.mqtt_cloud.unsubscribe(f"iot/{self.prodkey}/{self.deviceId}/#")

        _LOGGER.debug("Mqtt selected %s", self.name)

    async def entityWrite(self, entity: EntityZendure, value: Any) -> None:
        if entity.translation_key is None:
            _LOGGER.error("Entity %s has no translation_key, cannot write property", entity.name)
            return

        if self.online and self.connection.value == 0:
            await super().entityWrite(entity, value)
        else:
            _LOGGER.info("Writing property %s %s => %s", self.name, entity.propertyName, value)
            await self.httpPost("properties/write", {"properties": {entity.propertyName: value}})

    async def dataRefresh(self, update_count: int) -> None:
        if update_count == 0 and not self.online:
            json = await self.httpGet("properties/report")
            await self.mqttProperties(json)

    async def update_state(self) -> bool:
        """Get the current power."""
        if self.connection.value != 0:
            json = await self.httpGet("properties/report")
            await self.mqttProperties(json)

        return await super().update_state()

    async def charge(self, power: int, _off: bool = False) -> int:
        """Set charge power."""
        _LOGGER.info("Power charge %s => %s", self.name, power)
        await self.doCommand({"properties": {"smartMode": 0 if power == 0 else 1, "acMode": 1, "outputLimit": 0, "inputLimit": -power}})
        return power

    async def discharge(self, power: int) -> int:
        _LOGGER.info("Power discharge %s => %s", self.name, power)
        await self.doCommand({"properties": {"smartMode": 0 if power == 0 else 1, "acMode": 2, "outputLimit": power, "inputLimit": 0}})
        return power

    async def power_off(self) -> None:
        """Set the power off."""
        _LOGGER.info("Power off %s => %s", self.name)
        await self.doCommand({"properties": {"smartMode": 0, "acMode": 2, "outputLimit": 0, "inputLimit": 0}})

    async def doCommand(self, command: Any) -> None:
        if self.connection.value != 0:
            await self.httpPost("properties/write", command)
        else:
            self.mqttPublish(self.topic_write, command, self.mqtt)

    async def httpGet(self, url: str, key: str | None = None) -> dict[str, Any]:
        try:
            url = f"http://{self.ipAddress}/{url}"
            response = await self.session.get(url, headers=CONST_HEADER, timeout=CONST_TIMEOUT)
            payload = json.loads(await response.text())
            self.lastseen = datetime.now()
            return payload if key is None else payload.get(key, {})
        except Exception as e:
            _LOGGER.error("%s for %s during httpGet: %s", type(e).__name__, self.name, e)
            self.lastseen = datetime.min
        return {}

    async def httpPost(self, url: str, command: Any) -> bool:
        try:
            self.httpid += 1
            command["id"] = self.httpid
            command["sn"] = self.snNumber
            url = f"http://{self.ipAddress}/{url}"
            await self.session.post(url, json=command, headers=CONST_HEADER, timeout=CONST_TIMEOUT)
        except Exception as e:
            _LOGGER.error("%s for %s during httpPost: %s", type(e).__name__, self.name, e)
            self.lastseen = datetime.min
            return False
        return True
