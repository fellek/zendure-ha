"""Unit tests for HysteresisFilter.filter() — the Phase 1 mode-aware replacement
for the legacy apply_charge_cooldown path.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.zendure_ha.const import ManagerMode, SmartMode
from custom_components.zendure_ha.power_strategy import Direction, HysteresisFilter


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0)


def test_fresh_filter_passes_setpoint_through(t0: datetime) -> None:
    # No sentinel re-arm loop: a fresh filter must not swallow the first setpoint.
    f = HysteresisFilter()
    assert f.filter(-500, Direction.CHARGE, ManagerMode.MATCHING, t0) == -500


def test_manual_mode_bypasses_cooldown(t0: datetime) -> None:
    f = HysteresisFilter()
    # Arm a cooldown via a direction switch in MATCHING first.
    f.filter(-500, Direction.CHARGE, ManagerMode.MATCHING, t0)
    f.filter(+500, Direction.DISCHARGE, ManagerMode.MATCHING, t0 + timedelta(seconds=1))
    # Now a MANUAL dispatch must pass through even though cooldown would be active.
    assert f.filter(-300, Direction.CHARGE, ManagerMode.MANUAL, t0 + timedelta(seconds=2)) == -300


def test_direction_switch_arms_slow_cooldown(t0: datetime) -> None:
    f = HysteresisFilter()
    # First establish CHARGE as the last direction.
    assert f.filter(-500, Direction.CHARGE, ManagerMode.MATCHING, t0) == -500
    # Immediate switch to DISCHARGE must arm slow cooldown and return 0.
    assert f.filter(+500, Direction.DISCHARGE, ManagerMode.MATCHING, t0 + timedelta(seconds=1)) == 0
    # Still within cooldown window.
    mid = t0 + timedelta(seconds=1 + SmartMode.HYSTERESIS_SLOW_COOLDOWN - 1)
    assert f.filter(+500, Direction.DISCHARGE, ManagerMode.MATCHING, mid) == 0
    # After cooldown expires, setpoint passes.
    after = t0 + timedelta(seconds=2 + SmartMode.HYSTERESIS_SLOW_COOLDOWN)
    assert f.filter(+500, Direction.DISCHARGE, ManagerMode.MATCHING, after) == 500


def test_fast_cooldown_when_idle_long_enough(t0: datetime) -> None:
    f = HysteresisFilter()
    f.filter(-500, Direction.CHARGE, ManagerMode.MATCHING, t0)
    gap = SmartMode.HYSTERESIS_LONG_COOLDOWN + 5
    later = t0 + timedelta(seconds=gap)
    # After a long idle gap, switching direction should arm FAST (not SLOW).
    assert f.filter(+500, Direction.DISCHARGE, ManagerMode.MATCHING, later) == 0
    fast_done = later + timedelta(seconds=SmartMode.HYSTERESIS_FAST_COOLDOWN + 1)
    assert f.filter(+500, Direction.DISCHARGE, ManagerMode.MATCHING, fast_done) == 500


def test_direction_none_does_not_rearm(t0: datetime) -> None:
    f = HysteresisFilter()
    f.filter(-500, Direction.CHARGE, ManagerMode.MATCHING, t0)
    # NONE (idle) should not arm a cooldown.
    assert f.filter(0, Direction.NONE, ManagerMode.MATCHING, t0 + timedelta(seconds=1)) == 0
    # Continuing in the same direction later still passes through.
    assert f.filter(-400, Direction.CHARGE, ManagerMode.MATCHING, t0 + timedelta(seconds=2)) == -400


def test_same_direction_repeated_does_not_rearm(t0: datetime) -> None:
    f = HysteresisFilter()
    for i in range(5):
        assert f.filter(-500, Direction.CHARGE, ManagerMode.MATCHING, t0 + timedelta(seconds=i)) == -500
