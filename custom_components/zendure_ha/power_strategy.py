"""Power distribution strategy: assess, classify, dispatch.

Cycle shape (one call per dispatch tick):

    classify_and_dispatch(mgr, p1, isFast, time)
      └─ _assess(mgr, p1, time) -> SystemSnapshot
           ├─ _recover_wake_timeouts()   # self-heals stuck WAKEUP
           └─ _classify_devices()        # sorts devices into charge / discharge / idle
      └─ _update_group_limits(mgr)       # per-FuseGroup pwr_max caps
      └─ _dispatch_to_mode(...)          # mode → distribute_charge / distribute_discharge

Key components:
- SystemSnapshot: read-only carrier of the assess results (setpoint_raw, power,
  available_kwh, time). Passed through classify_and_dispatch.
- HysteresisFilter: mode-aware cooldown filter (`filter()`) plus the per-device
  deficit accumulator (`apply_device_suppression()`). No datetime sentinels —
  MANUAL bypasses, CHARGE<->DISCHARGE arms a cooldown, everything else passes.
- Command / DeviceAssignment / apply_assignment: typed command boundary. Every
  stop command routes through apply_assignment, which enforces the SF 2400
  quirk: STOP_CHARGE becomes power_discharge(0 or POWER_IDLE_OFFSET), never
  power_charge(0).
- distribute_charge / distribute_discharge: direction-specific dispatch; both
  delegate the proportional math to _distribute_power.
- _distribute_power: proportional weight-based distribution across active devices.
- _wake_idle_devices: two-pass wakeup (Pass 1: bypass-energy, Pass 2: grid-demand).
- _ramp_factor: soft-start factor for post-wakeup, near-minSoc, near-maxSoc.

Self-healing WAKEUP:
- A device stuck in PowerFlowState.WAKEUP past SmartMode.WAKE_TIMEOUT is reverted
  to IDLE at the start of the next assess cycle so it can be re-commanded.
- wake_started_at on ZendureDevice is the single source of truth for the timer.

Wakeup rules (inside _wake_idle_devices):
- wakeup commands are capped to the available surplus (raw_setpoint before hw-cap)
- pass1_woken guard: each device is woken at most once per cycle
- Ramping: ramp_factor is applied only in three specific scenarios — never globally
"""

# @todo calculate Selfconsumption with virtual Sensor?
#  for SF2400AC selfconsumption on charging is about 40W.
#  If Input Power is below 40W (charge(<40), with pwr_offgrid=0)
#  then Inverter consumes Batterypower to stay alive.
#  On charging SF2400AC with pwr_offgrid > 0, selfconsumption is added to pwr_offgrid on top.

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from .const import DeviceState, ManagerMode, PowerFlowState, SmartMode

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


def all_devices_blocked_no_solar(mgr: ZendureManager) -> bool:
    """True if all online devices are at/below the minSoC discharge limit AND no
    solar power is available. In this state no dispatch decision can change the
    outcome until external conditions shift, so the caller may safely slow the
    polling cadence (see `SmartMode.SLOW_POLL_INTERVAL`).
    """
    online = [d for d in mgr.devices if d.online]
    if not online:
        return False
    for d in online:
        if d.electricLevel.asInt > int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER:
            return False
        if d.solarPort and d.solarPort.total_solar_power > 0:
            return False
    return True


# ---------------------------------------------------------------------------
#  Hysteresis state: prevents rapid on/off cycling of devices
# ---------------------------------------------------------------------------

class Direction(Enum):
    """Power flow direction used by HysteresisFilter.filter() and (later) _decide."""

    NONE = 0
    CHARGE = -1
    DISCHARGE = +1


class Command(Enum):
    """Typed device command.

    CHARGE/DISCHARGE route to the obvious methods with a signed power.
    STOP_CHARGE and STOP_DISCHARGE are the *quirk-aware* stop commands: the
    SF 2400 AC misreports its direction after a plain `power_charge(0)` stop
    on an actively-charging device and re-enters the charging loop. The only
    safe way to stop a charging SF 2400 is `power_discharge(0 or 10)`; the
    symmetric case applies to stopping a discharging device. Using these
    enum values ensures a stop can never silently collapse back to
    `power_charge(0)` in the call site.
    """

    CHARGE = 1
    DISCHARGE = 2
    STOP_CHARGE = 3
    STOP_DISCHARGE = 4


