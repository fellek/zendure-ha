"""Tests for `DevicePowerFlowStateMachine.classify()` and `update()`.

The state machine is exercised without instantiating a real `ZendureDevice`:
we hand it a lightweight stand-in that exposes only the attributes the
state machine reads or writes.  That keeps these tests free of HA imports.

Classification matrix (see docs/power-charge-discharge-transition-analysis-2026-04-22.md §5):

  packState=0                                  → IDLE  (Standby / Bypass)
  packState=1, acMode=OUTPUT                   → IDLE  (CH→DIS firmware transition)
  packState=1, acMode=INPUT, is_charging       → CHARGE
  packState=1, acMode=INPUT, not charging      → IDLE  (startup ramp)
  packState=2, SOCEMPTY                        → IDLE  (guard §6.1)
  packState=2, bypass active                   → IDLE  (guard §6.2)
  packState=2, SOCFULL, is_discharging         → DISCHARGE
  packState=2, SOCFULL, not discharging        → IDLE
  packState=2, SOC <= minSoc+buffer            → IDLE  (SOC-guard §6.1)
  packState=2, is_discharging                  → DISCHARGE
  packState=2, wake_started_at expired         → IDLE  (timeout §6 self-heal)
  packState=2, no discharge, guards pass       → WAKEUP
  packState=other / not set                    → fallback: power-based classification
  OFFLINE                                      → OFF   (always)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from custom_components.zendure_ha.const import AcMode, DeviceState, PowerFlowState, SmartMode
from custom_components.zendure_ha.device_components import DevicePowerFlowStateMachine

from .conftest import FakeBypassRelay, FakeSensor


# ---------------------------------------------------------------------------
#  Fakes
# ---------------------------------------------------------------------------

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
    electricLevel: FakeSensor = field(default_factory=FakeSensor)          # noqa: N815
    minSoc: FakeSensor = field(default_factory=FakeSensor)                  # noqa: N815
    packState: FakeSensor = field(default_factory=FakeSensor)               # noqa: N815
    acMode: FakeSensor = field(                                              # noqa: N815
        default_factory=lambda: FakeSensor(value=AcMode.INPUT)
    )
    bypass: FakeBypassRelay = field(default_factory=FakeBypassRelay)
    wakeup_entered: datetime = datetime.min
    wake_started_at: datetime = datetime.min
    wakeup_committed: bool = False
    batteryPort: FakeBatteryPort = field(default_factory=FakeBatteryPort)   # noqa: N815
    inverterLossPort: FakeInverterLossPort = field(                          # noqa: N815
        default_factory=FakeInverterLossPort
    )
    power_flow_sensor: FakeSensor = field(default_factory=FakeSensor)
    inverterLoss: FakeSensor = field(default_factory=FakeSensor)             # noqa: N815


def _make(
    state: DeviceState = DeviceState.ACTIVE,
    *,
    charging: int = 0,
    discharging: int = 0,
    pack_state: int = 0,
    ac_mode: int = AcMode.INPUT,
    bypass_active: bool = False,
    soc: int = 50,
    min_soc: int = 10,
    flow: PowerFlowState = PowerFlowState.IDLE,
    wake_started_at: datetime = datetime.min,
) -> FakeSMDevice:
    d = FakeSMDevice(state=state, power_flow_state=flow, wake_started_at=wake_started_at)
    d.batteryPort.charge_power = charging
    d.batteryPort.discharge_power = discharging
    d.batteryPort.power = discharging - charging
    d.packState.value = pack_state
    d.acMode.value = ac_mode
    d.bypass.active = bypass_active
    d.electricLevel.value = soc
    d.minSoc.value = min_soc
    return d


def _sm(d: FakeSMDevice) -> DevicePowerFlowStateMachine:
    return DevicePowerFlowStateMachine(d)


# ---------------------------------------------------------------------------
#  Legacy / regression tests (updated for packState-based classify)
# ---------------------------------------------------------------------------

def test_offline_forces_off() -> None:
    d = _make(DeviceState.OFFLINE, pack_state=2, discharging=200)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.OFF
    assert d.power_flow_sensor.value == PowerFlowState.OFF.value


def test_offline_inverter_loss_is_zero() -> None:
    """The real InverterLossPort returns 0 for OFFLINE; update() reflects that."""
    d = _make(DeviceState.OFFLINE)
    d.inverterLossPort.power = 0  # matches real port behaviour for OFFLINE
    _sm(d).update()
    assert d.inverterLoss.value == 0


def test_active_charging_reports_charge() -> None:
    d = _make(DeviceState.ACTIVE, charging=200, pack_state=1, ac_mode=AcMode.INPUT)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.CHARGE


def test_active_discharging_reports_discharge() -> None:
    d = _make(DeviceState.ACTIVE, discharging=200, pack_state=2, soc=50, min_soc=10)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.DISCHARGE


def test_active_standby_reports_idle() -> None:
    d = _make(DeviceState.ACTIVE, pack_state=0)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.IDLE


def test_socfull_discharging_reports_discharge() -> None:
    d = _make(DeviceState.SOCFULL, discharging=200, pack_state=2, soc=100, min_soc=10)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.DISCHARGE


def test_socfull_not_discharging_reports_idle() -> None:
    d = _make(DeviceState.SOCFULL, charging=200, pack_state=2, soc=100, min_soc=10)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.IDLE


def test_socempty_charging_reports_charge() -> None:
    d = _make(DeviceState.SOCEMPTY, charging=200, pack_state=1, ac_mode=AcMode.INPUT)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.CHARGE


def test_socempty_discharge_blocked() -> None:
    d = _make(DeviceState.SOCEMPTY, discharging=200, pack_state=2)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.IDLE


def test_power_flow_sensor_mirrors_state_value() -> None:
    d = _make(DeviceState.ACTIVE, discharging=200, pack_state=2, soc=50, min_soc=10)
    _sm(d).update()
    assert d.power_flow_sensor.value == PowerFlowState.DISCHARGE.value


def test_inverter_loss_sensor_updated() -> None:
    d = _make(DeviceState.ACTIVE, discharging=200, pack_state=2, soc=50, min_soc=10)
    d.inverterLossPort.power = 45
    _sm(d).update()
    assert d.inverterLoss.value == 45


# ---------------------------------------------------------------------------
#  Matrix: packState=0 (Standby)
# ---------------------------------------------------------------------------

def test_standby_always_idle_regardless_of_battery_power() -> None:
    """packState=0 means firmware reports Standby — trust it over residual power values."""
    d = _make(DeviceState.ACTIVE, charging=100, pack_state=0)
    assert _sm(d).classify() == PowerFlowState.IDLE

    d2 = _make(DeviceState.ACTIVE, discharging=200, pack_state=0)
    assert _sm(d2).classify() == PowerFlowState.IDLE


# ---------------------------------------------------------------------------
#  Matrix: packState=1 (Charging direction)
# ---------------------------------------------------------------------------

def test_transition_ch_dis_idle_when_acmode_output() -> None:
    """packState=1 + acMode=OUTPUT: firmware switched to OUTPUT but battery measurement
    still shows residual charge power.  Must be IDLE — not CHARGE — so no
    redundant STOP_CHARGE is sent (the root cause of the 15-20s CH→DIS delay)."""
    d = _make(
        DeviceState.ACTIVE,
        charging=28,        # residual outputPackPower still visible
        pack_state=1,
        ac_mode=AcMode.OUTPUT,
    )
    assert _sm(d).classify() == PowerFlowState.IDLE


def test_charge_normal() -> None:
    d = _make(DeviceState.ACTIVE, charging=1100, pack_state=1, ac_mode=AcMode.INPUT)
    assert _sm(d).classify() == PowerFlowState.CHARGE


def test_charge_startup_ramp_reports_idle() -> None:
    """packState=1 + acMode=INPUT but battery not yet charging: firmware startup ramp."""
    d = _make(DeviceState.ACTIVE, charging=0, pack_state=1, ac_mode=AcMode.INPUT)
    assert _sm(d).classify() == PowerFlowState.IDLE


# ---------------------------------------------------------------------------
#  Matrix: packState=2 (Discharging direction) — guards
# ---------------------------------------------------------------------------

def test_bypass_guard_blocks_wakeup() -> None:
    """packState=2 + bypass active → IDLE, not WAKEUP (guard §6.2)."""
    d = _make(DeviceState.ACTIVE, pack_state=2, bypass_active=True, soc=50, min_soc=10)
    assert _sm(d).classify() == PowerFlowState.IDLE


def test_soc_guard_blocks_wakeup_at_min_soc() -> None:
    """packState=2 + SOC at minSoc+buffer boundary → IDLE (guard §6.1)."""
    buf = SmartMode.DISCHARGE_SOC_BUFFER
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=10 + buf, min_soc=10)
    assert _sm(d).classify() == PowerFlowState.IDLE


def test_soc_guard_allows_wakeup_above_min_soc() -> None:
    buf = SmartMode.DISCHARGE_SOC_BUFFER
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=10 + buf + 1, min_soc=10)
    assert _sm(d).classify() == PowerFlowState.WAKEUP


def test_socfull_guard_no_wakeup_when_not_discharging() -> None:
    """packState=2 + SOCFULL + no actual discharge → IDLE (guard §6.5)."""
    d = _make(DeviceState.SOCFULL, pack_state=2, soc=100, min_soc=10)
    assert _sm(d).classify() == PowerFlowState.IDLE


# ---------------------------------------------------------------------------
#  Matrix: packState=2 — WAKEUP lifecycle
# ---------------------------------------------------------------------------

def test_wakeup_valid_sets_wake_started_at() -> None:
    """First WAKEUP entry: classify() must set wake_started_at."""
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10)
    assert d.wake_started_at == datetime.min
    result = _sm(d).classify()
    assert result == PowerFlowState.WAKEUP
    assert d.wake_started_at != datetime.min


def test_wakeup_does_not_reset_existing_timer() -> None:
    """Subsequent WAKEUP cycles must not reset an active timer."""
    t0 = datetime.now() - timedelta(seconds=5)
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10, wake_started_at=t0)
    _sm(d).classify()
    assert d.wake_started_at == t0  # unchanged


def test_wakeup_timeout_reverts_to_idle() -> None:
    """packState=2 + wake_started_at expired past WAKE_TIMEOUT → IDLE (self-heal)."""
    expired = datetime.now() - timedelta(seconds=SmartMode.WAKE_TIMEOUT + 1)
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10, wake_started_at=expired)
    assert _sm(d).classify() == PowerFlowState.IDLE


def test_wakeup_releases_to_discharge_and_clears_timer() -> None:
    """packState=2 + battery starts discharging → DISCHARGE + wake_started_at cleared."""
    t0 = datetime.now() - timedelta(seconds=5)
    d = _make(
        DeviceState.ACTIVE,
        discharging=500,
        pack_state=2,
        soc=50,
        min_soc=10,
        wake_started_at=t0,
    )
    result = _sm(d).classify()
    assert result == PowerFlowState.DISCHARGE
    assert d.wake_started_at == datetime.min


def test_update_sets_wakeup_entered_on_first_wakeup_transition() -> None:
    """update() must record wakeup_entered when transitioning into WAKEUP."""
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              flow=PowerFlowState.IDLE)
    assert d.wakeup_entered == datetime.min
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.WAKEUP
    assert d.wakeup_entered != datetime.min


def test_update_sets_wakeup_committed_on_wakeup_to_discharge() -> None:
    """update() must set wakeup_committed when WAKEUP → DISCHARGE."""
    t0 = datetime.now() - timedelta(seconds=5)
    d = _make(
        DeviceState.ACTIVE,
        discharging=500,
        pack_state=2,
        soc=50,
        min_soc=10,
        flow=PowerFlowState.WAKEUP,
        wake_started_at=t0,
    )
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.DISCHARGE
    assert d.wakeup_committed is True


# ---------------------------------------------------------------------------
#  Fallback: packState not in {0, 1, 2}
# ---------------------------------------------------------------------------

def test_fallback_power_based_charge() -> None:
    """packState with unknown value → fallback to battery-port classification."""
    d = _make(DeviceState.ACTIVE, charging=200, pack_state=9)
    assert _sm(d).classify() == PowerFlowState.CHARGE


def test_fallback_power_based_discharge() -> None:
    d = _make(DeviceState.ACTIVE, discharging=200, pack_state=9, soc=50, min_soc=10)
    assert _sm(d).classify() == PowerFlowState.DISCHARGE


def test_fallback_power_based_idle() -> None:
    d = _make(DeviceState.ACTIVE, pack_state=9)
    assert _sm(d).classify() == PowerFlowState.IDLE