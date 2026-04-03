"""Power distribution strategy: classification, charge/discharge distribution."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import DeviceState, ManagerMode, ManagerState, SmartMode
from .power_port import DcSolarPowerPort, OffGridPowerPort

if TYPE_CHECKING:
    from .manager import ZendureManager

_LOGGER = logging.getLogger(__name__)


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
    for fg in mgr.fuse_groups:
        fg.initPower = True


async def classify_and_dispatch(mgr: ZendureManager, p1: int, isFast: bool, time: datetime) -> None:
    """Classify devices into charge/discharge/idle and dispatch to the active mode."""
    availableKwh = 0
    setpoint = mgr.grid_port.power
    power = 0

    for d in mgr.devices:
        ports = mgr.device_ports.get(d.deviceId, [])
        offgrid_port = next((p for p in ports if isinstance(p, OffGridPowerPort)), None)
        solar_port = next((p for p in ports if isinstance(p, DcSolarPowerPort)), None)

        if await d.power_get():
            offgrid_power = offgrid_port.power if offgrid_port else 0
            solar_power = solar_port.total_raw_solar if solar_port else 0

            d.pwr_produced = min(0,
                                 d.batteryOutput.asInt + d.homeInput.asInt - d.batteryInput.asInt - d.homeOutput.asInt - solar_power)
            mgr.produced -= d.pwr_produced

            # --- Classification (Charge / Discharge / Idle) ---
            if d.state == DeviceState.SOCEMPTY and d.batteryInput.asInt == 0 and d.homeOutput.asInt == 0:
                home = 0
                mgr.idle.append(d)
                mgr.idle_lvlmax = max(mgr.idle_lvlmax, d.electricLevel.asInt)
                mgr.idle_lvlmin = min(mgr.idle_lvlmin, d.electricLevel.asInt)
                _LOGGER.debug("Classify %s => IDLE (SOCEMPTY): homeInput=%s offgrid=%s batteryIn=%s state=%s soc=%s", d.name, d.homeInput.asInt, offgrid_power, d.batteryInput.asInt, d.state.name, d.electricLevel.asInt)

            elif (home := -d.homeInput.asInt + offgrid_power) < 0:
                mgr.charge.append(d)
                mgr.charge_limit += d.fuseGrp.charge_limit(d)
                mgr.charge_optimal += d.charge_optimal
                mgr.charge_weight += d.pwr_max * (100 - d.electricLevel.asInt)
                setpoint += -d.homeInput.asInt
                _LOGGER.debug("Classify %s => CHARGE: homeInput=%s offgrid=%s home=%s state=%s soc=%s setpoint_delta=%s", d.name, d.homeInput.asInt, offgrid_power, home, d.state.name, d.electricLevel.asInt, -d.homeInput.asInt)

            elif (home := d.homeOutput.asInt) > 0 or offgrid_power > 0:
                mgr.discharge.append(d)
                mgr.discharge_bypass -= d.pwr_produced if d.state == DeviceState.SOCFULL else 0
                mgr.discharge_limit += d.fuseGrp.discharge_limit(d)
                mgr.discharge_optimal += d.discharge_optimal
                mgr.discharge_produced -= d.pwr_produced
                mgr.discharge_weight += d.pwr_max * d.electricLevel.asInt

                net_battery = home - offgrid_power

                if home == 0 and net_battery <= 0:
                    _LOGGER.debug("Classify %s => DISCHARGE (WAKEUP): homeOutput=%s offgrid=%s state=%s soc=%s", d.name, d.homeOutput.asInt, offgrid_power, d.state.name, d.electricLevel.asInt)
                else:
                    setpoint += home
                    _LOGGER.debug("Classify %s => DISCHARGE (ACTIVE): homeOutput=%s offgrid=%s state=%s soc=%s setpoint_delta=%s", d.name, d.homeOutput.asInt, offgrid_power, d.state.name, d.electricLevel.asInt, net_battery)

            else:
                mgr.idle.append(d)
                mgr.idle_lvlmax = max(mgr.idle_lvlmax, d.electricLevel.asInt)
                mgr.idle_lvlmin = min(mgr.idle_lvlmin,
                                       d.electricLevel.asInt if d.state != DeviceState.SOCFULL else 100)
                _LOGGER.debug("Classify %s => IDLE: homeInput=%s homeOutput=%s offgrid=%s state=%s soc=%s", d.name,
                              d.homeInput.asInt, d.homeOutput.asInt, offgrid_power, d.state.name,
                              d.electricLevel.asInt)

            availableKwh += d.actualKwh
            power += offgrid_power + home + d.pwr_produced

    # Update the power entities
    mgr.power.update_value(power)
    mgr.availableKwh.update_value(availableKwh)

    if mgr.discharge_bypass > 0:
        setpoint = max(0 if p1 >= 0 else setpoint - mgr.discharge_bypass, setpoint - mgr.discharge_bypass)

    # Dispatch to mode handler
    _LOGGER.info("P1 ======> p1:%s isFast:%s, setpoint:%sW stored:%sW", p1, isFast, setpoint, mgr.produced)
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


async def distribute_charge(mgr: ZendureManager, setpoint: int, time: datetime) -> None:
    """Distribute charge power across devices."""
    # Promote chargeable discharge devices into the charge distribution,
    # stop the rest. Without this, a device currently discharging would
    # contribute charge_limit=0 and cap the entire charge setpoint to 0.
    for d in mgr.discharge:
        if d.state != DeviceState.SOCFULL:
            d.pwr_max = max(d.fuseGrp.minpower, d.charge_limit)
            mgr.charge.append(d)
            mgr.charge_limit += d.pwr_max
            mgr.charge_optimal += d.charge_optimal
            mgr.charge_weight += d.pwr_max * (100 - d.electricLevel.asInt)
            _LOGGER.debug(
                "Charge: promote discharge %s => charge (pwr_max=%s, charge_limit=%s, soc=%s)",
                d.name, d.pwr_max, d.charge_limit, d.electricLevel.asInt,
            )
        else:
            stop_pwr = 0 if d.pwr_offgrid == 0 else -SmartMode.POWER_IDLE_OFFSET
            _LOGGER.debug("Charge: stop discharge %s => power_charge(%s) [SOCFULL, offgrid=%s]", d.name, stop_pwr, d.pwr_offgrid)
            await d.power_charge(stop_pwr)

    _LOGGER.info("Charge => setpoint %sW, devices=%s, charge_limit=%s, charge_optimal=%s",
                  setpoint, len(mgr.charge), mgr.charge_limit, mgr.charge_optimal)

    # prevent hysteria
    if mgr.charge_time > time:
        if mgr.charge_time == datetime.max:
            cooldown = (
                SmartMode.HYSTERESIS_FAST_COOLDOWN
                if (time - mgr.charge_last).total_seconds() > SmartMode.HYSTERESIS_LONG_COOLDOWN
                else SmartMode.HYSTERESIS_SLOW_COOLDOWN
            )
            mgr.charge_time = time + timedelta(seconds=cooldown)
            mgr.charge_last = mgr.charge_time
            mgr.pwr_low = 0
            _LOGGER.debug("Charge: hysteresis started, cooldown=%ss, charge_time=%s", cooldown, mgr.charge_time)
        _LOGGER.debug("Charge: hysteresis active, setpoint %s => 0 (waiting until %s)", setpoint, mgr.charge_time)
        setpoint = 0
    mgr.operationstate.update_value(ManagerState.CHARGE.value if setpoint < 0 else ManagerState.IDLE.value)

    # Cap setpoint to the maximum possible charge limit of all devices
    limit = mgr.charge_limit
    capped_setpoint = max(setpoint, limit)
    if capped_setpoint != setpoint:
        _LOGGER.debug("Charge: setpoint capped by charge_limit: %s => %s (limit=%s)", setpoint, capped_setpoint, limit)
    setpoint = capped_setpoint

    # Check if we need to wake up idle devices
    dev_start = min(0, setpoint - mgr.charge_optimal * SmartMode.WAKEUP_CAPACITY_FACTOR) if setpoint < -SmartMode.POWER_START else 0
    remaining_setpoint = setpoint
    _LOGGER.debug("Charge: distributing setpoint=%s across %s devices, dev_start=%s, charge_weight=%s",
                   setpoint, len(mgr.charge), dev_start, mgr.charge_weight)

    for i, d in enumerate(sorted(mgr.charge, key=lambda d: d.electricLevel.asInt, reverse=True)):
        device_weight = d.pwr_max * (100 - d.electricLevel.asInt)

        if mgr.charge_weight != 0:
            pwr = int(remaining_setpoint * device_weight / mgr.charge_weight)
        else:
            pwr = 0
        mgr.charge_weight -= device_weight
        pwr_weighted = pwr

        pwr = max(pwr, remaining_setpoint, d.pwr_max)

        pwr_clamped = pwr
        # Hysteresis logic for the first device in a multi-device setup
        pwr_before_hyst = pwr
        if len(mgr.charge) > 1 and i == 0:
            abs_start = abs(d.charge_start)
            abs_optimal = abs(d.charge_optimal)
            abs_pwr = abs(pwr)

            delta = abs_start * SmartMode.HYSTERESIS_START_FACTOR - abs_pwr
            if delta >= 0:
                mgr.pwr_low = 0
            else:
                mgr.pwr_low += int(-delta)

            if mgr.pwr_low > abs_optimal:
                pwr = 0
            _LOGGER.debug(
                "Charge: hysteresis[%s] abs_pwr=%s threshold=%s delta=%s pwr_low=%s/%s => pwr %s->%s",
                d.name, abs_pwr, abs_start * SmartMode.HYSTERESIS_START_FACTOR, delta, mgr.pwr_low, abs_optimal, pwr_before_hyst, pwr,
            )

        _LOGGER.debug(
            "Charge: [%s/%s] %s soc=%s%% pwr_max=%s weight=%s remaining=%s pwr: weighted=%s clamped=%s final=%s",
            i, len(mgr.charge), d.name, d.electricLevel.asInt, d.pwr_max, device_weight,
            remaining_setpoint, pwr_weighted, pwr_clamped, pwr,
        )

        actual_pwr = await d.power_charge(pwr)
        remaining_setpoint -= actual_pwr
        dev_start += -1 if pwr != 0 and d.electricLevel.asInt > mgr.idle_lvlmin + SmartMode.SOC_IDLE_BUFFER else 0

        _LOGGER.debug("Charge: [%s] %s actual=%s remaining_after=%s", i, d.name, actual_pwr, remaining_setpoint)

    # start idle device if needed
    _LOGGER.debug("Charge: done distributing, remaining=%s dev_start=%s idle_count=%s",
                   remaining_setpoint, dev_start, len(mgr.idle))
    if dev_start < 0 and len(mgr.idle) > 0:
        mgr.idle.sort(key=lambda d: d.electricLevel.asInt, reverse=False)
        for d in mgr.idle:
            await d.power_charge(
                -SmartMode.POWER_START - max(0, d.pwr_offgrid) if d.state != DeviceState.SOCFULL else -max(0,
                                                                                                           d.pwr_offgrid))
            if (dev_start := dev_start - d.charge_optimal * 2) >= 0:
                break
        mgr.pwr_low = 0


async def distribute_discharge(mgr: ZendureManager, setpoint: int) -> None:
    """Distribute discharge power across devices."""
    _LOGGER.info("Discharge => setpoint %sW", setpoint)
    mgr.operationstate.update_value(
        ManagerState.DISCHARGE.value if setpoint > 0 and mgr.discharge else ManagerState.IDLE.value)

    # reset hysteria time
    if mgr.charge_time != datetime.max:
        mgr.charge_time = datetime.max
        mgr.pwr_low = 0

    # stop charging devices
    for d in mgr.charge:
        await d.power_discharge(0 if max(0, d.pwr_offgrid) == 0 else SmartMode.POWER_IDLE_OFFSET)

    # Determine if we only need to pass through solar power
    solaronly = mgr.discharge_produced >= setpoint
    limit = mgr.discharge_produced if solaronly else mgr.discharge_limit

    # Cap setpoint to available limit
    setpoint = min(setpoint, limit)

    dev_start = max(0,
                    setpoint - mgr.discharge_optimal * SmartMode.WAKEUP_CAPACITY_FACTOR - mgr.discharge_produced) if setpoint > SmartMode.POWER_START else 0
    remaining_setpoint = setpoint

    for i, d in enumerate(sorted(mgr.discharge, key=lambda d: d.electricLevel.asInt, reverse=False)):
        device_weight = d.pwr_max * d.electricLevel.asInt

        if mgr.discharge_weight != 0:
            pwr = int(remaining_setpoint * device_weight / mgr.discharge_weight)
        elif len(mgr.discharge) > i:
            pwr = int(remaining_setpoint / (len(mgr.discharge) - i))
        else:
            pwr = 0

        # SOCFULL devices should only pass through solar, not drain battery
        if pwr < -d.pwr_produced and d.state == DeviceState.SOCFULL:
            pwr = -d.pwr_produced

        mgr.discharge_weight -= device_weight

        # Clamp 1: Device cannot discharge faster than its hardware limit
        pwr = min(pwr, d.pwr_max)

        # Clamp 2: Device cannot discharge more than what is left of the setpoint
        pwr = min(pwr, remaining_setpoint)

        # Hysteresis logic for the first device in a multi-device setup
        if len(mgr.discharge) > 1 and i == 0 and d.state != DeviceState.SOCFULL:
            delta = d.discharge_start * SmartMode.HYSTERESIS_START_FACTOR - pwr
            if delta <= 0:
                mgr.pwr_low = 0
            else:
                mgr.pwr_low += int(delta)

            if mgr.pwr_low > d.discharge_optimal:
                pwr = 0

        actual_pwr = await d.power_discharge(pwr)
        remaining_setpoint -= actual_pwr
        dev_start += 1 if pwr != 0 and d.electricLevel.asInt + 3 < mgr.idle_lvlmax else 0

    # start idle device if needed
    if dev_start > 0 and len(mgr.idle) > 0:
        mgr.idle.sort(key=lambda d: d.electricLevel.asInt, reverse=True)
        for d in mgr.idle:
            if d.state != DeviceState.SOCEMPTY:
                await d.power_discharge(SmartMode.POWER_START)
                if (dev_start := dev_start - d.discharge_optimal * 2) <= 0:
                    break
        mgr.pwr_low = 0
