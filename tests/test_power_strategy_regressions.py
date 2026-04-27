"""Regression fence for bugs fixed during the power_strategy rewrite.

These tests encode historical bugs as explicit scenarios so they cannot silently
return. Add a test here whenever a power_strategy bug is fixed.

Current fixtures:
- SF 2400 stop-charge quirk: a STOP_CHARGE command must route through
  power_discharge(0 or POWER_IDLE_OFFSET), never power_charge(0).
- SF 2400 stop-discharge symmetric: STOP_DISCHARGE routes through power_charge.
- MANUAL mode must bypass hysteresis cooldown (re-arm loop bug).
- Cold-start wake must create a WakeupCommand (replaced old wake_started_at fence).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.zendure_ha.const import AcMode, DeviceState, ManagerMode, PowerFlowState, SmartMode, WakeupCommand
from custom_components.zendure_ha.power_strategy import (
    Command,
    DeviceAssignment,
    Direction,
    HysteresisFilter,
    apply_assignment,
    distribute_charge,
    distribute_discharge,
)


class _RecordingDevice:
    """Minimal ZendureDevice stand-in: records power_charge / power_discharge calls."""

    def __init__(
        self,
        name: str = "sf2400",
        offgrid_consumption: int = 0,
        power_flow_state: PowerFlowState = PowerFlowState.CHARGE,
    ) -> None:
        self.name = name
        self.offgridPort = SimpleNamespace(power_consumption=offgrid_consumption)  # noqa: N815
        self.power_flow_state = power_flow_state
        self.calls: list[tuple[str, int]] = []

    async def power_charge(self, power: int) -> int:
        self.calls.append(("charge", power))
        return power

    async def power_discharge(self, power: int) -> int:
        self.calls.append(("discharge", power))
        return power


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
#  SF 2400 stop quirk — the central reason Command.STOP_CHARGE exists.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_charge_without_offgrid_uses_power_discharge_zero() -> None:
    # Rule: no offgrid load => power_discharge(0), never power_charge(0).
    d = _RecordingDevice(offgrid_consumption=0)
    await apply_assignment(d, DeviceAssignment(Command.STOP_CHARGE))
    assert d.calls == [("discharge", 0)]


@pytest.mark.asyncio
async def test_stop_charge_with_offgrid_uses_power_discharge_idle_offset() -> None:
    # Rule: offgrid load present => power_discharge(POWER_IDLE_OFFSET), never power_charge.
    d = _RecordingDevice(offgrid_consumption=50)
    await apply_assignment(d, DeviceAssignment(Command.STOP_CHARGE))
    assert d.calls == [("discharge", SmartMode.POWER_IDLE_OFFSET)]


@pytest.mark.asyncio
async def test_stop_discharge_without_offgrid_uses_power_discharge_zero() -> None:
    d = _RecordingDevice(offgrid_consumption=0)
    await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    assert d.calls == [("discharge", 0)]


@pytest.mark.asyncio
async def test_stop_discharge_with_offgrid_uses_power_discharge_idle_offset() -> None:
    d = _RecordingDevice(offgrid_consumption=50)
    await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    assert d.calls == [("discharge", SmartMode.POWER_IDLE_OFFSET)]


@pytest.mark.asyncio
async def test_regular_charge_and_discharge_unchanged() -> None:
    d = _RecordingDevice()
    await apply_assignment(d, DeviceAssignment(Command.CHARGE, power=-500))
    await apply_assignment(d, DeviceAssignment(Command.DISCHARGE, power=300))
    assert d.calls == [("charge", -500), ("discharge", 300)]


# ---------------------------------------------------------------------------
#  Vorschlag 02 — STOP commands on an already-idle device are no-ops.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_charge_on_idle_device_is_noop() -> None:
    """Rule (Vorschlag 02): STOP_CHARGE on a device already in IDLE must not
    issue a hardware command — it used to spam ~300 redundant `Stopping charge
    … with 0` log lines per hour. Return 0, record nothing.
    """
    d = _RecordingDevice(offgrid_consumption=0, power_flow_state=PowerFlowState.IDLE)
    result = await apply_assignment(d, DeviceAssignment(Command.STOP_CHARGE))
    assert result == 0
    assert d.calls == []


@pytest.mark.asyncio
async def test_stop_discharge_on_idle_device_is_noop() -> None:
    d = _RecordingDevice(offgrid_consumption=50, power_flow_state=PowerFlowState.IDLE)
    result = await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    assert result == 0
    assert d.calls == []


@pytest.mark.asyncio
async def test_stop_charge_on_charging_device_still_fires() -> None:
    """Sanity check for Vorschlag 02: the IDLE-skip must NOT swallow legitimate
    stop commands on devices that are actively charging/discharging.
    """
    d = _RecordingDevice(offgrid_consumption=0, power_flow_state=PowerFlowState.CHARGE)
    await apply_assignment(d, DeviceAssignment(Command.STOP_CHARGE))
    assert d.calls == [("discharge", 0)]


# ---------------------------------------------------------------------------
#  MANUAL re-arm loop bug — regression fence.
# ---------------------------------------------------------------------------


def test_manual_mode_never_enters_rearm_loop(t0: datetime) -> None:
    """Historical bug: mgr.hysteresis.reset() was called every MANUAL cycle which
    re-armed the cooldown via the `datetime.max` sentinel, forcing setpoint to 0
    forever. HysteresisFilter.filter() must return the setpoint unchanged on every
    MANUAL cycle regardless of how many times it is called.
    """
    f = HysteresisFilter()
    # First cycle after a MATCHING charge would historically arm the sentinel.
    f.filter(-500, Direction.CHARGE, ManagerMode.MATCHING, t0)
    # Then the user switches to MANUAL and nudges -300. Historically this became 0.
    for i in range(1, 20):
        out = f.filter(-300, Direction.CHARGE, ManagerMode.MANUAL, t0 + timedelta(seconds=i))
        assert out == -300, f"MANUAL cycle #{i} returned {out}, expected -300"


# ---------------------------------------------------------------------------
#  Cold-start WakeupCommand fence — replaces old wake_started_at regression.
# ---------------------------------------------------------------------------


class _WakeDevice:
    """Minimal device for cold-start wake tests.

    Defined as a class (not SimpleNamespace) so it is hashable for the
    `pass1_woken: set` inside `_wake_idle_devices`.
    """

    def __init__(self) -> None:
        self.name = "sf2400"
        self.power_flow_state = PowerFlowState.IDLE
        self.state = DeviceState.ACTIVE
        self.electricLevel = SimpleNamespace(asInt=50)
        self.minSoc = SimpleNamespace(asNumber=10.0)
        self.packState = SimpleNamespace(asInt=0)
        self.charge_limit = -2400
        self.discharge_limit = 2400
        self.charge_optimal = 400
        self.discharge_optimal = 400
        self.wakeup_cmd: WakeupCommand | None = None
        self.bypass = SimpleNamespace(is_active=False)
        self.offgridPort = SimpleNamespace(power_consumption=0, power_production=0)
        self.solarPort = None
        self.connectorPort = SimpleNamespace(power=0)
        self.last_bypass_wake_sent_at = datetime.min

    async def power_charge(self, power: int) -> int:
        return power

    async def power_discharge(self, power: int) -> int:
        return power


def _cold_start_mgr(d: _WakeDevice) -> SimpleNamespace:
    return SimpleNamespace(
        charge=[],
        discharge=[],
        discharge_limit=2400,
        discharge_produced=0,
        idle=[d],
        idle_lvlmin=50,
        idle_lvlmax=50,
        power_flow_sensor=SimpleNamespace(update_value=lambda v: None),
        hysteresis=HysteresisFilter(),
        operation=ManagerMode.MATCHING,
    )


@pytest.mark.asyncio
async def test_cold_start_discharge_creates_wakeup_cmd(t0: datetime) -> None:
    """Cold-start discharge must create a WakeupCommand (discharge direction).

    Historical context: before the WakeupCommand refactor, wake_started_at and
    wakeup_entered were set separately. A bug where only wakeup_entered was set
    caused _recover_wake_timeouts to fire immediately (time - datetime.min ≈ 63 Gs).
    Now a single WakeupCommand.sent_at carries the timer.
    """
    d = _WakeDevice()
    await distribute_discharge(_cold_start_mgr(d), setpoint=200, time=t0)

    assert d.wakeup_cmd is not None, "wakeup_cmd not set — timeout fires immediately"
    assert d.wakeup_cmd.direction == AcMode.OUTPUT
    assert d.wakeup_cmd.expected_pack_state == 2
    assert d.wakeup_cmd.expected_limit >= SmartMode.POWER_START


@pytest.mark.asyncio
async def test_cold_start_charge_creates_wakeup_cmd(t0: datetime) -> None:
    """Cold-start charge must create a WakeupCommand (charge direction)."""
    d = _WakeDevice()
    await distribute_charge(_cold_start_mgr(d), setpoint=-200, time=t0)

    assert d.wakeup_cmd is not None, "wakeup_cmd not set — timeout fires immediately"
    assert d.wakeup_cmd.direction == AcMode.INPUT
    assert d.wakeup_cmd.expected_pack_state == 1
    assert d.wakeup_cmd.expected_limit >= SmartMode.POWER_START
