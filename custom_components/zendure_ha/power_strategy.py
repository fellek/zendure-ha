"""Power distribution strategy: classification, charge/discharge distribution."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import DeviceState, ManagerMode, ManagerState, SmartMode
from .power_port import DcSolarPowerPort, OffGridPowerPort

if TYPE_CHECKING:
    from .device import ZendureDevice
    from .manager import ZendureManager

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _DistDirection:
    """Direction-specific parameters for power distribution."""

    label: str
    sign: int  # -1 = charge, +1 = discharge
    sort_high_first: bool


CHARGE_DIR = _DistDirection("Charge", -1, True)
DISCHARGE_DIR = _DistDirection("Discharge", +1, False)


# ---------------------------------------------------------------------------
#  Hysteresis state: prevents rapid on/off cycling of devices
# ---------------------------------------------------------------------------

@dataclass
class HysteresisState:
    """Tracks hysteresis state for power distribution.

    - charge_cooldown: prevents immediate re-charging after a charge stop
    - accumulator (pwr_low): tracks underutilization to shut down unnecessary devices
    """

    accumulator: int = 0
    charge_time: datetime = field(default_factory=lambda: datetime.max)
    charge_last: datetime = field(default_factory=lambda: datetime.min)

    def reset(self) -> None:
        """Reset all hysteresis state (e.g. on mode switch to discharge)."""
        self.accumulator = 0
        self.charge_time = datetime.max
        self.charge_last = datetime.min

    def reset_accumulator(self) -> None:
        """Reset only the accumulator (e.g. after waking idle devices)."""
        self.accumulator = 0

    def apply_charge_cooldown(self, setpoint: int, time: datetime, operationstate: Any) -> int:
        """Apply cooldown hysteresis before allowing charge. Returns adjusted setpoint."""
        if self.charge_time > time:
            if self.charge_time == datetime.max:
                cooldown = (
                    SmartMode.HYSTERESIS_FAST_COOLDOWN
                    if (time - self.charge_last).total_seconds() > SmartMode.HYSTERESIS_LONG_COOLDOWN
                    else SmartMode.HYSTERESIS_SLOW_COOLDOWN
                )
                self.charge_time = time + timedelta(seconds=cooldown)
                self.charge_last = self.charge_time
                self.accumulator = 0
                _LOGGER.debug("Charge: hysteresis started, cooldown=%ss, charge_time=%s", cooldown, self.charge_time)
            _LOGGER.debug("Charge: hysteresis active, setpoint %s => 0 (waiting until %s)", setpoint, self.charge_time)
            return 0
        else:
            operationstate.update_value(ManagerState.CHARGE.value if setpoint < 0 else ManagerState.IDLE.value)
            return setpoint

    def apply_device_suppression(self, pwr: int, is_charge: bool, start: int, optimal: int, state: DeviceState, label: str, name: str) -> int:
        """Apply per-device hysteresis to suppress underpowered devices.

        Tracks whether a device consistently receives less power than its start
        threshold. Once accumulated deficit exceeds optimal, suppresses the device.
        """
        pwr_before = pwr
        if is_charge:
            abs_start = abs(start)
            abs_optimal = abs(optimal)
            abs_pwr = abs(pwr)

            delta = abs_start * SmartMode.HYSTERESIS_START_FACTOR - abs_pwr
            if delta >= 0:
                self.accumulator = 0
            else:
                self.accumulator += int(-delta)

            if self.accumulator > abs_optimal:
                pwr = 0
            _LOGGER.debug("%s: hysteresis[%s] abs_pwr=%s threshold=%s delta=%s accumulator=%s/%s => pwr %s->%s",
                          label, name, abs_pwr, abs_start * SmartMode.HYSTERESIS_START_FACTOR,
                          delta, self.accumulator, abs_optimal, pwr_before, pwr)
        elif state != DeviceState.SOCFULL:
            delta = start * SmartMode.HYSTERESIS_START_FACTOR - pwr
            if delta <= 0:
                self.accumulator = 0
            else:
                self.accumulator += int(delta)

            if self.accumulator > optimal:
                pwr = 0

        return pwr


# ---------------------------------------------------------------------------
#  Public API (called from manager.py)
# ---------------------------------------------------------------------------

def reset_power_state(mgr: ZendureManager) -> None:
    """Reset all power distribution lists and counters before recalculating."""
    mgr.zero_fast = datetime.max
    mgr.charge.clear()
    mgr.charge_limit = 0
    mgr.charge_optimal = 0
    mgr.charge_weight = 0
    mgr.discharge.clear()
    mgr.discharge_bypass = 0
    mgr.discharge_limit = 0
    mgr.discharge_optimal = 0
    mgr.discharge_produced = 0
    mgr.discharge_weight = 0
    mgr.idle.clear()
    mgr.idle_lvlmax = 0
    mgr.idle_lvlmin = 100
    mgr.produced = 0


async def classify_and_dispatch(mgr: ZendureManager, p1: int, isFast: bool, time: datetime) -> None:
    """Classify devices into charge/discharge/idle and dispatch to the active mode."""
    setpoint, available_kwh, power = await _classify_devices(mgr)
    _update_group_limits(mgr)

    mgr.power.update_value(power)
    mgr.availableKwh.update_value(available_kwh)

    if mgr.discharge_bypass > 0:
        setpoint = max(0 if p1 >= 0 else setpoint - mgr.discharge_bypass, setpoint - mgr.discharge_bypass)

    _LOGGER.info("P1 ======> p1:%s isFast:%s, setpoint:%sW stored:%sW", p1, isFast, setpoint, mgr.produced)
    await _dispatch_to_mode(mgr, p1, setpoint, isFast, time)


# ---------------------------------------------------------------------------
#  Phase 1: Classify devices into charge / discharge / idle
# ---------------------------------------------------------------------------

async def _classify_devices(mgr: ZendureManager) -> tuple[int, float, int]:
    """Classify each device and return (setpoint, available_kwh, power)."""
    available_kwh: float = 0
    setpoint = mgr.grid_port.power
    power = 0

    # Build port lookup once instead of searching per iteration
    offgrid_map: dict[str, OffGridPowerPort | None] = {}
    solar_map: dict[str, DcSolarPowerPort | None] = {}
    for d in mgr.devices:
        ports = mgr.device_ports.get(d.deviceId, [])
        offgrid_map[d.deviceId] = next((p for p in ports if isinstance(p, OffGridPowerPort)), None)
        solar_map[d.deviceId] = next((p for p in ports if isinstance(p, DcSolarPowerPort)), None)

    for d in mgr.devices:
        if not await d.power_get():
            continue

        offgrid_port = offgrid_map.get(d.deviceId)
        solar_port = solar_map.get(d.deviceId)
        offgrid_power = offgrid_port.power if offgrid_port else 0
        solar_power = solar_port.total_raw_solar if solar_port else 0

        d.pwr_produced = min(0,
                             d.batteryOutput.asInt + d.homeInput.asInt - d.batteryInput.asInt - d.homeOutput.asInt - solar_power)
        mgr.produced -= d.pwr_produced

        home, setpoint_delta = _classify_single_device(mgr, d, offgrid_power)
        setpoint += setpoint_delta
        available_kwh += d.actualKwh
        power += offgrid_power + home + d.pwr_produced

    return setpoint, available_kwh, power


def _classify_single_device(mgr: ZendureManager, d: ZendureDevice, offgrid_power: int) -> tuple[int, int]:
    """Classify one device into charge/discharge/idle. Returns (home, setpoint_delta)."""
    # SOCEMPTY and fully idle (no grid draw, no output, no battery activity)
    # If homeInput > 0 the device is already drawing from the grid and must be
    # classified as CHARGE so it receives the full setpoint, not just the
    # wake-up pulse.  Routing it to idle here would cap charge_limit to 0 and
    # prevent proper charging.
    if d.state == DeviceState.SOCEMPTY and d.homeInput.asInt == 0 and d.batteryInput.asInt == 0 and d.homeOutput.asInt == 0:
        mgr.idle.append(d)
        mgr.idle_lvlmax = max(mgr.idle_lvlmax, d.electricLevel.asInt)
        mgr.idle_lvlmin = min(mgr.idle_lvlmin, d.electricLevel.asInt)
        _LOGGER.debug("Classify %s => IDLE (SOCEMPTY): homeInput=%s offgrid=%s batteryIn=%s state=%s soc=%s",
                       d.name, d.homeInput.asInt, offgrid_power, d.batteryInput.asInt, d.state.name, d.electricLevel.asInt)
        return 0, 0

    # Charging (home input exceeds offgrid)
    home = -d.homeInput.asInt + offgrid_power
    if home < 0:
        mgr.charge.append(d)
        _LOGGER.debug("Classify %s => CHARGE: homeInput=%s offgrid=%s home=%s state=%s soc=%s setpoint_delta=%s",
                       d.name, d.homeInput.asInt, offgrid_power, home, d.state.name, d.electricLevel.asInt, -d.homeInput.asInt)
        return home, -d.homeInput.asInt

    # Discharging (home output or offgrid active)
    home = d.homeOutput.asInt
    if home > 0 or offgrid_power > 0:
        mgr.discharge.append(d)
        mgr.discharge_bypass -= d.pwr_produced if d.state == DeviceState.SOCFULL else 0
        mgr.discharge_produced -= d.pwr_produced

        net_battery = home - offgrid_power
        if home == 0 and net_battery <= 0:
            _LOGGER.debug("Classify %s => DISCHARGE (WAKEUP): homeOutput=%s offgrid=%s state=%s soc=%s",
                           d.name, d.homeOutput.asInt, offgrid_power, d.state.name, d.electricLevel.asInt)
            return home, 0
        else:
            _LOGGER.debug("Classify %s => DISCHARGE (ACTIVE): homeOutput=%s offgrid=%s state=%s soc=%s setpoint_delta=%s",
                           d.name, d.homeOutput.asInt, offgrid_power, d.state.name, d.electricLevel.asInt, net_battery)
            return home, home

    # Idle
    mgr.idle.append(d)
    mgr.idle_lvlmax = max(mgr.idle_lvlmax, d.electricLevel.asInt)
    mgr.idle_lvlmin = min(mgr.idle_lvlmin, d.electricLevel.asInt if d.state != DeviceState.SOCFULL else 100)
    _LOGGER.debug("Classify %s => IDLE: homeInput=%s homeOutput=%s offgrid=%s state=%s soc=%s",
                   d.name, d.homeInput.asInt, d.homeOutput.asInt, offgrid_power, d.state.name, d.electricLevel.asInt)
    return 0, 0


# ---------------------------------------------------------------------------
#  Phase 2: Update FuseGroup limits
# ---------------------------------------------------------------------------

def _update_group_limits(mgr: ZendureManager) -> None:
    """Compute per-device pwr_max via FuseGroup and update global aggregates."""
    for fg in {d.fuseGrp for d in mgr.charge}:
        fg.update_charge_limits()
    for fg in {d.fuseGrp for d in mgr.discharge}:
        fg.update_discharge_limits()

    mgr.charge_limit = sum(d.pwr_max for d in mgr.charge)
    mgr.discharge_limit = sum(d.pwr_max for d in mgr.discharge)
    mgr.charge_optimal = sum(d.charge_optimal for d in mgr.charge)
    mgr.discharge_optimal = sum(d.discharge_optimal for d in mgr.discharge)


# ---------------------------------------------------------------------------
#  Phase 3: Dispatch to mode handler
# ---------------------------------------------------------------------------

async def _dispatch_to_mode(mgr: ZendureManager, p1: int, setpoint: int, isFast: bool, time: datetime) -> None:
    """Dispatch to the active mode handler."""
    match mgr.operation:
        case ManagerMode.MATCHING:
            if setpoint < 0:
                await distribute_charge(mgr, setpoint, time)
            else:
                await distribute_discharge(mgr, setpoint)

        case ManagerMode.MATCHING_DISCHARGE:
            await distribute_discharge(mgr, max(0, setpoint))

        case ManagerMode.MATCHING_CHARGE | ManagerMode.STORE_SOLAR:
            if setpoint > 0 and mgr.produced > SmartMode.POWER_START and mgr.operation == ManagerMode.MATCHING_CHARGE:
                await distribute_discharge(mgr, min(mgr.produced, setpoint))
            elif setpoint > 0:
                await distribute_discharge(mgr, 0)
            else:
                await distribute_charge(mgr, min(0, setpoint), time)

        case ManagerMode.MANUAL:
            if (setpoint := int(mgr.manualpower.asNumber)) > 0:
                await distribute_discharge(mgr, setpoint)
                _LOGGER.info("Set Manual power discharging: isFast:%s, setpoint:%sW stored:%sW", isFast, setpoint, mgr.produced)
            else:
                await distribute_charge(mgr, setpoint, time)
                _LOGGER.info("Set Manual power charging: isFast:%s, setpoint:%sW stored:%sW", isFast, setpoint, mgr.produced)

        case ManagerMode.OFF:
            mgr.operationstate.update_value(ManagerState.OFF.value)


# ---------------------------------------------------------------------------
#  Charge / Discharge entry points
# ---------------------------------------------------------------------------

async def distribute_charge(mgr: ZendureManager, setpoint: int, time: datetime) -> None:
    """Prepare charge list and delegate distribution."""
    _LOGGER.info("Charge => setpoint %sW, devices=%s", setpoint, len(mgr.charge))

    active_devices = list(mgr.charge)

    # Promote chargeable discharge devices
    for d in mgr.discharge:
        if d.state != DeviceState.SOCFULL:
            d.pwr_max = max(d.fuseGrp.minpower, d.charge_limit)
            active_devices.append(d)
            _LOGGER.debug("Charge: promote discharge %s => charge (pwr_max=%s, charge_limit=%s, soc=%s)",
                           d.name, d.pwr_max, d.charge_limit, d.electricLevel.asInt)
        else:
            stop_pwr = 0 if d.pwr_offgrid == 0 else -SmartMode.POWER_IDLE_OFFSET
            _LOGGER.debug("Charge: stop discharge %s => power_charge(%s) [SOCFULL, offgrid=%s]", d.name, stop_pwr, d.pwr_offgrid)
            await d.power_charge(stop_pwr)

    await _distribute_power(mgr, active_devices, setpoint, CHARGE_DIR, time)


async def distribute_discharge(mgr: ZendureManager, setpoint: int) -> None:
    """Prepare discharge list and delegate distribution."""
    _LOGGER.info("Discharge => setpoint %sW", setpoint)
    mgr.operationstate.update_value(
        ManagerState.DISCHARGE.value if setpoint > 0 and mgr.discharge else ManagerState.IDLE.value)

    # Reset charge hysteresis on mode switch
    if mgr.hysteresis.charge_time != datetime.max:
        mgr.hysteresis.reset()

    # Stop charging devices
    for d in mgr.charge:
        await d.power_discharge(0 if max(0, d.pwr_offgrid) == 0 else SmartMode.POWER_IDLE_OFFSET)

    # Determine if we only need to pass through solar power
    solaronly = mgr.discharge_produced >= setpoint
    limit = mgr.discharge_produced if solaronly else mgr.discharge_limit
    setpoint = min(setpoint, limit)

    await _distribute_power(mgr, mgr.discharge, setpoint, DISCHARGE_DIR)


# ---------------------------------------------------------------------------
#  Core distribution logic
# ---------------------------------------------------------------------------

async def _distribute_power(
    mgr: ZendureManager,
    devices: list,
    setpoint: int,
    direction: _DistDirection,
    time: datetime | None = None,
) -> None:
    """Distribute power across devices proportionally by weight."""
    is_charge = direction.sign == -1
    label = direction.label

    total_limit, total_weight, optimal = _compute_weights(devices, is_charge)

    # Charge hysteresis (cooldown before allowing charge)
    if is_charge and time:
        setpoint = mgr.hysteresis.apply_charge_cooldown(setpoint, time, mgr.operationstate)

    dev_start = _compute_wakeup_threshold(setpoint, optimal, is_charge, mgr)
    setpoint = _cap_setpoint(setpoint, total_limit, is_charge, label)

    remaining = setpoint
    _LOGGER.debug("%s: distributing setpoint=%s across %s devices, dev_start=%s, weight=%s",
                  label, setpoint, len(devices), dev_start, total_weight)

    # Distribution loop
    for i, d in enumerate(sorted(devices, key=lambda d: d.electricLevel.asInt, reverse=direction.sort_high_first)):
        soc = d.electricLevel.asInt
        device_weight = d.pwr_max * (100 - soc) if is_charge else d.pwr_max * soc

        pwr = _compute_device_power(remaining, device_weight, total_weight, is_charge, devices, i, d)
        total_weight -= device_weight
        pwr_weighted = pwr

        # Clamping
        if is_charge:
            pwr = max(pwr, remaining, d.pwr_max)
        else:
            pwr = min(pwr, d.pwr_max, remaining)
        pwr_clamped = pwr

        # SOCFULL solar passthrough (discharge only)
        if not is_charge and pwr_weighted < -d.pwr_produced and d.state == DeviceState.SOCFULL:
            pwr = -d.pwr_produced

        # Per-device hysteresis (first device in multi-device setup)
        if len(devices) > 1 and i == 0:
            pwr = mgr.hysteresis.apply_device_suppression(pwr, is_charge, d.charge_start if is_charge else d.discharge_start, d.charge_optimal if is_charge else d.discharge_optimal, d.state, label, d.name)

        _LOGGER.debug("%s: [%s/%s] %s soc=%s%% pwr_max=%s weight=%s remaining=%s pwr: weighted=%s clamped=%s final=%s",
                      label, i, len(devices), d.name, soc, d.pwr_max, device_weight, remaining, pwr_weighted, pwr_clamped, pwr)

        # @todo: how does it work with multiple devices?
        # --- NEU: Min-SoC Schutz bei Entladung ---
        if not is_charge:
            min_soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= min_soc_limit:
                _LOGGER.warning("%s: Discharge blocked! SoC=%s%% too close to minSoc=%s%%",
                              d.name, d.electricLevel.asInt, min_soc_limit)
                pwr = 0

        # Apply power
        actual_pwr = await d.power_charge(pwr) if is_charge else await d.power_discharge(pwr)
        remaining -= actual_pwr

        # Wake-up tracking
        if is_charge:
            dev_start += -1 if pwr != 0 and soc > mgr.idle_lvlmin + SmartMode.SOC_IDLE_BUFFER else 0
        else:
            dev_start += 1 if pwr != 0 and soc + SmartMode.SOC_IDLE_BUFFER < mgr.idle_lvlmax else 0

        _LOGGER.debug("%s: [%s] %s actual=%s remaining_after=%s", label, i, d.name, actual_pwr, remaining)

    await _wake_idle_devices(mgr, dev_start, is_charge)


# ---------------------------------------------------------------------------
#  Distribution helpers
# ---------------------------------------------------------------------------

def _compute_weights(devices: list, is_charge: bool) -> tuple[int, int, int]:
    """Compute total_limit, total_weight and optimal for a device list."""
    total_limit = sum(d.pwr_max for d in devices)
    if is_charge:
        total_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devices)
        optimal = sum(d.charge_optimal for d in devices)
    else:
        total_weight = sum(d.pwr_max * d.electricLevel.asInt for d in devices)
        optimal = sum(d.discharge_optimal for d in devices)
    return total_limit, total_weight, optimal


def _cap_setpoint(setpoint: int, total_limit: int, is_charge: bool, label: str) -> int:
    """Cap setpoint to hardware limits."""
    if is_charge:
        capped = max(setpoint, total_limit)
    else:
        capped = min(setpoint, total_limit)
    if capped != setpoint:
        _LOGGER.debug("%s: setpoint capped by limit: %s => %s (limit=%s)", label, setpoint, capped, total_limit)
    return capped


def _compute_wakeup_threshold(setpoint: int, optimal: int, is_charge: bool, mgr: ZendureManager) -> int:
    """Compute the dev_start threshold for waking idle devices."""
    if is_charge:
        return min(0, setpoint - optimal * SmartMode.WAKEUP_CAPACITY_FACTOR) if setpoint < -SmartMode.POWER_START else 0
    else:
        return max(0, setpoint - optimal * SmartMode.WAKEUP_CAPACITY_FACTOR - mgr.discharge_produced) if setpoint > SmartMode.POWER_START else 0


def _compute_device_power(remaining: int, device_weight: int, total_weight: int, is_charge: bool, devices: list, index: int, d: Any) -> int:
    """Compute proportional power share for one device."""
    if total_weight != 0:
        return int(remaining * device_weight / total_weight)
    elif not is_charge and len(devices) > index:
        return int(remaining / (len(devices) - index))
    return 0


async def _wake_idle_devices(mgr: ZendureManager, dev_start: int, is_charge: bool) -> None:
    """Wake up idle devices if active devices are overloaded."""
    needs_wake = (dev_start < 0) if is_charge else (dev_start > 0)
    label = "Charge" if is_charge else "Discharge"
    _LOGGER.debug("%s: done distributing, dev_start=%s idle_count=%s", label, dev_start, len(mgr.idle))

    if not needs_wake or len(mgr.idle) == 0:
        return

    mgr.idle.sort(key=lambda d: d.electricLevel.asInt, reverse=not is_charge)
    for d in mgr.idle:
        if is_charge:
            await d.power_charge(
                -SmartMode.POWER_START - max(0, d.pwr_offgrid) if d.state != DeviceState.SOCFULL else -max(0, d.pwr_offgrid))
            if (dev_start := dev_start - d.charge_optimal * 2) >= 0:
                break
        else:
            if d.state == DeviceState.SOCEMPTY:
                continue

            # --- NEU: Min-SoC Schutz auch beim Aufwecken beachten ---
            min_soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= min_soc_limit:
                _LOGGER.debug("Discharge Wakeup blocked: %s SoC=%s%% at minSoc limit", d.name, d.electricLevel.asInt)
                continue
            # ---------------------------------------------------------

            await d.power_discharge(SmartMode.POWER_START)
            if (dev_start := dev_start - d.discharge_optimal * 2) <= 0:
                break
    mgr.hysteresis.reset_accumulator()
