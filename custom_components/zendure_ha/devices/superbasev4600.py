"""Module for the SuperBaseV4600 device integration in Home Assistant."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.zendure_ha.device import ZendureLegacy
from custom_components.zendure_ha.select import ZendureSelect
from custom_components.zendure_ha.switch import ZendureSwitch

_LOGGER = logging.getLogger(__name__)


class SuperBaseV4600(ZendureLegacy):
    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, definition: Any, parent: str | None = None) -> None:
        """Initialise SuperBaseV4600."""
        # Hinweis: Parameter 'prodName' wurde zu 'name' angepasst für Konsistenz zur Basisklasse
        super().__init__(hass, deviceId, name, definition["productModel"], definition, parent)

        self.setLimits(-900, 800)
        self.maxSolar = -900

        # --- NEU: Port-Konfiguration ---
        self.pv_port_count = 1  # Gerät hat einen DC-Eingang (maxSolar ist definiert)
        # _has_offgrid bleibt False (Standard), da keine Offgrid-Steckdose existiert
        # ------------------------------

        # Gerätespezifische Entitäten (unverändert)
        self.acSwitch = ZendureSwitch(self, "acSwitch", self.entityWrite, None, "switch", 1)
        self.dcSwitch = ZendureSelect(self, "dcSwitch", {0: "off", 1: "on"}, self.entityWrite, 1)

        # --- NEU: Port-Initialisierung ---
        # Muss ganz am Ende stehen, damit maxSolar und Flags gesetzt sind
        self._init_power_ports()
        # ---------------------------------

    async def charge(self, power: int) -> int:
        _LOGGER.info("Power charge %s => %s", self.name, power)
        self.mqttInvoke(
            {
                "arguments": [
                    {
                        "autoModelProgram": 2,
                        "autoModelValue": {
                            "chargingType": 1,
                            "chargingPower": -power,
                            "freq": 0,
                            "outPower": 0,
                        },
                        "msgType": 1,
                        "autoModel": 8,
                    }
                ],
                "function": "deviceAutomation",
            }
        )
        return power

    async def discharge(self, power: int) -> int:
        _LOGGER.info("Power discharge %s => %s", self.name, power)
        self.mqttInvoke(
            {
                "arguments": [
                    {
                        "autoModelProgram": 2,
                        "autoModelValue": {
                            "chargingType": 0,
                            "chargingPower": 0,
                            "freq": 0,
                            "outPower": power,
                        },
                        "msgType": 1,
                        "autoModel": 8,
                    }
                ],
                "function": "deviceAutomation",
            }
        )
        return power

    async def power_off(self) -> None:
        """Set the power off."""
        self.mqttInvoke(
            {
                "arguments": [
                    {
                        "autoModelProgram": 0,
                        "autoModelValue": {
                            "chargingType": 0,
                            "chargingPower": 0,
                            "freq": 0,
                            "outPower": 0,
                        },
                        "msgType": 1,
                        "autoModel": 0,
                    }
                ],
                "function": "deviceAutomation",
            }
        )