@dataclass(frozen=True, slots=True)
class DeviceAssignment:
    """One device's command for one dispatch cycle.

    `power` is the signed value the distribution math produced; for STOP_*
    commands it is ignored and the 0/POWER_IDLE_OFFSET decision is made in
    `apply_assignment` based on the device's offgrid load.
    """

    command: Command
    power: int = 0


async def apply_assignment(d: Any, assignment: DeviceAssignment) -> int:
    """Single mutation site for device commands.

    Returns the actual power commanded (same contract as `power_charge` /
    `power_discharge`). Every stop path routes through here, so the SF 2400
    quirk is enforced structurally: a `STOP_CHARGE` can never emit
    `power_charge(0)`.
    """
    cmd = assignment.command
    if cmd == Command.CHARGE:
        return await d.power_charge(assignment.power)
    if cmd == Command.DISCHARGE:
        return await d.power_discharge(assignment.power)

    # Skip redundant stop commands when device is already idle (Vorschlag 02).
    if d.power_flow_state == PowerFlowState.IDLE:
        return 0

    offgrid_consumption = d.offgridPort.power_consumption if d.offgridPort else 0
    if cmd == Command.STOP_CHARGE:
        # SF 2400 quirk: a charging device must be stopped via power_discharge.
        stop_pwr = SmartMode.POWER_IDLE_OFFSET if offgrid_consumption > 0 else 0
        _LOGGER.info("Stopping charge %s with %s", d.name, stop_pwr)
        return await d.power_discharge(stop_pwr)
    # STOP_DISCHARGE: symmetric — stop a discharging device via power_charge.
    stop_pwr = SmartMode.POWER_IDLE_OFFSET if offgrid_consumption > 0 else 0
    _LOGGER.info("Stopping charge %s with %s", d.name, stop_pwr)
    return await d.power_discharge(stop_pwr)


@dataclass
class HysteresisFilter:
    """Mode-aware hysteresis state for power distribution.

    Two distinct responsibilities:
    - Cooldown filter via `filter()`: arms on CHARGE<->DISCHARGE transitions,
      passes through in MANUAL. No sentinel values, no re-arm loop.
    - Per-device suppression via `apply_device_suppression()` + `accumulator`:
      accumulates deficit when a device keeps getting less than its start
      threshold and eventually suppresses it entirely.
    """

    accumulator: int = 0

    _cooldown_until: datetime = field(default_factory=lambda: datetime.min)
    _last_direction: Direction = Direction.NONE
    _last_nonidle_time: datetime = field(default_factory=lambda: datetime.min)

    def filter(self, setpoint: int, direction: Direction, mode: ManagerMode, time: datetime) -> int:
        """Mode-aware setpoint filter.

        Rules:
        - MANUAL: user intent wins, cooldown and deadband are bypassed.
        - Deadband: |setpoint| < POWER_START is treated as noise — return 0 and
          leave `_last_direction` untouched so a sub-noise blip cannot flip the
          tracked direction between real cycles.
        - Direction switch (CHARGE <-> DISCHARGE): arm a cooldown whose length
          depends on how long ago the last non-idle cycle was (fast if
          >LONG_COOLDOWN ago, slow otherwise).
        - While cooldown is active: return 0.
        - Otherwise: pass setpoint through.

        Must be called on BOTH the charge and discharge paths — the cooldown
        can only detect transitions if `_last_direction` sees both sides.
        """
        if mode == ManagerMode.MANUAL:
            self._last_direction = direction
            self._cooldown_until = datetime.min
            if direction != Direction.NONE:
                self._last_nonidle_time = time
            return setpoint

        if direction != Direction.NONE and abs(setpoint) < SmartMode.POWER_START:
            return 0

        if direction != Direction.NONE:
            if self._last_direction not in (Direction.NONE, direction):
                gap = (time - self._last_nonidle_time).total_seconds()
                cooldown_s = (
                    SmartMode.HYSTERESIS_FAST_COOLDOWN
                    if gap > SmartMode.HYSTERESIS_LONG_COOLDOWN
                    else SmartMode.HYSTERESIS_SLOW_COOLDOWN
                )
                self._cooldown_until = time + timedelta(seconds=cooldown_s)
            self._last_direction = direction
            self._last_nonidle_time = time

        if time < self._cooldown_until:
            return 0
        return setpoint

    def reset_accumulator(self) -> None:
        """Zero only the per-device deficit accumulator (e.g. after a wake command)."""
        self.accumulator = 0

    def reset(self) -> None:
        """Voll-Reset: Cooldown, Direction-Tracker und Accumulator.

        Wird nach einem frisch comitteten WAKEUP (WAKEUP → CHARGE/DISCHARGE)
        aufgerufen. Der Direction-Change-Cooldown würde sonst den ersten
        Zyklus nach dem Wake auf 0 zurückhalten und das Gerät sofort wieder
        abwürgen (Abschalt-Transient → Moduswechsel-Flattern).
        """
        self._cooldown_until = datetime.min
        self._last_direction = Direction.NONE
        self.accumulator = 0

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

