"""Abstrakte und konkrete Strom-Anschlusspunkte (Power Ports) für das Zendure-Management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .const import DeviceState, SmartMode

if TYPE_CHECKING:
    from .sensor import ZendureSensor
    from .device import ZendureDevice


class PowerPort(ABC):
    """Abstrakte Basisklasse für jeden Strom-Input/Output."""

    def __init__(self, name: str, is_input_only: bool = False):
        self.name = name
        self.is_input_only = is_input_only
        self._cached_power: int | None = None

    def invalidate(self) -> None:
        """Cache leeren; wird vom DevicePortBundle zum Zyklusstart aufgerufen."""
        self._cached_power = None

    @property
    def power(self) -> int:
        """
        Gibt die aktuelle Leistung in Watt zurück.
        Konvention: + = Verbrauch, - = Erzeugung.
        Innerhalb eines Zyklus gecacht; invalidate() leert den Cache.
        """
        if self._cached_power is None:
            self._cached_power = self._compute_power()
        return self._cached_power

    @abstractmethod
    def _compute_power(self) -> int:
        pass


class GridSmartmeter(PowerPort):
    """Representiert den P1 Zähler (AC Strom vom Netz)."""

    def __init__(self):
        super().__init__(name="AC Grid (P1)", is_input_only=False)
        self._power = 0

    def update_state(self, power: int):
        """Wird vom Manager aufgerufen, wenn sich der P1-Zähler ändert."""
        self._power = power
        self.invalidate()

    def _compute_power(self) -> int:
        # P1 Positiv = Netzbezug (Verbrauch), P1 Negativ = Einspeisung (Erzeugung)
        return self._power


class ConnectorPowerPort(PowerPort):
    """Repräsentiert die AC-Netzverbindung eines Geräts (gridInputPower / outputHomePower)."""

    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"AC Grid ({device.name})", is_input_only=False)
        self.device = device

    def _compute_power(self) -> int:
        """Positiv = Einspeisung (outputHomePower), Negativ = Netzbezug (gridInputPower)."""
        return self.device.homeOutput.asInt - self.device.homeInput.asInt

    @property
    def power_consumption(self) -> int:
        """Net power drawn from grid (always >= 0)."""
        return max(0, -self.power)

    @property
    def power_production(self) -> int:
        """Net power fed into home (always >= 0)."""
        return max(0, self.power)

    @property
    def is_consuming(self) -> bool:
        """Device is currently drawing from grid."""
        return self.power < 0

    @property
    def is_producing(self) -> bool:
        """Device is currently feeding into home."""
        return self.power > 0


class BatteryPowerPort(PowerPort):
    """Representiert den Netto-Batterie-Leistungsfluss eines Geräts."""

    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"Battery ({device.name})", is_input_only=False)
        self.device = device

    def _compute_power(self) -> int:
        """Positive = discharging (battery -> system), negative = charging (system -> battery)."""
        return self.device.batteryOutput.asInt - self.device.batteryInput.asInt

    @property
    def charge_power(self) -> int:
        """Net charge power (always >= 0)."""
        return max(0, -self.power)

    @property
    def discharge_power(self) -> int:
        """Net discharge power (always >= 0)."""
        return max(0, self.power)

    @property
    def is_charging(self) -> bool:
        """Battery is currently charging."""
        return self.power < 0

    @property
    def is_discharging(self) -> bool:
        """Battery is actively discharging (above idle offset)."""
        return self.power > SmartMode.POWER_IDLE_OFFSET


class OffGridPowerPort(PowerPort):
    """Representiert die integrierte Offgrid-Steckdose (Input/Output)."""

    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"Offgrid ({device.name})", is_input_only=False)
        self.device = device

    def _compute_power(self) -> int:
        """Offgrid netto: positiv = Verbrauch, negativ = Einspeisung (externer Akku/MWR)."""
        return self.device.pwr_offgrid

    @property
    def power_consumption(self) -> int:
        """Reine Last an Offgrid-Steckdose (>= 0)."""
        return max(0, self.power)

    @property
    def power_production(self) -> int:
        """Einspeisung über Offgrid (>= 0). Externer Akku oder Mikrowechselrichter."""
        return max(0, -self.power)


class DcSolarPowerPort(PowerPort):
    """Representiert die DC Solar-Eingänge eines Geräts (1 bis N)."""

    def __init__(self, device: ZendureDevice, sensors: list[ZendureSensor]):
        super().__init__(name=f"DC Solar ({device.name})", is_input_only=True)
        self.device = device
        self._sensors = sensors

    def _compute_power(self) -> int:
        return self.total_solar_power

    @property
    def total_solar_power(self) -> int:
        """Summiert alle zugewiesenen DC-Eingänge auf."""
        return sum(sensor.asInt for sensor in self._sensors)


class InverterLossPowerPort(PowerPort):
    """Schätzt den Selbstverbrauch des Inverters.

    Zwei Strategien:
    - energy_balance: Wenn Offgrid AN und gridOffPower > 0, berechne aus Firmware-Bilanz.
      Obergrenze, da offgrid_load nicht von self_consumption separierbar ist.
    - model: Fester Modellwert pro Gerätezustand (Fallback).

    Modellwerte (SF2400AC, P1-kreuzvalidiert 2026-04-14):
    - Standby: ~25W (gemessen 31W mit Offgrid-Schaltung aktiv)
    - Aktiv:   ~45W (gemessen 23-63W je nach Betriebspunkt)

    Gibt immer >= 0 zurück.
    """
    # @todo replace magic numbers with vars
    def __init__(self, device: ZendureDevice, model_active_w: int = 45, model_standby_w: int = 25):
        super().__init__(name=f"Inverter Loss ({device.name})", is_input_only=False)
        self.device = device
        self._model_active_w = model_active_w
        self._model_standby_w = model_standby_w

    def _compute_power(self) -> int:
        """Geschätzter Selbstverbrauch in Watt (immer >= 0)."""
        measured = self._from_energy_balance()
        if measured is not None:
            return measured
        return self._from_model()

    @property
    def strategy(self) -> str:
        """Aktuell verwendete Schätzmethode (für Logging/Debug)."""
        if self._from_energy_balance() is not None:
            return "energy_balance"
        return "model"

    def _from_energy_balance(self) -> int | None:
        """Energiebilanz wenn Offgrid AN und gridOffPower > 0.

        Berechnet: all_in - all_out = gridOffPower = offgrid_load + self_consumption.
        Gilt als Obergrenze, da offgrid_load nicht separierbar.
        """
        if not self.device._has_offgrid:
            return None
        offgrid = self.device.offgrid_power
        # @todo handle power production on offgrid-socket
        if offgrid <= 0:
            return None

        bat_out = self.device.batteryPort.discharge_power
        bat_in = self.device.batteryPort.charge_power
        conn_in = self.device.connectorPort.power_consumption
        conn_out = self.device.connectorPort.power_production
        solar = self.device.solarPort.total_solar_power if self.device.solarPort else 0

        all_in = conn_in + solar + bat_out
        all_out = bat_in + conn_out
        return max(0, all_in - all_out)

    def _from_model(self) -> int:
        """Modellbasierte Schätzung als Fallback."""
        if self.device.state == DeviceState.OFFLINE:
            return 0
        is_active = (self.device.batteryPort.charge_power > 0
                     or self.device.batteryPort.discharge_power > 0)
        return self._model_active_w if is_active else self._model_standby_w