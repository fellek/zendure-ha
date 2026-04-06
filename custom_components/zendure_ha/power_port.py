"""Abstrakte und konkrete Strom-Anschlusspunkte (Power Ports) für das Zendure-Management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

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


class OffGridPowerPort(PowerPort):
    """Representiert die integrierte Offgrid-Steckdose (Input/Output)."""

    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"Offgrid ({device.name})", is_input_only=False)
        self.device = device

    @property
    def power(self) -> int:
        """
        Im Zendure-Ökosystem kann pwr_offgrid negativ sein, wenn intern
        Solarstrom an der Steckdose anliegt. Das zählt aber eigentlich zum
        DC Solar Port. Für die Nulleinspeisung ist hier nur der reine
        Verbrauch (Last an der Steckdose) relevant.
        """
        return max(0, self.device.pwr_offgrid)


class DcSolarPowerPort(PowerPort):
    """Representiert die DC Solar-Eingänge eines Geräts (1 bis N)."""

    def __init__(self, device: ZendureDevice, sensors: list[ZendureSensor]):
        super().__init__(name=f"DC Solar ({device.name})", is_input_only=True)
        self.device = device
        self._sensors = sensors

    @property
    def power(self) -> int:
        """
        Für die Basis-Setpoint-Berechnung ist DC Solar "unsichtbar",
        da der Wechselrichter den Solarstrom automatisch durchreicht
        (P1 reagiert darauf ohnehin).
        """
        return 0

    @property
    def total_raw_solar(self) -> int:
        """Summiert alle zugewiesenen DC-Eingänge auf."""
        return sum(sensor.asInt for sensor in self._sensors)