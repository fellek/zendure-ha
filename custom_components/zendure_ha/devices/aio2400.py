"""Module for the AIO 2400 device integration in Home Assistant."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.zendure_ha.device import ZendureLegacy

_LOGGER = logging.getLogger(__name__)


class AIO2400(ZendureLegacy):
    """AIO 2400 cannot charge using AC."""

    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, definition: Any, parent: str | None = None) -> None:
        """Initialise AIO2400."""
        # Hinweis: Parameter 'prodName' wurde zu 'name' angepasst für Konsistenz zur Basisklasse
        super().__init__(hass, deviceId, name, definition["productModel"], definition, parent)

        self.setLimits(0, 1200)
        self.maxSolar = -1200

        # --- NEU: Port-Konfiguration ---
        self.pv_port_count = 1  # Gerät hat einen DC-Eingang (maxSolar ist definiert)
        # _has_offgrid bleibt False (Standard), da keine Offgrid-Steckdose existiert
        # ------------------------------

        # --- NEU: Port-Initialisierung ---
        # Muss ganz am Ende stehen, damit maxSolar und Flags gesetzt sind
        self._init_power_ports()
        # ---------------------------------

    async def charge(self, power: int) -> int:
        """Überschrieben, da das AIO 2400 nicht über AC laden kann."""
        _LOGGER.info("No AC charge for %s available", self.name)
        return 0

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
                            "outPower": max(0, power),
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