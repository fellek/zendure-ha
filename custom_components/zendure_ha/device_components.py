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
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .const import DeviceState, PowerFlowState, SmartMode
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


class DevicePowerFlowStateMachine:
    """Derives `power_flow_state` from `state` and battery port readings."""

    def __init__(self, device: ZendureDevice) -> None:
        self.device = device

    def update(self) -> None:
        d = self.device

        if d.power_flow_state == PowerFlowState.WAKEUP:
            if abs(d.batteryPort.power) <= SmartMode.POWER_IDLE_OFFSET:
                return
            d.wakeup_entered = datetime.now()

        prev_state = d.power_flow_state

        if d.state == DeviceState.OFFLINE:
            d.power_flow_state = PowerFlowState.OFF
            d.power_flow_sensor.update_value(d.power_flow_state.value)
            return

        if d.state == DeviceState.SOCFULL:
            d.power_flow_state = (
                PowerFlowState.DISCHARGE if d.batteryPort.is_discharging else PowerFlowState.IDLE
            )
        elif d.state == DeviceState.SOCEMPTY:
            d.power_flow_state = (
                PowerFlowState.CHARGE if d.batteryPort.is_charging else PowerFlowState.IDLE
            )
        else:  # DeviceState.ACTIVE
            if d.batteryPort.is_charging:
                d.power_flow_state = PowerFlowState.CHARGE
            elif d.batteryPort.is_discharging:
                d.power_flow_state = PowerFlowState.DISCHARGE
            else:
                d.power_flow_state = PowerFlowState.IDLE

        if d.power_flow_state != prev_state:
            _LOGGER.debug(
                "PowerFlow %s: %s \u2192 %s (state=%s soc=%s)",
                d.name,
                prev_state.name,
                d.power_flow_state.name,
                d.state.name,
                d.electricLevel.asInt,
            )
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