@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """Read-only result of the Assess phase.

    Captures the facts `_dispatch_to_mode` and the Phase 3 `_decide` need to
    make a decision: the raw setpoint (before hysteresis/capping), aggregate
    production/storage, and the wall-clock `time` the assessment was taken at.
    Intentionally minimal — device-level snapshots arrive in Phase 3.
    """

    setpoint_raw: int
    available_kwh: float
    power: int
    time: datetime


def _recover_wake_timeouts(devices: list, time: datetime) -> list:
    """Revert devices stuck in WAKEUP past SmartMode.WAKE_TIMEOUT back to IDLE.

    Returns the list of devices that were recovered (for logging/tests). This
    is the self-healing path for the sticky-WAKEUP bug: if a wake command
    never produced a battery response within the timeout, the device is
    reclassified as IDLE so the next cycle can re-command it.
    """
    recovered = []
    deadline = timedelta(seconds=SmartMode.WAKE_TIMEOUT)
    for d in devices:
        if d.power_flow_state != PowerFlowState.WAKEUP:
            continue
        if time - d.wake_started_at > deadline:
            _LOGGER.info("Wake timeout: %s stuck in WAKEUP for %ss => reverting to IDLE",
                         d.name, (time - d.wake_started_at).total_seconds())
            d.power_flow_state = PowerFlowState.IDLE
            recovered.append(d)
    return recovered


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
    snapshot = await _assess(mgr, p1, time)
    _update_group_limits(mgr)

    mgr.power.update_value(snapshot.power)
    mgr.availableKwh.update_value(snapshot.available_kwh)

    setpoint = snapshot.setpoint_raw
    if mgr.discharge_bypass > 0:
        setpoint = max(0 if p1 >= 0 else setpoint - mgr.discharge_bypass, setpoint - mgr.discharge_bypass)
    _LOGGER.info("P1 ======> p1:%s isFast:%s, setpoint:%sW stored:%sW", p1, isFast, setpoint, mgr.produced)
    await _dispatch_to_mode(mgr, p1, setpoint, isFast, time)


# @todo verwendet _p1 nicht?
async def _assess(mgr: ZendureManager, _p1: int, time: datetime) -> SystemSnapshot:
    """Assess phase: self-heal WAKE_TIMEOUT, classify devices, build a snapshot.

    The single mutation here is `_recover_wake_timeouts`: it reverts stuck
    WAKEUP devices to IDLE *before* classification so the recovered device
    participates in the same cycle's dispatch. Everything else the snapshot
    captures is pure read.
    """
    _recover_wake_timeouts(mgr.devices, time)
    setpoint, available_kwh, power = await _classify_devices(mgr)
    return SystemSnapshot(
        setpoint_raw=setpoint,
        available_kwh=available_kwh,
        power=power,
        time=time,
    )


