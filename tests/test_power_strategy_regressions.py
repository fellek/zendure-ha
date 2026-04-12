"""Regression fence for bugs fixed during the power_strategy rewrite.

These tests encode historical bugs as explicit scenarios so they cannot silently
return. Add a test here whenever a power_strategy bug is fixed.

Current fixtures:
- SF 2400 stop-charge quirk: a STOP_CHARGE command must route through
  power_discharge(0 or POWER_IDLE_OFFSET), never power_charge(0).
- SF 2400 stop-discharge symmetric: STOP_DISCHARGE routes through power_charge.
- MANUAL mode must bypass hysteresis cooldown (re-arm loop bug).
- WAKE_PENDING must fall back to IDLE after WAKE_TIMEOUT (sticky WAKEUP bug).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.zendure_ha.const import ManagerMode, PowerFlowState, SmartMode
from custom_components.zendure_ha.power_strategy import (
    Command,
    DeviceAssignment,
    Direction,
    HysteresisFilter,
    _recover_wake_timeouts,
    apply_assignment,
)


class _RecordingDevice:
    """Minimal ZendureDevice stand-in: records power_charge / power_discharge calls."""

    def __init__(self, name: str = "sf2400", offgrid_consumption: int = 0) -> None:
        self.name = name
        self.offgridPort = SimpleNamespace(consumption=offgrid_consumption)  # noqa: N815
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
async def test_stop_discharge_without_offgrid_uses_power_charge_zero() -> None:
    d = _RecordingDevice(offgrid_consumption=0)
    await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    assert d.calls == [("charge", 0)]


@pytest.mark.asyncio
async def test_stop_discharge_with_offgrid_uses_power_charge_negative_idle_offset() -> None:
    d = _RecordingDevice(offgrid_consumption=50)
    await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    assert d.calls == [("charge", -SmartMode.POWER_IDLE_OFFSET)]


@pytest.mark.asyncio
async def test_regular_charge_and_discharge_unchanged() -> None:
    d = _RecordingDevice()
    await apply_assignment(d, DeviceAssignment(Command.CHARGE, power=-500))
    await apply_assignment(d, DeviceAssignment(Command.DISCHARGE, power=300))
    assert d.calls == [("charge", -500), ("discharge", 300)]


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
#  Sticky WAKEUP bug — regression fence.
# ---------------------------------------------------------------------------


def test_wake_timeout_recovers_stuck_device(t0: datetime) -> None:
    """Historical bug: once power_flow_state=WAKEUP was set, both wake passes
    excluded the device, leaving it permanently deadlocked if the battery never
    responded. _recover_wake_timeouts must flip the device back to IDLE after
    SmartMode.WAKE_TIMEOUT seconds.
    """
    stuck = SimpleNamespace(
        name="stuck",
        power_flow_state=PowerFlowState.WAKEUP,
        wake_started_at=t0 - timedelta(seconds=SmartMode.WAKE_TIMEOUT + 1),
    )
    recovered = _recover_wake_timeouts([stuck], t0)
    assert recovered == [stuck]
    assert stuck.power_flow_state == PowerFlowState.IDLE
