"""Collaborator classes for `ZendureDevice` (see `docs/plans/vorschlag-06`).

Splits the former god-class into three cooperating components, each testable
in isolation:

- `DevicePortBundle`       — constructs and owns all `PowerPort` instances.
- `DevicePowerFlowStateMachine` — derives `power_flow_state` from port data.
- `MqttProtocolHandler`    — thin object facade over the `mqtt_protocol` module.

`ZendureDevice` becomes a facade that holds these three collaborators and
exposes legacy attributes (`batteryPort`, `connectorPort`, ...) as properties
for backward compatibility.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import AcMode, DeviceState, PowerFlowState, SmartMode, WakeupCommand
from .power_port import (
    BatteryPowerPort,
    ConnectorPowerPort,
    DcSolarPowerPort,
    InverterLossPowerPort,
    OffGridPowerPort,
    PowerPort,
)

if TYPE_CHECKING:
    from paho.mqtt import client as mqtt_client

    from .device import ZendureDevice
    from .entity import EntityZendure

_LOGGER = logging.getLogger(__name__)


class DevicePortBundle:
    """Owns every `PowerPort` a device can have.

    Port construction depends on device flags (`pv_port_count`, `maxSolar`,
    `_has_offgrid`) so `init()` must be called *after* the subclass has set
    those fields.
    """

    def __init__(self, device: ZendureDevice) -> None:
        self.device = device
        self.connector: ConnectorPowerPort | None = None
        self.battery: BatteryPowerPort | None = None
        self.solar: DcSolarPowerPort | None = None
        self.offgrid: OffGridPowerPort | None = None
        self.inverter_loss: InverterLossPowerPort | None = None
        self.all: list[PowerPort] = []

    def init(self) -> None:
        """Build the ports applicable to this device."""
        from .sensor import ZendureRestoreSensor, ZendureSensor

        d = self.device

        self.connector = ConnectorPowerPort(d)
        self.all.append(self.connector)

        self.battery = BatteryPowerPort(d)
        self.all.append(self.battery)

        if d.pv_port_count > 0 and d.maxSolar != 0:
            solar_sensors: list[ZendureSensor] = [d.solarInput]
            for i in range(2, d.pv_port_count + 1):
                extra_sensor = ZendureSensor(
                    d, f"solarInputPower_{i}", None, "W", "power", "measurement", icon="mdi:solar-panel"
                )
                solar_sensors.append(extra_sensor)
            self.solar = DcSolarPowerPort(d, solar_sensors)
            self.all.append(self.solar)

        if d._has_offgrid:
            d.offGrid = ZendureSensor(d, "gridOffPower", None, "W", "power", "measurement")
            d.aggrOffGrid = ZendureRestoreSensor(
                d, "aggrGridOffPower", None, "kWh", "energy", "total_increasing", 2
            )
            self.offgrid = OffGridPowerPort(d)
            self.all.append(self.offgrid)

        self.inverter_loss = InverterLossPowerPort(d)
        self.all.append(self.inverter_loss)

    def invalidate_all(self) -> None:
        """Leert den Power-Cache aller Ports zum Zyklusstart."""
        for port in self.all:
            port.invalidate()


class DevicePowerFlowStateMachine:
    """Derives `power_flow_state` from packState, acMode, battery ports, and wakeup_cmd.

    Entry point for callers: `classify()` \u2014 pure, no side effects, returns PowerFlowState.
    `update()` calls `classify()` and owns all lifecycle mutations (wakeup_cmd, sensor updates).

    Classification precedence:
      packState=0, cmd set    \u2192 WAKEUP  (Sunset-Bug fix: SF2400 briefly reports 0 mid-wakeup)
      packState=0, no cmd     \u2192 IDLE
      packState=1, acMode=OUTPUT \u2192 IDLE  (CH\u2192DIS transition; prevents redundant STOP_CHARGE)
      packState=1, acMode=INPUT, is_charging \u2192 CHARGE
      packState=1, acMode=INPUT, !charging, cmd \u2192 WAKEUP
      packState=2 + SOCEMPTY  \u2192 IDLE
      packState=2, is_discharging \u2192 DISCHARGE
      packState=2, bypass      \u2192 IDLE
      packState=2, SOCFULL, cmd \u2192 WAKEUP
      packState=2, SoC guard   \u2192 IDLE
      packState=2, cmd set     \u2192 WAKEUP  (waiting for battery; timeout managed by update())
      packState=2, no cmd      \u2192 IDLE
      Fallback (packState unknown) \u2192 power-based legacy classification
    """

    def __init__(self, device: ZendureDevice) -> None:
        self.device = device

    def classify(self) -> PowerFlowState:
        """Derive the current PowerFlowState from device signals. No side effects."""
        d = self.device
        cmd = d.wakeup_cmd

        if d.state == DeviceState.OFFLINE:
            return PowerFlowState.OFF

        pack_state = d.packState.asInt  # 0=Standby, 1=Charging, 2=Discharging
        ac_mode    = d.acMode.asInt     # AcMode.INPUT=1, AcMode.OUTPUT=2

        if pack_state == 0:
            # Standby \u2014 but a pending command means the device is starting up.
            # SF2400 AC briefly returns packState=0 after firmware accepts the command
            # (acMode + limit echo received). Without this WAKEUP return, the next
            # dispatch would see IDLE and send a STOP \u2192 Sunset-Bug.
            return PowerFlowState.WAKEUP if cmd is not None else PowerFlowState.IDLE

        if pack_state == 1:
            if ac_mode == AcMode.OUTPUT:
                # Firmware already switched direction; prevent redundant STOP_CHARGE
                # (root cause of the 15-20s CH\u2192DIS standby detour).
                return PowerFlowState.IDLE
            # acMode=INPUT: charging path
            if d.batteryPort.is_charging:
                return PowerFlowState.CHARGE
            # packState=1 but battery not yet pulling power: startup ramp in progress
            return PowerFlowState.WAKEUP if cmd is not None else PowerFlowState.IDLE

        if pack_state == 2:
            if d.state == DeviceState.SOCEMPTY:
                return PowerFlowState.IDLE
            # Battery physically delivering power \u2014 Eigenverbrauch has negative batteryPort.power
            # (outputPackPower only, packInputPower=0), so is_discharging is False there.
            if d.batteryPort.is_discharging:
                return PowerFlowState.DISCHARGE
            if d.bypass.is_active:
                return PowerFlowState.IDLE
            if d.state == DeviceState.SOCFULL:
                return PowerFlowState.WAKEUP if cmd is not None else PowerFlowState.IDLE
            soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= soc_limit:
                return PowerFlowState.IDLE
            # packState=2 but battery not yet delivering: WAKEUP if we sent a command,
            # IDLE otherwise (timeout managed by update(), not here).
            return PowerFlowState.WAKEUP if cmd is not None else PowerFlowState.IDLE

        # Fallback: packState unknown / sensor not yet populated \u2014 power-based classification.
        if d.state == DeviceState.SOCFULL:
            if d.batteryPort.is_discharging:
                return PowerFlowState.DISCHARGE
            return PowerFlowState.WAKEUP if cmd is not None else PowerFlowState.IDLE
        if d.state == DeviceState.SOCEMPTY:
            return PowerFlowState.CHARGE if d.batteryPort.is_charging else PowerFlowState.IDLE
        if d.batteryPort.is_charging:
            return PowerFlowState.CHARGE
        if d.batteryPort.is_discharging:
            return PowerFlowState.DISCHARGE
        return PowerFlowState.IDLE

    def update(self) -> None:
        """Classify and apply state transitions; manage WakeupCommand lifecycle."""
        d = self.device
        cmd = d.wakeup_cmd

        # 1. Update confirmation state from latest firmware echo.
        #    Direction must match so default sensor values (acMode=INPUT, limit=0) don't
        #    trigger false positives before any MQTT arrives.
        if cmd is not None:
            ac_mode = d.acMode.asInt
            if ac_mode == cmd.direction:
                received_limit = (
                    d.limitOutput.asInt if cmd.direction == AcMode.OUTPUT else d.limitInput.asInt
                )
                if received_limit == 0:
                    # STOP echo: firmware accepted a stop \u2014 abort pending wakeup.
                    _LOGGER.debug("Wakeup cancelled %s: STOP echo (dir=%d)", d.name, cmd.direction)
                    d.wakeup_cmd = None
                    cmd = None
                elif received_limit >= SmartMode.POWER_START and not cmd.confirmed:
                    _LOGGER.debug(
                        "Wakeup confirmed %s: dir=%d limit=%dW",
                        d.name, cmd.direction, received_limit,
                    )
                    cmd.confirmed = True

        # 2. Timeout \u2014 clear commands that never produced a result.
        if cmd is not None:
            elapsed = (datetime.now() - cmd.sent_at).total_seconds()
            if elapsed > SmartMode.WAKE_TIMEOUT:
                _LOGGER.info(
                    "Wakeup timeout %s: dir=%d limit=%dW confirmed=%s after %.0fs",
                    d.name, cmd.direction, cmd.expected_limit, cmd.confirmed, elapsed,
                )
                d.wakeup_cmd = None

        # 3. Classify current state (reads d.wakeup_cmd, no side effects).
        prev_state = d.power_flow_state
        d.power_flow_state = self.classify()

        # 4. Successful wakeup transition: WAKEUP \u2192 CHARGE or DISCHARGE.
        #    Signal hysteresis reset via wakeup_just_completed (read+cleared by _distribute_power).
        if (prev_state == PowerFlowState.WAKEUP
                and d.power_flow_state in (PowerFlowState.CHARGE, PowerFlowState.DISCHARGE)):
            d.wakeup_cmd = None
            d.wakeup_just_completed = True

        # 5. Log transitions.
        if d.power_flow_state != prev_state:
            active_cmd = d.wakeup_cmd
            cmd_info = (
                "%d/%dW%s" % (
                    active_cmd.direction,
                    active_cmd.expected_limit,
                    "\u2713" if active_cmd.confirmed else "",
                ) if active_cmd else "none"
            )
            _LOGGER.debug(
                "PowerFlow %s: %s \u2192 %s (state=%s soc=%s pack=%s ac=%s cmd=%s)",
                d.name,
                prev_state.name,
                d.power_flow_state.name,
                d.state.name,
                d.electricLevel.asInt,
                d.packState.asInt,
                d.acMode.asInt,
                cmd_info,
            )

        # 6. Sensor updates.
        d.power_flow_sensor.update_value(d.power_flow_state.value)
        d.inverterLoss.update_value(d.inverterLossPort.power)


class MqttProtocolHandler:
    """Object facade over the free functions in `mqtt_protocol`.

    The `mqtt_protocol` module pulls in `homeassistant.util.dt`, so it's
    imported lazily inside each method. That keeps this module importable
    from test environments that stub out Home Assistant.
    """

    def __init__(self, device: ZendureDevice) -> None:
        self.device = device

    def publish(self, topic: str, command: Any, client: mqtt_client.Client | None = None) -> None:
        from . import mqtt_protocol

        mqtt_protocol.mqtt_publish(self.device, topic, command, client)

    def invoke(self, command: Any) -> None:
        from . import mqtt_protocol

        mqtt_protocol.mqtt_invoke(self.device, command)

    async def properties(self, payload: Any) -> None:
        from . import mqtt_protocol

        await mqtt_protocol.mqtt_properties(self.device, payload)

    def message(self, topic: str, payload: Any) -> bool:
        from . import mqtt_protocol

        return mqtt_protocol.mqtt_message(self.device, topic, payload)

    async def entity_write(self, entity: EntityZendure, value: Any) -> None:
        from . import mqtt_protocol

        await mqtt_protocol.mqtt_entity_write(self.device, entity, value)
