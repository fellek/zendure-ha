"""Zendure Battery data model."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .entity import EntityDevice


class ZendureBattery(EntityDevice):
    """Zendure Battery class for devices."""

    def __init__(self, hass: HomeAssistant, sn: str, parent: EntityDevice) -> None:
        """Initialize Device."""
        self.kWh = 0.0
        model = "???"
        match sn[0]:
            case "A": model = "AIO2400" if sn[3] == "3" else "AB1000"; self.kWh = 2.4 if sn[3] == "3" else 0.96
            case "B": model = "AB1000S"; self.kWh = 0.96
            case "C": model = "AB2000" + ("S" if sn[3] == "F" else "X" if sn[3] == "E" else ""); self.kWh = 1.92
            case "F": model = "AB3000"; self.kWh = 2.88
            case "G": model = "AB3000L"; self.kWh = 2.88
            case "J": model = "I2400"; self.kWh = 2.4
            case _: model = "Unknown"; self.kWh = 0.0
        name = f"{model} {sn[-5:]}".strip()
        super().__init__(hass, sn, name, model, "", sn, parent.deviceId)
        self.attr_device_info["serial_number"] = sn