# ---------------------------------------------------------------------------
#  Phase 1: Classify devices into charge / discharge / idle
# ---------------------------------------------------------------------------

async def _classify_devices(mgr: ZendureManager) -> tuple[int, float, int]:
    """Classify each device and return (setpoint, available_kwh, power)."""
    available_kwh: float = 0
    setpoint = mgr.grid_smartmeter.power
    power = 0

    for d in mgr.devices:
        if not await d.update_state():
            continue

        mgr.produced -= d.pwr_produced

        connector_power, grid_impact = _classify_single_device(mgr, d)
        setpoint += grid_impact
        available_kwh += d.actualKwh
        power += d.offgrid_power + connector_power + d.pwr_produced

    return setpoint, available_kwh, power

#@todo encapsulate power values:
def _classify_single_device(mgr: ZendureManager, d: ZendureDevice) -> tuple[int, int]:
    """Sortiert Device in Manager-Listen basierend auf power_flow_state."""
    offgrid_load = d.offgridPort.power_consumption if d.offgridPort else 0
    match d.power_flow_state:
        case PowerFlowState.OFF:
            return 0, 0

        case PowerFlowState.CHARGE:
            # @todo should it be d.batteryPort.power?
            connector_power = -d.connectorPort.power_consumption + offgrid_load
            mgr.charge.append(d)
            _LOGGER.debug("Classify %s => CHARGE: gridConsumption=%s offgrid=%s connector_power=%s soc=%s",
                          d.name, d.connectorPort.power_consumption, offgrid_load, connector_power, d.electricLevel.asInt)
            return connector_power, -d.connectorPort.power_consumption

        case PowerFlowState.DISCHARGE:
            connector_power = d.connectorPort.power_production
            mgr.discharge.append(d)
            mgr.discharge_bypass -= d.pwr_produced if d.state == DeviceState.SOCFULL else 0
            mgr.discharge_produced -= d.pwr_produced
            # @todo should it be d.batteryPort.power?
            net_battery = connector_power - offgrid_load
            _LOGGER.debug("Classify %s => DISCHARGE: feedIn=%s offgrid=%s soc=%s",
                          d.name, connector_power, offgrid_load, d.electricLevel.asInt)
            return connector_power, (0 if connector_power == 0 and net_battery <= 0 else connector_power)

        case _:  # IDLE, WAKEUP   (BYPASS removed — use device.is_bypassing instead)
            mgr.idle.append(d)
            mgr.idle_lvlmax = max(mgr.idle_lvlmax, d.electricLevel.asInt)
            mgr.idle_lvlmin = min(mgr.idle_lvlmin, d.electricLevel.asInt if d.state != DeviceState.SOCFULL else 100)
            _LOGGER.debug("Classify %s => %s: gridConsumption=%s offgrid=%s soc=%s",
                          d.name, d.power_flow_state.name, d.connectorPort.power_consumption, offgrid_load, d.electricLevel.asInt)
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
                await distribute_discharge(mgr, setpoint, time)

        case ManagerMode.MATCHING_DISCHARGE:
            await distribute_discharge(mgr, max(0, setpoint), time)

        case ManagerMode.MATCHING_CHARGE | ManagerMode.STORE_SOLAR:
            if setpoint > 0 and mgr.produced > SmartMode.POWER_START and mgr.operation == ManagerMode.MATCHING_CHARGE:
                await distribute_discharge(mgr, min(mgr.produced, setpoint), time)
            elif setpoint > 0:
                pass
                #await distribute_discharge(mgr, 0, time)
            else:
                await distribute_charge(mgr, min(0, setpoint), time)

        case ManagerMode.MANUAL:
            if (setpoint := int(mgr.manualpower.asNumber)) > 0:
                await distribute_discharge(mgr, setpoint, time)
                _LOGGER.info("Set Manual power discharging: isFast:%s, setpoint:%sW stored:%sW", isFast, setpoint, mgr.produced)
            else:
                await distribute_charge(mgr, setpoint, time)
                _LOGGER.info("Set Manual power charging: isFast:%s, setpoint:%sW stored:%sW", isFast, setpoint, mgr.produced)

        case ManagerMode.OFF:
            mgr.power_flow_sensor.update_value(PowerFlowState.OFF.value)


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
            _LOGGER.debug("Charge: stop discharge %s [SOCFULL, offgrid=%s]", d.name, d.offgrid_power)
            await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    # @todo review this condition, becaus its different to distribute_charge()
    # Cold-start wakeup: kein aktives Charge-Gerät, aber Überschuss vorhanden.
    # Analog zum Discharge-Pfad: echten Setpoint-Anteil senden, damit
    # batteryInput über die is_charging-Schwelle kommt und der IDLE-Loop
    # durchbrochen wird.
    if not active_devices and mgr.idle and setpoint < -SmartMode.POWER_IDLE_OFFSET:
        _LOGGER.debug("Charge cold-start: entered (idle=%d, setpoint=%s)", len(mgr.idle), setpoint)
        woken = False
        for d in sorted(mgr.idle, key=lambda d: d.electricLevel.asInt):
            if d.state == DeviceState.SOCFULL:
                _LOGGER.debug("Charge cold-start: skip %s [SOCFULL]", d.name)
                continue
            wake_power = max(setpoint, d.charge_limit)  # beides negativ; charge_limit ist untere Schranke
            _LOGGER.debug("Charge: cold-start wake %s => power_charge(%s)", d.name, wake_power)
            await d.power_charge(wake_power)
            if d.power_flow_state == PowerFlowState.IDLE:
                d.power_flow_state = PowerFlowState.WAKEUP
                d.wakeup_entered = time
                d.wake_started_at = time
            woken = True
            break  # ein Gerät pro Zyklus aufwecken
        if not woken:
            _LOGGER.debug("Charge cold-start: no eligible idle device (all SOCFULL)")

    await _distribute_power(mgr, active_devices, setpoint, CHARGE_DIR, time)


