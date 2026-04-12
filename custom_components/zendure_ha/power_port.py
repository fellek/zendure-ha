"""Abstrakte und konkrete Strom-Anschlusspunkte (Power Ports) für das Zendure-Management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .const import SmartMode

if TYPE_CHECKING:
    from .sensor import ZendureSensor
    from .device import ZendureDevice


class PowerPort(ABC):
    """Abstrakte Basisklasse für jeden Strom-Input/Output."""

    def __init__(self, name: str, is_input_only: bool = False):
        self.name = name
        self.is_input_only = is_input_only

    @property
    @abstractmethod
    def power(self) -> int:
        """
        Gibt die aktuelle Leistung in Watt zurück.
        Konvention: + = Verbrauch, - = Erzeugung.
        """
        pass


# @ todo rename to GridSmartMeter()
class GridPowerPort(PowerPort):
    """Representiert den P1 Zähler (AC Strom vom Netz)."""

    def __init__(self):
        super().__init__(name="AC Grid (P1)", is_input_only=False)
        self._power = 0

    def update_state(self, power: int):
        """Wird vom Manager aufgerufen, wenn sich der P1-Zähler ändert."""
        self._power = power

    @property
    def power(self) -> int:
        # P1 Positiv = Netzbezug (Verbrauch), P1 Negativ = Einspeisung (Erzeugung)
        return self._power


class AcPowerPort(PowerPort):
    """Representiert die AC-Netzverbindung eines Geräts (gridInputPower / outputHomePower)."""

    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"AC Grid ({device.name})", is_input_only=False)
        self.device = device

    @property
    def power(self) -> int:
        """Positiv = Einspeisung (outputHomePower), Negativ = Netzbezug (gridInputPower)."""
        return self.device.homeOutput.asInt - self.device.homeInput.asInt

    @property
    def grid_consumption(self) -> int:
        """Power drawn from grid (gridInputPower), always >= 0."""
        return self.device.homeInput.asInt

    @property
    def feed_in(self) -> int:
        """Power fed into home (outputHomePower), always >= 0."""
        return self.device.homeOutput.asInt

    @property
    def is_charging(self) -> bool:
        """Device is currently drawing from grid."""
        return self.device.homeInput.asInt > 0

    @property
    def is_discharging(self) -> bool:
        """Device is currently feeding into home."""
        return self.device.homeOutput.asInt > 0


class BatteryPowerPort(PowerPort):
    """Representiert den Netto-Batterie-Leistungsfluss eines Geräts."""

    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"Battery ({device.name})", is_input_only=False)
        self.device = device

    @property
    def power(self) -> int:
        """Positive = discharging (battery -> system), negative = charging (system -> battery)."""
        return self.device.batteryOutput.asInt - self.device.batteryInput.asInt

    @property
    def charge_power(self) -> int:
        """Raw charge power (always >= 0)."""
        return self.device.batteryInput.asInt

    @property
    def discharge_power(self) -> int:
        """Raw discharge power (always >= 0)."""
        return self.device.batteryOutput.asInt

    @property
    def is_charging(self) -> bool:
        """Battery is currently charging."""
        return self.device.batteryInput.asInt > 0

    @property
    def is_discharging(self) -> bool:
        """Battery is actively discharging (above idle offset)."""
        return self.device.batteryOutput.asInt > SmartMode.POWER_IDLE_OFFSET


class OffGridPowerPort(PowerPort):
    """Representiert die integrierte Offgrid-Steckdose (Input/Output)."""

    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"Offgrid ({device.name})", is_input_only=False)
        self.device = device

    @property
    def power(self) -> int:
        """Offgrid netto: positiv = Verbrauch, negativ = Einspeisung (externer Akku/MWR)."""
        return self.device.pwr_offgrid

    @property
    def consumption(self) -> int:
        """Reine Last an Offgrid-Steckdose (>= 0)."""
        return max(0, self.device.pwr_offgrid)

    @property
    def feed_in(self) -> int:
        """Einspeisung über Offgrid (>= 0). Externer Akku oder Mikrowechselrichter."""
        return max(0, -self.device.pwr_offgrid)


class DcSolarPowerPort(PowerPort):
    """Representiert die DC Solar-Eingänge eines Geräts (1 bis N)."""

    def __init__(self, device: ZendureDevice, sensors: list[ZendureSensor]):
        super().__init__(name=f"DC Solar ({device.name})", is_input_only=True)
        self.device = device
        self._sensors = sensors

    @property
    def power(self) -> int:

        return self.total_raw_solar

    @property
    def total_raw_solar(self) -> int:
        """Summiert alle zugewiesenen DC-Eingänge auf."""
        return sum(sensor.asInt for sensor in self._sensors)