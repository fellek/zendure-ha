"""Tests for `DevicePowerFlowStateMachine` (vorschlag-06).

The state machine is exercised without instantiating a real `ZendureDevice`:
we hand it a lightweight stand-in that exposes only the attributes the
state machine reads or writes. That keeps these tests free of HA imports
and guards the transitions in `device.py:update_power_flow_state`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from custom_components.zendure_ha.const import DeviceState, PowerFlowState, SmartMode
from custom_components.zendure_ha.device_components import DevicePowerFlowStateMachine

from .conftest import FakeSensor


@dataclass
class FakeBatteryPort:
    power: int = 0
    charge_power: int = 0
    discharge_power: int = 0

    @property
    def is_charging(self) -> bool:
        return self.charge_power > 0

    @property
    def is_discharging(self) -> bool:
        return self.discharge_power > SmartMode.POWER_IDLE_OFFSET


@dataclass
class FakeInverterLossPort:
    power: int = 0


@dataclass
class FakeSMDevice:
    """Minimal device surface used by `DevicePowerFlowStateMachine`."""

    name: str = "sm-fake"
    state: DeviceState = DeviceState.ACTIVE
    power_flow_state: PowerFlowState = PowerFlowState.IDLE
    electricLevel: FakeSensor = field(default_factory=FakeSensor)  # noqa: N815
    wakeup_entered: datetime = datetime.min
    batteryPort: FakeBatteryPort = field(default_factory=FakeBatteryPort)  # noqa: N815
    inverterLossPort: FakeInverterLossPort = field(default_factory=FakeInverterLossPort)  # noqa: N815
    power_flow_sensor: FakeSensor = field(default_factory=FakeSensor)
    inverterLoss: FakeSensor = field(default_factory=FakeSensor)  # noqa: N815


def _make(state: DeviceState, *, charging: int = 0, discharging: int = 0,
          flow: PowerFlowState = PowerFlowState.IDLE) -> FakeSMDevice:
    d = FakeSMDevice(state=state, power_flow_state=flow)
    d.batteryPort.charge_power = charging
    d.batteryPort.discharge_power = discharging
    d.batteryPort.power = discharging - charging
    return d


def test_offline_forces_power_flow_off_and_skips_inverter_loss_update() -> None:
    d = _make(DeviceState.OFFLINE, flow=PowerFlowState.IDLE)
    d.inverterLossPort.power = 42
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.OFF
    assert d.power_flow_sensor.value == PowerFlowState.OFF.value
    # OFFLINE path returns early, so inverter loss sensor stays at default
    assert d.inverterLoss.value == 0


def test_active_charging_reports_charge() -> None:
    d = _make(DeviceState.ACTIVE, charging=200)
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.CHARGE


def test_active_discharging_reports_discharge() -> None:
    d = _make(DeviceState.ACTIVE, discharging=200)
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.DISCHARGE


def test_active_idle_reports_idle() -> None:
    d = _make(DeviceState.ACTIVE)
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.IDLE


def test_socfull_discharging_reports_discharge_else_idle() -> None:
    d = _make(DeviceState.SOCFULL, discharging=200)
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.DISCHARGE

    d2 = _make(DeviceState.SOCFULL, charging=200)
    DevicePowerFlowStateMachine(d2).update()
    # SOCFULL must never escalate to CHARGE
    assert d2.power_flow_state == PowerFlowState.IDLE


def test_socempty_charging_reports_charge_else_idle() -> None:
    d = _make(DeviceState.SOCEMPTY, charging=200)
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.CHARGE

    d2 = _make(DeviceState.SOCEMPTY, discharging=200)
    DevicePowerFlowStateMachine(d2).update()
    # SOCEMPTY must never escalate to DISCHARGE
    assert d2.power_flow_state == PowerFlowState.IDLE


def test_wakeup_holds_until_battery_exceeds_idle_offset() -> None:
    d = _make(DeviceState.ACTIVE, flow=PowerFlowState.WAKEUP)
    # Battery below idle offset → stay in WAKEUP, no timestamp written
    d.batteryPort.power = SmartMode.POWER_IDLE_OFFSET
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.WAKEUP
    assert d.wakeup_entered == datetime.min


def test_wakeup_releases_when_battery_starts_moving() -> None:
    d = _make(DeviceState.ACTIVE, charging=200, flow=PowerFlowState.WAKEUP)
    d.batteryPort.power = -200  # charging => negative
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_state == PowerFlowState.CHARGE
    assert d.wakeup_entered != datetime.min


def test_power_flow_sensor_mirrors_state_value() -> None:
    d = _make(DeviceState.ACTIVE, discharging=200)
    DevicePowerFlowStateMachine(d).update()
    assert d.power_flow_sensor.value == PowerFlowState.DISCHARGE.value


def test_inverter_loss_sensor_updated_on_non_offline_path() -> None:
    d = _make(DeviceState.ACTIVE, discharging=200)
    d.inverterLossPort.power = 45
    DevicePowerFlowStateMachine(d).update()
    assert d.inverterLoss.value == 45