async def distribute_discharge(mgr: ZendureManager, setpoint: int, time: datetime) -> None:
    """Prepare discharge list and delegate distribution."""
    _LOGGER.info("Discharge => setpoint %sW", setpoint)
    mgr.power_flow_sensor.update_value(
        PowerFlowState.DISCHARGE.value if setpoint > 0 and mgr.discharge else PowerFlowState.IDLE.value)

    # Stop charging devices (but never discharge SOCEMPTY or near-empty devices)
    for d in mgr.charge:
        if d.state == DeviceState.SOCEMPTY:
            continue
        if d.electricLevel.asInt <= int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER:
            continue
        await apply_assignment(d, DeviceAssignment(Command.STOP_CHARGE))

    # Cold-start wakeup: kein aktives Discharge-Gerät, aber Bedarf vorhanden.
    # Echten Setpoint-Anteil senden (nicht nur 10W Keepalive), sonst bleibt
    # batteryOutput auf der is_discharging-Schwelle stehen und IDLE-Loop zementiert.
    if not mgr.discharge and mgr.idle and setpoint > SmartMode.POWER_IDLE_OFFSET:
        _LOGGER.debug("Discharge cold-start: entered (idle=%d, setpoint=%s)", len(mgr.idle), setpoint)
        woken = False
        for d in sorted(mgr.idle, key=lambda d: d.electricLevel.asInt, reverse=True):
            if d.state == DeviceState.SOCEMPTY:
                _LOGGER.debug("Discharge cold-start: skip %s [SOCEMPTY]", d.name)
                continue
            min_soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= min_soc_limit:
                _LOGGER.debug("Discharge cold-start: skip %s SoC=%s%% at minSoc limit (%s)",
                              d.name, d.electricLevel.asInt, min_soc_limit)
                continue
            # @todo review this condition, becaus its different to distribute_charge()
            wake_power = min(setpoint, d.discharge_limit)
            _LOGGER.debug("Discharge: cold-start wake %s => power_discharge(%s)", d.name, wake_power)
            await d.power_discharge(wake_power)
            # Zustand auf WAKEUP heben, damit _recover_wake_timeouts bei
            # fehlender Hardware-Antwort self-healed und der nächste Zyklus
            # nicht erneut als kompletter Cold-Start behandelt wird.
            if d.power_flow_state == PowerFlowState.IDLE:
                d.power_flow_state = PowerFlowState.WAKEUP
                d.wakeup_entered = time
                d.wake_started_at = time
            woken = True
            break  # ein Gerät pro Zyklus aufwecken
        if not woken:
            _LOGGER.debug("Discharge cold-start: no eligible idle device (all SOCEMPTY or below minSoc)")

    # Determine if we only need to pass through solar power
    solaronly = mgr.discharge_produced >= setpoint
    limit = mgr.discharge_produced if solaronly else mgr.discharge_limit
    setpoint = min(setpoint, limit)

    await _distribute_power(mgr, mgr.discharge, setpoint, DISCHARGE_DIR, time)


