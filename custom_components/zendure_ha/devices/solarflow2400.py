"""Module for the Solarflow2400AC device integration."""

from typing import Any
from homeassistant.core import HomeAssistant
from custom_components.zendure_ha.device import ZendureZenSdk

class SolarFlow2400AC(ZendureZenSdk):
    def __init__(self, hass: HomeAssistant, deviceId: str, prodName: str, definition: Any) -> None:
        super().__init__(hass, deviceId, prodName, definition["productModel"], definition)
        self.setLimits(-2400, 2400)
        self.maxSolar = -2400
        self._has_offgrid = True
        self._init_power_ports()

class SolarFlow2400AC_Plus(ZendureZenSdk):
    def __init__(self, hass: HomeAssistant, deviceId: str, prodName: str, definition: Any) -> None:
        super().__init__(hass, deviceId, prodName, definition["productModel"], definition)
        self.setLimits(-3200, 2400)
        self.maxSolar = -2400
        self._has_offgrid = True
        self._init_power_ports()

class SolarFlow2400Pro(ZendureZenSdk):
    def __init__(self, hass: HomeAssistant, deviceId: str, prodName: str, definition: Any) -> None:
        super().__init__(hass, deviceId, prodName, definition["productModel"], definition)
        self.setLimits(-3200, 2400)
        self.maxSolar = -3000
        self.pv_port_count = 4
        self._has_offgrid = True
        self._init_power_ports()