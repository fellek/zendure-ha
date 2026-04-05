"""Module for SolarFlow800 integration."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.zendure_ha.device import ZendureZenSdk

_LOGGER = logging.getLogger(__name__)


class SolarFlow800(ZendureZenSdk):
    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, definition: Any, parent: str | None = None) -> None:
        """Initialise SolarFlow800."""
        super().__init__(hass, deviceId, name, definition["productModel"], definition, parent)
        self.setLimits(-1000, 800)
        self.maxSolar = -1200

        self.pv_port_count = 1
        self._init_power_ports()


class SolarFlow800Plus(ZendureZenSdk):
    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, definition: Any, parent: str | None = None) -> None:
        """Initialise SolarFlow800Plus."""
        super().__init__(hass, deviceId, name, definition["productModel"], definition, parent)
        self.setLimits(-1000, 800)
        self.maxSolar = -1500

        self.pv_port_count = 1
        self._init_power_ports()


class SolarFlow800Pro(ZendureZenSdk):
    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, definition: Any, parent: str | None = None) -> None:
        """Initialise SolarFlow800Pro."""
        super().__init__(hass, deviceId, name, definition["productModel"], definition, parent)
        self.setLimits(-1000, 800)
        self.maxSolar = -1200
        self.pv_port_count = 1
        self._has_offgrid = True
        self._init_power_ports()