# ---------------------------------------------------------------------------
#  Core distribution logic
# ---------------------------------------------------------------------------

async def _distribute_power(
    mgr: ZendureManager,
    devices: list,
    setpoint: int,
    direction: _DistDirection,
    time: datetime,
) -> None:
    """Distribute power across devices proportionally by weight."""
    is_charge = direction.sign == -1
    label = direction.label

    total_limit, total_weight, optimal = _compute_weights(devices, is_charge)

    # Fresh WAKEUP commit: der Hysterese-Filter würde beim Direction-Change
    # (z.B. CHARGE → DISCHARGE nach Cold-Start-Wake) den ersten Zyklus auf 0
    # zurückhalten und das gerade hochgefahrene Gerät wieder abwürgen. Das
    # erzeugt einen Abschalt-Transient (Rückstrom in den Akku), der als
    # fälschliches CHARGE klassifiziert wird und Moduswechsel-Flattern triggert.
    # Consume-and-reset: jedes Gerät das WAKEUP → CHARGE/DISCHARGE committet hat
    # löst einen Hysterese-Reset aus, und das Flag wird gelöscht.
    for d in devices:
        if d.wakeup_committed:
            _LOGGER.debug("%s: fresh WAKEUP commit on %s → reset hysteresis", label, d.name)
            mgr.hysteresis.reset()
            d.wakeup_committed = False

    # Mode-aware hysteresis: must run on BOTH charge and discharge paths so the
    # filter's internal `_last_direction` sees both sides and can detect CHARGE<->
    # DISCHARGE transitions. MANUAL bypass and the |setpoint|<POWER_IDLE_OFFSET deadband
    # are handled inside filter() itself.
    filter_direction = Direction.CHARGE if is_charge else Direction.DISCHARGE
    setpoint = mgr.hysteresis.filter(setpoint, filter_direction, mgr.operation, time)
    if is_charge:
        mgr.power_flow_sensor.update_value(
            PowerFlowState.CHARGE.value if setpoint < 0 else PowerFlowState.IDLE.value,
        )

    dev_start = _compute_wakeup_threshold(setpoint, optimal, is_charge, mgr)
    raw_setpoint = setpoint  # save before capping; used as surplus ceiling in wakeup passes
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

        # --- NEU: Min-SoC Schutz bei Entladung ---
        if not is_charge:
            min_soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= min_soc_limit:
                _LOGGER.warning("%s: Discharge blocked! SoC=%s%% too close to minSoc=%s%%",
                              d.name, d.electricLevel.asInt, min_soc_limit)
                pwr = 0

        # @todo turn of ramp
        # Soft-start ramp (post-wakeup, near minSoc/maxSoc boundaries)
        if time is not None and pwr != 0:
            pwr = pwr
            #pwr = int(pwr * _ramp_factor(d, soc, is_charge, time))

        # Apply power
        actual_pwr = await d.power_charge(pwr) if is_charge else await d.power_discharge(pwr)
        remaining -= actual_pwr

        # Wake-up tracking
        if is_charge:
            dev_start += -1 if pwr != 0 and soc > mgr.idle_lvlmin + SmartMode.SOC_IDLE_BUFFER else 0
        else:
            dev_start += 1 if pwr != 0 and soc + SmartMode.SOC_IDLE_BUFFER < mgr.idle_lvlmax else 0

        _LOGGER.debug("%s: [%s] %s actual=%s remaining_after=%s", label, i, d.name, actual_pwr, remaining)

    await _wake_idle_devices(mgr, dev_start, is_charge, raw_setpoint)


