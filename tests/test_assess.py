"""Tests for the _assess phase.

SystemSnapshot is a read-only carrier of the assess results.

Note: Wakeup timeout self-healing was formerly tested here via
`_recover_wake_timeouts`. That function was removed in the WakeupCommand
refactor — timeout is now handled by DevicePowerFlowStateMachine.update().
See test_device_state_machine.py::test_update_clears_expired_wakeup_cmd.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.zendure_ha.const import PowerFlowState
from custom_components.zendure_ha.power_strategy import SystemSnapshot


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0)


def test_system_snapshot_is_frozen(t0: datetime) -> None:
    snap = SystemSnapshot(setpoint_raw=150, available_kwh=2.5, power=400, time=t0)
    assert snap.setpoint_raw == 150
    assert snap.available_kwh == 2.5
    assert snap.power == 400
    assert snap.time == t0
    with pytest.raises((AttributeError, Exception)):
        snap.setpoint_raw = 999  # type: ignore[misc]