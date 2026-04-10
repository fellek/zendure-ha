"""Tests for the _assess phase.

Focuses on the two Phase 2 deliverables:
- SystemSnapshot is a read-only carrier of the assess results.
- _recover_wake_timeouts is the self-healing path for the sticky-WAKEUP bug.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.zendure_ha.const import PowerFlowState, SmartMode
from custom_components.zendure_ha.power_strategy import (
    SystemSnapshot,
    _recover_wake_timeouts,
)


def _dev(name: str, state: PowerFlowState, wake_started_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(name=name, power_flow_state=state, wake_started_at=wake_started_at)


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


def test_recover_wake_timeouts_reverts_stale_wakeup(t0: datetime) -> None:
    stale_at = t0 - timedelta(seconds=SmartMode.WAKE_TIMEOUT + 1)
    d = _dev("stuck", PowerFlowState.WAKEUP, stale_at)
    recovered = _recover_wake_timeouts([d], t0)
    assert recovered == [d]
    assert d.power_flow_state == PowerFlowState.IDLE


def test_recover_wake_timeouts_leaves_fresh_wakeup_alone(t0: datetime) -> None:
    fresh_at = t0 - timedelta(seconds=SmartMode.WAKE_TIMEOUT - 1)
    d = _dev("fresh", PowerFlowState.WAKEUP, fresh_at)
    recovered = _recover_wake_timeouts([d], t0)
    assert recovered == []
    assert d.power_flow_state == PowerFlowState.WAKEUP


def test_recover_wake_timeouts_ignores_non_wakeup_devices(t0: datetime) -> None:
    stale_at = t0 - timedelta(seconds=SmartMode.WAKE_TIMEOUT * 10)
    d_idle = _dev("idle", PowerFlowState.IDLE, stale_at)
    d_charge = _dev("charging", PowerFlowState.CHARGE, stale_at)
    d_off = _dev("off", PowerFlowState.OFF, stale_at)
    recovered = _recover_wake_timeouts([d_idle, d_charge, d_off], t0)
    assert recovered == []
    assert d_idle.power_flow_state == PowerFlowState.IDLE
    assert d_charge.power_flow_state == PowerFlowState.CHARGE
    assert d_off.power_flow_state == PowerFlowState.OFF


def test_recover_wake_timeouts_mixed_batch(t0: datetime) -> None:
    stale = _dev("stale", PowerFlowState.WAKEUP, t0 - timedelta(seconds=SmartMode.WAKE_TIMEOUT + 5))
    fresh = _dev("fresh", PowerFlowState.WAKEUP, t0 - timedelta(seconds=1))
    idle = _dev("idle", PowerFlowState.IDLE, datetime.min)
    recovered = _recover_wake_timeouts([stale, fresh, idle], t0)
    assert recovered == [stale]
    assert stale.power_flow_state == PowerFlowState.IDLE
    assert fresh.power_flow_state == PowerFlowState.WAKEUP
    assert idle.power_flow_state == PowerFlowState.IDLE