# ---------------------------------------------------------------------------
#  Distribution helpers
# ---------------------------------------------------------------------------

def _ramp_factor(d: Any, soc: int, is_charge: bool, time: datetime) -> float:
    """Return 0.0–1.0 soft-start factor. Applied only in three specific scenarios:
    1. Directly after WAKEUP→CHARGE/DISCHARGE transition (post-wakeup ramp).
    2. When discharging near minSoc + SOC_IDLE_BUFFER.
    3. When charging near maxSoc + SOC_IDLE_BUFFER.
    """
    if d.wakeup_entered != datetime.min:
        elapsed = (time - d.wakeup_entered).total_seconds()
        if elapsed < SmartMode.WAKEUP_RAMP_DURATION:
            factor = elapsed / SmartMode.WAKEUP_RAMP_DURATION
            _LOGGER.debug("%s: post-wakeup ramp=%.2f (%.1fs/%.0fs)",
                          d.name, factor, elapsed, SmartMode.WAKEUP_RAMP_DURATION)
            return factor
        d.wakeup_entered = datetime.min  # ramp complete

    if not is_charge:
        min_limit = int(d.minSoc.asNumber) + SmartMode.SOC_IDLE_BUFFER
        if soc <= min_limit + SmartMode.SOC_IDLE_BUFFER:
            margin = soc - int(d.minSoc.asNumber)
            return max(0.0, margin / (SmartMode.SOC_IDLE_BUFFER * 2))
    else:
        max_soc = int(d.socSet.asNumber)
        if soc >= max_soc - SmartMode.SOC_IDLE_BUFFER * 2:
            margin = max_soc - soc
            return max(0.0, margin / (SmartMode.SOC_IDLE_BUFFER * 2))

    return 1.0


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


def _need_wakeup(d: "ZendureDevice") -> bool:
    """True if device qualifies for bypass-energy wakeup (Pass 1).

    Conditions: bypass active (implies energy flowing) + battery idle + SOCEMPTY or SOCFULL.
    """
    return (
        d.bypass.is_active
        and d.power_flow_state == PowerFlowState.IDLE
        and d.state in (DeviceState.SOCEMPTY, DeviceState.SOCFULL)
    )


