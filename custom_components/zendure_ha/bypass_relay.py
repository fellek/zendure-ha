"""BypassRelay — kapselt den MQTT-'pass'-Kanal für Zendure-Geräte."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass

from .binary_sensor import ZendureBinarySensor
from .sensor import ZendureSensor

if TYPE_CHECKING:
    from .device import ZendureDevice

_MODES: dict[int, str] = {0: "off", 2: "reverse", 3: "input"}


class BypassRelay(ZendureBinarySensor):
    """Kapselt den MQTT-'pass'-Kanal: binary HA-Entity + Modus-Sensor + typisierte Properties.

    pass-Werte: 0 = kein Bypass, 2 = BYPASS-REVERSE, 3 = BYPASS-INPUT.
    """

    def __init__(self, device: ZendureDevice) -> None:
        super().__init__(device, "pass")          # registriert unter entities["pass"]
        self._raw: int = 0
        self._mode = ZendureSensor(device, "pass_mode")
        self._mode._attr_options = ["off", "reverse", "input"]
        self._mode._attr_device_class = SensorDeviceClass.ENUM

    def update_value(self, value: Any) -> bool:
        try:
            self._raw = int(value)
        except (ValueError, TypeError):
            self._raw = 0
        self._mode.update_value(_MODES.get(self._raw, "off"))
        return super().update_value(value)

    @property
    def is_active(self) -> bool:
        """True wenn Bypass aktiv (pass != 0)."""
        return self._raw != 0

    @property
    def is_reverse(self) -> bool:
        """True wenn BYPASS-REVERSE (pass == 2)."""
        return self._raw == 2

    @property
    def is_input(self) -> bool:
        """True wenn BYPASS-INPUT (pass == 3)."""
        return self._raw == 3
