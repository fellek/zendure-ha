"""Module for the Solarflow1600 device integration in Home Assistant."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.zendure_ha.device import ZendureZenSdk

_LOGGER = logging.getLogger(__name__)


class SolarFlow1600(ZendureZenSdk):
    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, definition: Any, parent: str | None = None) -> None:
        """Initialise SolarFlow1600."""
        # Hinweis: Parameter 'prodName' wurde zu 'name' angepasst für Konsistenz zur Basisklasse
        super().__init__(hass, deviceId, name, definition["productModel"], definition, parent)
        self.setLimits(-1600, 1600)
        self.maxSolar = -1600
        self.pv_port_count = 1       # Hat einen DC-Eingang
        self._has_offgrid = True     # Hat eine Offgrid-Steckdose

        self._init_power_ports()