async def _wake_idle_devices(mgr: ZendureManager, dev_start: int, is_charge: bool, setpoint: int = 0) -> None:
    """Wake idle/bypass devices. Charge: bypass-energy pass first, then grid-demand pass. Discharge: idle only."""
    _LOGGER.debug("%s: dev_start=%s idle=%s", "Charge" if is_charge else "Discharge", dev_start, len(mgr.idle))

    pass1_woken: set = set()  # devices handled by Pass 1 this iteration — skip in Pass 2

    if is_charge:
        # Pass 1: wake BYPASS devices — battery must receive/deliver net POWER_START despite bypass offset
        _cooldown = timedelta(seconds=SmartMode.BYPASS_WAKE_COOLDOWN)
        for d in mgr.idle:
            if not _need_wakeup(d):
                continue
            if datetime.now() < d.wake_started_at + _cooldown:
                _LOGGER.debug("Bypass-wake %s: cooldown active (next at %s)",
                              d.name, d.wake_started_at + _cooldown)
                continue
            solar        = d.solarPort.total_solar_power if d.solarPort else 0
            offgrid_in   = d.offgridPort.power_production       if d.offgridPort else 0
            # If device is SOCEMPTY, all power from offgrid_load is already consumed from grid
            offgrid_load = d.offgridPort.power_consumption   if d.offgridPort else 0
            bypass_load = solar + offgrid_in - offgrid_load
            device_power = d.connectorPort.power if d.connectorPort else 0

            if d.state == DeviceState.SOCEMPTY:
                pwr = -(SmartMode.POWER_IDLE_OFFSET + device_power)
                if setpoint < 0:
                    pwr_capped = max(pwr, setpoint)  # never request more than available surplus
                    if pwr_capped != pwr:
                        _LOGGER.debug("Bypass-wake SOCEMPTY %s: cap %s => %s (device=%s surplus=%s)", d.name, pwr, pwr_capped, device_power, setpoint)
                    pwr = pwr_capped
                _LOGGER.debug("Bypass-wake SOCEMPTY %s => power_charge(%s) [bypass=%s device=%s offgrid_load=%s]",
                              d.name, pwr, bypass_load, device_power, offgrid_load)
                await d.power_charge(pwr)
                d.wake_started_at = datetime.now()
                d.power_flow_state = PowerFlowState.WAKEUP
                pass1_woken.add(d)

            elif d.state == DeviceState.SOCFULL:
                pwr = SmartMode.POWER_IDLE_OFFSET + device_power
                _LOGGER.debug("Bypass-wake SOCFULL %s => power_discharge(%s) [bypass=%s device=%s offgrid_load=%s]",
                              d.name, pwr, bypass_load, device_power, offgrid_load)
                await d.power_discharge(pwr)
                d.wake_started_at = datetime.now()
                d.power_flow_state = PowerFlowState.WAKEUP
                pass1_woken.add(d)

    needs_wake = (dev_start < 0) if is_charge else (dev_start > 0)
    if not needs_wake:
        mgr.hysteresis.reset_accumulator()
        return

    if is_charge:
        # Pass 2: wake remaining devices based on grid demand (highest SOC first)
        for d in sorted(mgr.idle, key=lambda d: d.electricLevel.asInt, reverse=True):
            if d in pass1_woken:
                continue  # Pass 1 already handled this device this iteration
            if d.power_flow_state == PowerFlowState.WAKEUP and mgr.operation != ManagerMode.MANUAL:
                continue  # already waiting for battery to respond (MANUAL re-commands anyway)
            offgrid_load = d.offgridPort.power_consumption if d.offgridPort else 0
            if d.state == DeviceState.SOCFULL:
                await d.power_charge(-offgrid_load)
            elif d.state == DeviceState.SOCEMPTY:
                pwr = min(dev_start, -SmartMode.POWER_START) - offgrid_load
                if setpoint < 0:
                    pwr_capped = max(pwr, setpoint)  # never request more than available surplus
                    if pwr_capped != pwr:
                        _LOGGER.debug("Charge wake SOCEMPTY %s: cap %s => %s (surplus=%s)", d.name, pwr, pwr_capped, setpoint)
                    pwr = pwr_capped
                _LOGGER.debug("Charge: wake SOCEMPTY %s %s => power_charge(%s)", d.power_flow_state.name, d.name, pwr)
                await d.power_charge(pwr)
                d.power_flow_state = PowerFlowState.WAKEUP
            else:
                pwr = -SmartMode.POWER_START - offgrid_load
                _LOGGER.debug("Charge: wake %s %s => power_charge(%s)", d.power_flow_state.name, d.name, pwr)
                await d.power_charge(pwr)
            if d.state != DeviceState.SOCEMPTY:
                if (dev_start := dev_start - d.charge_optimal * 2) >= 0:
                    break
    else:
        # Discharge: SOCEMPTY and WAKEUP devices are never discharged
        for d in sorted(mgr.idle, key=lambda d: d.electricLevel.asInt):
            if d.state == DeviceState.SOCEMPTY or d.power_flow_state == PowerFlowState.WAKEUP:
                continue
            min_soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= min_soc_limit:
                _LOGGER.debug("Discharge blocked: %s SoC=%s%% at limit", d.name, d.electricLevel.asInt)
                continue
            await d.power_discharge(SmartMode.POWER_IDLE_OFFSET)
            if (dev_start := dev_start - d.discharge_optimal * 2) <= 0:
                break
    mgr.hysteresis.reset_accumulator()
