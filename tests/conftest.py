"""Shared fixtures for power_strategy tests.

Fakes are intentionally minimal: each phase of the rewrite extends them
as tests demand. Do not add attributes speculatively — only what a real
test touches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest


@dataclass
class FakeSensor:
    """Stand-in for ZendureSensor / ZendureNumber.

    Exposes the surface power_strategy actually reads: `.asInt`, `.asNumber`,
    and `.update_value()`.
    """

    value: float = 0

    @property
    def asInt(self) -> int:  # noqa: N802 — match production name
        return int(self.value)

    @property
    def asNumber(self) -> float:  # noqa: N802
        return float(self.value)

    def update_value(self, v: Any) -> None:
        self.value = v


@dataclass
class FakePort:
    """Stand-in for AcPowerPort / BatteryPowerPort / OffGridPowerPort / DcSolarPowerPort."""

    power: int = 0
    available: int = 0


@dataclass
class FakeBypassRelay:
    active: bool = False

    @property
    def is_active(self) -> bool:
        return self.active


@dataclass
class FakeFuseGroup:
    minpower: int = -3600
    maxpower: int = 3600
    charge_limit: int = 3600
    discharge_limit: int = 3600

    def update_charge_limits(self) -> None:
        pass

    def update_discharge_limits(self) -> None:
        pass


@dataclass
class FakeDevice:
    """Minimal ZendureDevice stand-in.

    Records every power_charge/power_discharge call for assertion in tests.
    """

    name: str = "fake"
    electricLevel: int = 50  # noqa: N815
    minSoc: int = 10  # noqa: N815
    socSet: int = 95  # noqa: N815
    pwr_max: int = 2400
    charge_limit: int = 2400
    discharge_limit: int = 2400
    charge_optimal: int = 1200
    discharge_optimal: int = 1200
    charge_start: int = 100
    discharge_start: int = 100

    state: Any = None
    power_flow_state: Any = None

    acPort: FakePort = field(default_factory=FakePort)  # noqa: N815
    batteryPort: FakePort = field(default_factory=FakePort)  # noqa: N815
    solarPort: FakePort = field(default_factory=FakePort)  # noqa: N815
    offgridPort: FakePort | None = field(default_factory=FakePort)  # noqa: N815

    bypass: FakeBypassRelay = field(default_factory=FakeBypassRelay)
    fuseGrp: FakeFuseGroup = field(default_factory=FakeFuseGroup)  # noqa: N815

    wake_started_at: datetime = datetime.min
    wakeup_entered: datetime = datetime.min

    calls: list[tuple[str, int]] = field(default_factory=list)

    @property
    def pwr_offgrid(self) -> int:
        return self.offgridPort.power if self.offgridPort else 0

    async def power_charge(self, power: int) -> None:
        self.calls.append(("charge", power))

    async def power_discharge(self, power: int) -> None:
        self.calls.append(("discharge", power))


@dataclass
class FakeManager:
    """Minimal ZendureManager stand-in."""

    operation: Any = None
    manualpower: int = 0
    charge: list[FakeDevice] = field(default_factory=list)
    discharge: list[FakeDevice] = field(default_factory=list)
    idle: list[FakeDevice] = field(default_factory=list)
    power: FakeSensor = field(default_factory=FakeSensor)
    availableKwh: FakeSensor = field(default_factory=FakeSensor)  # noqa: N815
    power_flow_sensor: FakeSensor = field(default_factory=FakeSensor)
    grid_port: FakePort = field(default_factory=FakePort)
    hysteresis: Any = None


@pytest.fixture
def fake_device() -> FakeDevice:
    return FakeDevice()


@pytest.fixture
def fake_manager() -> FakeManager:
    return FakeManager()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0)
