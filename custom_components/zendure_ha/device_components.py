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

from .const import AcMode, DeviceState, PowerFlowState, SmartMode
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
    """Derives `power_flow_state` from packState, acMode and battery port readings.

    Entry point for callers: `classify()` \u2014 pure, no side effects, returns PowerFlowState.
    `update()` calls `classify()` and handles state-transition bookkeeping.

    Classification precedence (see docs/power-charge-discharge-transition-analysis-2026-04-22.md \u00a75):
      packState=0             \u2192 IDLE  (Standby/Bypass, always)
      packState=1, acMode=OUTPUT \u2192 IDLE  (CH\u2192DIS firmware transition; prevents redundant STOP_CHARGE)
      packState=1, acMode=INPUT  \u2192 CHARGE or IDLE
      packState=2 + guards    \u2192 DISCHARGE, WAKEUP or IDLE
      Fallback (packState absent/unknown) \u2192 power-based legacy classification
    """

    def __init__(self, device: ZendureDevice) -> None:
        self.device = device

    def classify(self) -> PowerFlowState:
        """Derive the current PowerFlowState from device signals. No side effects."""
        d = self.device

        if d.state == DeviceState.OFFLINE:
            return PowerFlowState.OFF

        pack_state = d.packState.asInt   # 0=Standby, 1=Charging, 2=Discharging
        ac_mode    = d.acMode.asInt      # AcMode.INPUT=1, AcMode.OUTPUT=2

        if pack_state == 0:
            return PowerFlowState.IDLE

        if pack_state == 1:
            if ac_mode == AcMode.OUTPUT:
                # Firmware has already switched direction; outputPackPower residual must not
                # trigger a redundant STOP_CHARGE (the root cause of the 15-20s CH\u2192DIS delay).
                return PowerFlowState.IDLE
            # acMode=INPUT: normal charging path
            if d.batteryPort.is_charging:
                return PowerFlowState.CHARGE
            return PowerFlowState.IDLE  # startup ramp, power not yet above threshold

        if pack_state == 2:
            # Guards \u00a76.1, \u00a76.2, \u00a76.5
            if d.state == DeviceState.SOCEMPTY:
                return PowerFlowState.IDLE
            # \u00a76.2: is_discharging before bypass \u2014 battery physically delivering power wins
            if d.batteryPort.is_discharging:
                if d.wake_started_at != datetime.min:
                    d.wake_started_at = datetime.min  # WAKEUP complete; clear timer
                return PowerFlowState.DISCHARGE
            if d.bypass.is_active:
                return PowerFlowState.IDLE
            if d.state == DeviceState.SOCFULL:
                return self._socfull_idle_or_wakeup()
            # SOC-Guard \u00a76.1: prevent WAKEUP-loop near minSoC
            soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= soc_limit:
                return PowerFlowState.IDLE
            # WAKEUP timeout: if firmware reports packState=2 but battery never delivers
            # power within WAKE_TIMEOUT, classify as IDLE to allow re-command.
            if d.wake_started_at == datetime.min:
                d.wake_started_at = datetime.now()
            if datetime.now() - d.wake_started_at > timedelta(seconds=SmartMode.WAKE_TIMEOUT):
                return PowerFlowState.IDLE
            return PowerFlowState.WAKEUP

        # Fallback: packState value unknown / sensor not yet populated.
        # Mirrors the pre-packState power-based logic for backward compatibility.
        if d.state == DeviceState.SOCFULL:
            if d.batteryPort.is_discharging:
                return PowerFlowState.DISCHARGE
            return self._socfull_idle_or_wakeup()
        if d.state == DeviceState.SOCEMPTY:
            return PowerFlowState.CHARGE if d.batteryPort.is_charging else PowerFlowState.IDLE
        if d.batteryPort.is_charging:
            return PowerFlowState.CHARGE
        if d.batteryPort.is_discharging:
            return PowerFlowState.DISCHARGE
        return PowerFlowState.IDLE

    def _socfull_idle_or_wakeup(self) -> PowerFlowState:
        """WAKEUP if a discharge command was recently sent (wake_started_at set), else IDLE.

        Used for SOCFULL devices that cannot discharge via the normal WAKEUP path because
        packState=2 + SOCFULL would otherwise always return IDLE, preventing the discharge
        cold-start from ever triggering a response.
        """
        d = self.device
        soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
        if (d.electricLevel.asInt > soc_limit
                and d.wake_started_at != datetime.min
                and datetime.now() - d.wake_started_at
                    <= timedelta(seconds=SmartMode.WAKE_TIMEOUT)):
            return PowerFlowState.WAKEUP
        return PowerFlowState.IDLE

    def update(self) -> None:
        """Classify and apply state transitions with bookkeeping and sensor updates."""
        d = self.device
        prev_state = d.power_flow_state
        d.power_flow_state = self.classify()

        if d.power_flow_state == PowerFlowState.WAKEUP and prev_state != PowerFlowState.WAKEUP:
            d.wakeup_entered = datetime.now()

        if d.power_flow_state != prev_state:
            _LOGGER.debug(
                "PowerFlow %s: %s \u2192 %s (state=%s soc=%s pack=%s ac=%s)",
                d.name,
                prev_state.name,
                d.power_flow_state.name,
                d.state.name,
                d.electricLevel.asInt,
                d.packState.asInt,
                d.acMode.asInt,
            )
            if prev_state == PowerFlowState.WAKEUP and d.power_flow_state in (
                PowerFlowState.CHARGE,
                PowerFlowState.DISCHARGE,
            ):
                d.wakeup_committed = True

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
