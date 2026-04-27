"""Tests for `DevicePowerFlowStateMachine.classify()` and `update()`.

The state machine is exercised without instantiating a real `ZendureDevice`:
we hand it a lightweight stand-in that exposes only the attributes the
state machine reads or writes.  That keeps these tests free of HA imports.

Classification matrix:

  packState=0, wakeup_cmd=None             → IDLE  (Standby)
  packState=0, wakeup_cmd set              → WAKEUP (Sunset-Bug fix)
  packState=1, acMode=OUTPUT               → IDLE  (CH→DIS firmware transition)
  packState=1, acMode=INPUT, is_charging   → CHARGE
  packState=1, acMode=INPUT, not charging  → IDLE  (startup ramp, no cmd)
  packState=1, acMode=INPUT, not charging, cmd → WAKEUP (startup ramp, cmd pending)
  packState=2, SOCEMPTY                    → IDLE  (guard)
  packState=2, bypass active               → IDLE  (guard)
  packState=2, SOCFULL, is_discharging     → DISCHARGE
  packState=2, SOCFULL, not discharging    → IDLE
  packState=2, SOC <= minSoc+buffer        → IDLE  (SOC-guard)
  packState=2, is_discharging              → DISCHARGE
  packState=2, wakeup_cmd=None             → IDLE
  packState=2, wakeup_cmd set, guards pass → WAKEUP
  packState=other / not set                → fallback: power-based classification
  OFFLINE                                  → OFF   (always)

WakeupCommand lifecycle (managed by update()):
  STOP echo (acMode==dir, limit==0)        → clears wakeup_cmd
  Confirm echo (acMode==dir, limit>=START) → sets cmd.confirmed=True
  Timeout (elapsed > WAKE_TIMEOUT)         → clears wakeup_cmd
  WAKEUP → CHARGE/DISCHARGE               → clears wakeup_cmd, sets wakeup_just_completed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from custom_components.zendure_ha.const import AcMode, DeviceState, PowerFlowState, SmartMode, WakeupCommand
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
    wakeup_cmd: WakeupCommand | None = None
    wakeup_just_completed: bool = False
    batteryPort: FakeBatteryPort = field(default_factory=FakeBatteryPort)   # noqa: N815
    inverterLossPort: FakeInverterLossPort = field(                          # noqa: N815
        default_factory=FakeInverterLossPort
    )
    power_flow_sensor: FakeSensor = field(default_factory=FakeSensor)
    inverterLoss: FakeSensor = field(default_factory=FakeSensor)             # noqa: N815
    limitOutput: FakeSensor = field(default_factory=FakeSensor)              # noqa: N815
    limitInput: FakeSensor = field(default_factory=FakeSensor)               # noqa: N815


def _cmd(
    direction: int = AcMode.OUTPUT,
    expected_pack_state: int = 2,
    expected_limit: int = 100,
    *,
    confirmed: bool = False,
    age_seconds: float = 0.0,
) -> WakeupCommand:
    cmd = WakeupCommand(direction, expected_pack_state, expected_limit)
    cmd.confirmed = confirmed
    if age_seconds:
        cmd.sent_at = datetime.now() - timedelta(seconds=age_seconds)
    return cmd


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
    wakeup_cmd: WakeupCommand | None = None,
) -> FakeSMDevice:
    d = FakeSMDevice(state=state, power_flow_state=flow, wakeup_cmd=wakeup_cmd)
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
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=10 + buf + 1, min_soc=10,
              wakeup_cmd=_cmd())
    assert _sm(d).classify() == PowerFlowState.WAKEUP


def test_socfull_guard_no_wakeup_when_not_discharging() -> None:
    """packState=2 + SOCFULL + no actual discharge → IDLE (guard §6.5)."""
    d = _make(DeviceState.SOCFULL, pack_state=2, soc=100, min_soc=10)
    assert _sm(d).classify() == PowerFlowState.IDLE


# ---------------------------------------------------------------------------
#  Matrix: packState=0 — Sunset-Bug fix
# ---------------------------------------------------------------------------

def test_standby_with_cmd_returns_wakeup() -> None:
    """packState=0 + wakeup_cmd → WAKEUP (Sunset-Bug fix: SF2400 reports 0 mid-wakeup)."""
    d = _make(DeviceState.ACTIVE, pack_state=0, wakeup_cmd=_cmd())
    assert _sm(d).classify() == PowerFlowState.WAKEUP


def test_standby_without_cmd_returns_idle() -> None:
    """packState=0 + no wakeup_cmd → IDLE."""
    d = _make(DeviceState.ACTIVE, pack_state=0)
    assert _sm(d).classify() == PowerFlowState.IDLE


# ---------------------------------------------------------------------------
#  Matrix: packState=1 — startup ramp with/without cmd
# ---------------------------------------------------------------------------

def test_pack1_not_charging_with_cmd_returns_wakeup() -> None:
    """packState=1 + acMode=INPUT + battery not yet charging + cmd → WAKEUP (startup ramp)."""
    d = _make(DeviceState.ACTIVE, charging=0, pack_state=1, ac_mode=AcMode.INPUT,
              wakeup_cmd=_cmd(AcMode.INPUT, 1, 200))
    assert _sm(d).classify() == PowerFlowState.WAKEUP


def test_pack1_not_charging_without_cmd_returns_idle() -> None:
    """packState=1 + acMode=INPUT + battery not yet charging + no cmd → IDLE."""
    d = _make(DeviceState.ACTIVE, charging=0, pack_state=1, ac_mode=AcMode.INPUT)
    assert _sm(d).classify() == PowerFlowState.IDLE


# ---------------------------------------------------------------------------
#  Matrix: packState=2 — WAKEUP with wakeup_cmd
# ---------------------------------------------------------------------------

def test_pack2_with_cmd_and_guards_pass_returns_wakeup() -> None:
    """packState=2 + guards pass + wakeup_cmd → WAKEUP."""
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              wakeup_cmd=_cmd())
    assert _sm(d).classify() == PowerFlowState.WAKEUP


def test_pack2_without_cmd_returns_idle() -> None:
    """packState=2 + guards pass + no wakeup_cmd → IDLE."""
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10)
    assert _sm(d).classify() == PowerFlowState.IDLE


# ---------------------------------------------------------------------------
#  WakeupCommand lifecycle: update() — confirmation
# ---------------------------------------------------------------------------

def test_update_confirms_wakeup_cmd_on_matching_echo() -> None:
    """acMode==direction + limit>=POWER_START → cmd.confirmed=True."""
    cmd = _cmd(AcMode.OUTPUT, 2, 200)
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              wakeup_cmd=cmd)
    d.acMode.value = AcMode.OUTPUT
    d.limitOutput.value = 200
    _sm(d).update()
    assert d.wakeup_cmd is not None
    assert d.wakeup_cmd.confirmed is True


def test_update_cancels_wakeup_cmd_on_stop_echo() -> None:
    """acMode==direction + limit==0 → wakeup_cmd cleared (STOP echo)."""
    cmd = _cmd(AcMode.OUTPUT, 2, 200)
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              wakeup_cmd=cmd)
    d.acMode.value = AcMode.OUTPUT
    d.limitOutput.value = 0
    _sm(d).update()
    assert d.wakeup_cmd is None


def test_update_ignores_echo_with_wrong_direction() -> None:
    """acMode != direction → STOP echo is not processed (avoids false cancellation)."""
    cmd = _cmd(AcMode.OUTPUT, 2, 200)  # discharge command
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              wakeup_cmd=cmd)
    d.acMode.value = AcMode.INPUT  # different direction
    d.limitInput.value = 0         # would cancel if direction matched
    _sm(d).update()
    assert d.wakeup_cmd is not None  # must NOT be cancelled


# ---------------------------------------------------------------------------
#  WakeupCommand lifecycle: update() — timeout
# ---------------------------------------------------------------------------

def test_update_clears_expired_wakeup_cmd() -> None:
    """wakeup_cmd older than WAKE_TIMEOUT → cleared by update()."""
    cmd = _cmd(age_seconds=SmartMode.WAKE_TIMEOUT + 1)
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              wakeup_cmd=cmd)
    _sm(d).update()
    assert d.wakeup_cmd is None


def test_update_keeps_fresh_wakeup_cmd() -> None:
    """wakeup_cmd within WAKE_TIMEOUT → not cleared."""
    cmd = _cmd(age_seconds=SmartMode.WAKE_TIMEOUT - 1)
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              wakeup_cmd=cmd)
    _sm(d).update()
    assert d.wakeup_cmd is not None


# ---------------------------------------------------------------------------
#  WakeupCommand lifecycle: update() — success transition
# ---------------------------------------------------------------------------

def test_update_sets_wakeup_just_completed_on_wakeup_to_discharge() -> None:
    """WAKEUP → DISCHARGE: wakeup_cmd cleared, wakeup_just_completed=True."""
    cmd = _cmd(AcMode.OUTPUT, 2, 200, age_seconds=2)
    d = _make(
        DeviceState.ACTIVE,
        discharging=500,
        pack_state=2,
        soc=50,
        min_soc=10,
        flow=PowerFlowState.WAKEUP,
        wakeup_cmd=cmd,
    )
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.DISCHARGE
    assert d.wakeup_cmd is None
    assert d.wakeup_just_completed is True


def test_update_sets_wakeup_just_completed_on_wakeup_to_charge() -> None:
    """WAKEUP → CHARGE: wakeup_cmd cleared, wakeup_just_completed=True."""
    cmd = _cmd(AcMode.INPUT, 1, 200, age_seconds=2)
    d = _make(
        DeviceState.ACTIVE,
        charging=500,
        pack_state=1,
        ac_mode=AcMode.INPUT,
        flow=PowerFlowState.WAKEUP,
        wakeup_cmd=cmd,
    )
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.CHARGE
    assert d.wakeup_cmd is None
    assert d.wakeup_just_completed is True


def test_update_does_not_set_wakeup_just_completed_on_idle_to_wakeup() -> None:
    """IDLE → WAKEUP transition: wakeup_just_completed must stay False."""
    cmd = _cmd(age_seconds=2)
    d = _make(DeviceState.ACTIVE, pack_state=2, soc=50, min_soc=10,
              flow=PowerFlowState.IDLE, wakeup_cmd=cmd)
    _sm(d).update()
    assert d.power_flow_state == PowerFlowState.WAKEUP
    assert d.wakeup_just_completed is False


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