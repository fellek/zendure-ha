"""Tests for Vorschlag 03 — slow polling when all devices are minSoC-blocked.

The helper `all_devices_blocked_no_solar` decides whether the dispatch loop can
extend its cadence from `SmartMode.TIMEZERO` (4 s) to `SmartMode.SLOW_POLL_INTERVAL`
(60 s). This matters in two scenarios:

- Night-time: every device at/near minSoC, no solar, only home demand. The old
  4 s cadence produced ~900 no-op dispatch log lines per hour.
- Extended empty state on cloudy days: same state, same waste.

The caller in `manager.py:_p1_changed` also gates on `avg > 0` (demand direction);
on surplus (`avg <= 0`) the helper is not consulted, so tests here only model its
output, not the gating.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.zendure_ha.const import SmartMode
from custom_components.zendure_ha.power_strategy import all_devices_blocked_no_solar


def _dev(*, soc: int, min_soc: int, solar: int, online: bool = True) -> SimpleNamespace:
    """Build a minimal device stand-in matching the attributes the helper reads.

    The helper touches: `.online`, `.electricLevel.asInt`, `.minSoc.asNumber`,
    `.solarPort.total_solar_power`. Anything else is irrelevant.
    """
    return SimpleNamespace(
        online=online,
        electricLevel=SimpleNamespace(asInt=soc),
        minSoc=SimpleNamespace(asNumber=float(min_soc)),
        solarPort=SimpleNamespace(total_solar_power=solar),
    )


def _mgr(devices: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(devices=devices)


def test_returns_false_when_no_devices() -> None:
    # Degenerate case: empty manager must not claim "everything blocked".
    assert all_devices_blocked_no_solar(_mgr([])) is False


def test_returns_false_when_only_offline_devices() -> None:
    offline = _dev(soc=5, min_soc=10, solar=0, online=False)
    assert all_devices_blocked_no_solar(_mgr([offline])) is False


def test_returns_true_when_all_at_minsoc_no_solar() -> None:
    # Single device exactly at minSoC + buffer boundary → blocked.
    soc = 10 + SmartMode.DISCHARGE_SOC_BUFFER
    d = _dev(soc=soc, min_soc=10, solar=0)
    assert all_devices_blocked_no_solar(_mgr([d])) is True


def test_returns_true_when_all_below_minsoc() -> None:
    # Well below minSoC — still blocked (discharge impossible).
    d = _dev(soc=5, min_soc=10, solar=0)
    assert all_devices_blocked_no_solar(_mgr([d])) is True


def test_returns_false_when_any_device_above_minsoc() -> None:
    blocked = _dev(soc=10 + SmartMode.DISCHARGE_SOC_BUFFER, min_soc=10, solar=0)
    available = _dev(soc=50, min_soc=10, solar=0)
    # One discharge-capable device → polling must stay fast.
    assert all_devices_blocked_no_solar(_mgr([blocked, available])) is False


def test_returns_false_when_any_device_has_solar() -> None:
    # All blocked by SoC but one panel delivers 50 W → surplus may arrive any second.
    d1 = _dev(soc=5, min_soc=10, solar=0)
    d2 = _dev(soc=5, min_soc=10, solar=50)
    assert all_devices_blocked_no_solar(_mgr([d1, d2])) is False


def test_offline_devices_do_not_block_fast_path() -> None:
    """Offline devices must be excluded. A single offline device with unknown SoC
    must not force the system into slow-poll mode.
    """
    offline = _dev(soc=5, min_soc=10, solar=0, online=False)
    available = _dev(soc=50, min_soc=10, solar=0)
    assert all_devices_blocked_no_solar(_mgr([offline, available])) is False


def test_offline_devices_do_not_prevent_slow_path() -> None:
    """Symmetric: an offline device alongside blocked online devices should not
    keep the system in fast poll — the helper ignores the offline one entirely.
    """
    offline = _dev(soc=50, min_soc=10, solar=0, online=False)  # would unblock if online
    blocked = _dev(soc=5, min_soc=10, solar=0)
    assert all_devices_blocked_no_solar(_mgr([offline, blocked])) is True


def test_buffer_boundary_is_inclusive() -> None:
    """SoC == minSoC + DISCHARGE_SOC_BUFFER counts as blocked (matches the
    `<=` in `_wake_idle_devices`, `distribute_discharge` and friends).
    """
    d = _dev(soc=12, min_soc=10, solar=0)  # buffer = 2 → 12 is the boundary
    assert all_devices_blocked_no_solar(_mgr([d])) is True


def test_one_above_buffer_is_not_blocked() -> None:
    d = _dev(soc=13, min_soc=10, solar=0)  # just above buffer
    assert all_devices_blocked_no_solar(_mgr([d])) is False
