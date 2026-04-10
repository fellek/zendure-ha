"""Zendure Integration device."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiohttp import ClientTimeout
from homeassistant.components import persistent_notification
from homeassistant.components.number import NumberMode
from homeassistant.core import HomeAssistant

from paho.mqtt import client as mqtt_client

from .binary_sensor import ZendureBinarySensor
from .bypass_relay import BypassRelay
from .button import ZendureButton
from .const import DeviceState, FuseGroupType, PowerFlowState, SmartMode
from .entity import EntityDevice, EntityZendure
from .number import ZendureNumber
from .select import ZendureRestoreSelect, ZendureSelect
from .sensor import ZendureRestoreSensor, ZendureSensor
from . import ble as ble_transport
from . import mqtt_protocol
from .battery import ZendureBattery
from .power_port import PowerPort, AcPowerPort, BatteryPowerPort, DcSolarPowerPort, OffGridPowerPort

if TYPE_CHECKING:
    from .api import ZendureApi

_LOGGER = logging.getLogger(__name__)

CONST_HEADER = {"content-type": "application/json; charset=UTF-8"}
CONST_TIMEOUT = ClientTimeout(total=4)


class ZendureDevice(EntityDevice):
    """Zendure Device class for devices integration."""

    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, model: str, definition: dict[str, str], parent: str | None = None) -> None:
        """Initialize Device."""
        from .fusegroup import FuseGroup

        """Initialize Device."""
        self.prodkey = definition["productKey"]
        super().__init__(hass, deviceId, name, model, self.prodkey, definition["snNumber"], parent)
        self.api: ZendureApi
        self.snNumber = definition["snNumber"]
        self.definition = definition
        self.fuseGrp: FuseGroup

        self.mqtt: mqtt_client.Client | None = None
        self.zendure: mqtt_client.Client | None = None
        self.ipAddress = definition.get("ip", "") if definition.get("ip", "") != "" else f"zendure-{definition['productModel'].replace(' ', '')}-{self.snNumber}.local"

        self.topic_read = f"iot/{self.prodkey}/{self.deviceId}/properties/read"
        self.topic_write = f"iot/{self.prodkey}/{self.deviceId}/properties/write"
        self.topic_function = f"iot/{self.prodkey}/{self.deviceId}/function/invoke"

        self.batteries: dict[str, ZendureBattery | None] = {}
        self.lastseen = datetime.min
        self._messageid = 0
        self.kWh = 0.0

        self.charge_limit: int = 0
        self.charge_optimal: int = 0
        self.charge_start: int = 0
        self.discharge_limit: int = 0
        self.discharge_optimal: int = 0
        self.discharge_start: int = 0
        self.maxSolar = 0
        self._has_offgrid = False
        self.pv_port_count: int = 1  # <-- NEU: Standard ist 1 PV-Port
        self.solar_inputs: list[ZendureSensor] = [] # <-- NEU: Speichert alle PV-Sensoren
        self.pwr_max: int = 0
        self.actualKwh: float = 0.0
        self.state: DeviceState = DeviceState.OFFLINE
        self.power_flow_state: PowerFlowState = PowerFlowState.OFF
        self.wake_started_at: datetime = datetime.min
        self.wakeup_entered: datetime = datetime.min

        self.create_entities()
        self.bypass = BypassRelay(self)
        self.ports: list[PowerPort] = []      # Wird von jeder Subklasse befüllt

    @property
    def is_bypassing(self) -> bool:
        """True when device MQTT 'pass' field reports bypass active (values 2 or 3)."""
        return self.bypass.is_active

    def create_entities(self) -> None:
        """Create the device entities."""
        self.limitOutput = ZendureNumber(self, "outputLimit", self.entityWrite, None, "W", "power", self.discharge_limit, 0, NumberMode.SLIDER)
        self.limitInput = ZendureNumber(self, "inputLimit", self.entityWrite, None, "W", "power", self.charge_limit, 0, NumberMode.SLIDER)
        self.minSoc = ZendureNumber(self, "minSoc", self.entityWrite, None, "%", "soc", 95, 10, NumberMode.SLIDER, 10)
        self.socSet = ZendureNumber(self, "socSet", self.entityWrite, None, "%", "soc", 95, 10, NumberMode.SLIDER, 10)
        self.socStatus = ZendureSensor(self, "socStatus", state=0)
        self.socLimit = ZendureSensor(self, "socLimit", state=0)
        self.fuseGroup = ZendureRestoreSelect(self, "fuseGroup", FuseGroupType.as_select_dict(), None)
        self.acMode = ZendureSelect(self, "acMode", {1: "input", 2: "output"}, self.entityWrite, 1)
        self.electricLevel = ZendureSensor(self, "electricLevel", None, "%", "battery", "measurement")
        self.homeInput = ZendureSensor(self, "gridInputPower", None, "W", "power", "measurement")
        self.solarInput = ZendureSensor(self, "solarInputPower", None, "W", "power", "measurement", icon="mdi:solar-panel")
        self.batteryInput = ZendureSensor(self, "outputPackPower", None, "W", "power", "measurement")
        self.batteryOutput = ZendureSensor(self, "packInputPower", None, "W", "power", "measurement")
        self.homeOutput = ZendureSensor(self, "outputHomePower", None, "W", "power", "measurement")
        self.batInOut = ZendureSensor(self, "batInOut", None, "W", "power", "measurement", 0)
        self.heatState = ZendureBinarySensor(self, "heatState")
        self.hemsState = ZendureBinarySensor(self, "hemsState")
        self.hemsStateUpdated = datetime.min
        self.availableKwh = ZendureSensor(self, "available_kwh", None, "kWh", "energy", None, 1)
        self.totalKwh = ZendureSensor(self, "total_kwh", None, "kWh", "energy", "measurement", 2)
        self.connectionStatus = ZendureSensor(self, "connectionStatus")
        self.connection: ZendureRestoreSelect
        self.bleAdapter: ZendureRestoreSelect | None = None
        self.remainingTime = ZendureSensor(self, "remainingTime", None, "h", "duration", "measurement")
        self.nextCalibration = ZendureRestoreSensor(self, "nextCalibration", None, None, "timestamp", None)

        self.aggrCharge = ZendureRestoreSensor(self, "aggrCharge", None, "kWh", "energy", "total_increasing", 2)
        self.aggrDischarge = ZendureRestoreSensor(self, "aggrDischarge", None, "kWh", "energy", "total_increasing", 2)
        self.aggrHomeInput = ZendureRestoreSensor(self, "aggrGridInputPower", None, "kWh", "energy", "total_increasing", 2)
        self.aggrHomeOut = ZendureRestoreSensor(self, "aggrOutputHome", None, "kWh", "energy", "total_increasing", 2)
        self.aggrSolar = ZendureRestoreSensor(self, "aggrSolar", None, "kWh", "energy", "total_increasing", 2)
        self.aggrSwitchCount = ZendureRestoreSensor(self, "switchCount", None, None, None, "total_increasing", 0)
        self.power_flow_sensor = ZendureSensor(self, "power_flow_state")

    # NEU: Zentrale Initialisierung der Power Ports
    def _init_power_ports(self) -> None:
        """Initialisiert Ports NUR, wenn das Gerät diese auch physisch hat."""
        self.solarPort: DcSolarPowerPort | None = None
        self.offgridPort: OffGridPowerPort | None = None

        # 0. AC Grid Port: Jedes Gerät hat eine AC-Netzverbindung
        self.acPort = AcPowerPort(self)
        self.ports.append(self.acPort)

        # 1. Battery Port: Jedes Gerät hat Batterien
        self.batteryPort = BatteryPowerPort(self)
        self.ports.append(self.batteryPort)

        # 2. DC Solar Port: Wird IGNORIERT, wenn pv_port_count == 0
        if self.pv_port_count > 0 and self.maxSolar != 0:
            solar_sensors = [self.solarInput] # Der Haupt-Sensor aus create_entities()
            for i in range(2, self.pv_port_count + 1):
                extra_sensor = ZendureSensor(self, f"solarInputPower_{i}", None, "W", "power", "measurement", icon="mdi:solar-panel")
                solar_sensors.append(extra_sensor)
            self.solarPort = DcSolarPowerPort(self, solar_sensors)
            self.ports.append(self.solarPort)

        # 2. Offgrid Port: Wird IGNORIERT, wenn _has_offgrid == False
        if self._has_offgrid:
            self.offGrid = ZendureSensor(self, "gridOffPower", None, "W", "power", "measurement")
            self.aggrOffGrid = ZendureRestoreSensor(self, "aggrGridOffPower", None, "kWh", "energy", "total_increasing", 2)
            self.offgridPort = OffGridPowerPort(self)
            self.ports.append(self.offgridPort)

    def setLimits(self, charge: int, discharge: int) -> None:
        """Set the device limits."""
        try:
            self.charge_limit = charge
            self.charge_optimal = charge // 4
            self.charge_start = charge // 10
            self.limitInput.update_range(0, abs(charge))

            self.discharge_limit = discharge
            self.discharge_optimal = discharge // 4
            self.discharge_start = discharge // 10
            self.limitOutput.update_range(0, discharge)
        except Exception:
            _LOGGER.error("SetLimits error %s %s %s!", self.name, charge, discharge)

    def setStatus(self) -> None:
        try:
            if self.lastseen == datetime.min:
                self.connectionStatus.update_value(0)
            elif self.socStatus.asInt == 1:
                self.connectionStatus.update_value(1)
            elif self.hemsState.is_on:
                self.connectionStatus.update_value(2)
            elif self.fuseGroup.value == 0:
                self.connectionStatus.update_value(3)
            elif self.connection.value == SmartMode.ZENSDK:
                self.connectionStatus.update_value(12)
            elif self.mqtt is not None and self.mqtt.host == self.api.local_server:
                self.connectionStatus.update_value(11)
            else:
                self.connectionStatus.update_value(10)
        except Exception:
            self.connectionStatus.update_value(0)

    def entityUpdate(self, key: Any, value: Any) -> bool:
        if key in {"remainOutTime", "remainInputTime"}:
            self.remainingTime.update_value(self.calcRemainingTime())
            return True

        changed = super().entityUpdate(key, value)
        if changed:
            mqtt_protocol.entity_update_side_effects(self, key, value)
        return changed

    def calcRemainingTime(self) -> float:
        """Calculate the remaining time."""
        level = self.electricLevel.asInt
        power = self.batteryPort.power

        if power == 0:
            return 0

        if power < 0:
            soc = self.socSet.asNumber
            return 0 if level >= soc else min(999, self.kWh * 10 / -power * (soc - level))

        soc = self.minSoc.asNumber
        return 0 if level <= soc else min(999, self.kWh * 10 / power * (level - soc))

    async def entityWrite(self, entity: EntityZendure, value: Any) -> None:
        await mqtt_protocol.mqtt_entity_write(self, entity, value)

    async def button_press(self, _key: str) -> None:
        return

    def mqttPublish(self, topic: str, command: Any, client: mqtt_client.Client | None = None) -> None:
        mqtt_protocol.mqtt_publish(self, topic, command, client)

    def mqttInvoke(self, command: Any) -> None:
        mqtt_protocol.mqtt_invoke(self, command)

    async def mqttProperties(self, payload: Any) -> None:
        await mqtt_protocol.mqtt_properties(self, payload)

    def mqttMessage(self, topic: str, payload: Any) -> bool:
        return mqtt_protocol.mqtt_message(self, topic, payload)

    async def mqttSelect(self, _select: ZendureRestoreSelect, _value: Any) -> None:
        # During restore, api is not yet assigned — skip until loadDevices() completes
        if not hasattr(self, "api"):
            _LOGGER.debug("mqttSelect %s skipped: api not yet initialized (restore)", self.name)
            return

        self.mqtt = None
        if self.lastseen != datetime.min:
            if self.connection.value == 0:
                await self.bleMqtt(self.api.mqtt_cloud)
            elif self.connection.value == 1:
                await self.bleMqtt(self.api.mqtt_local)

        _LOGGER.debug("Mqtt selected %s", self.name)

    @property
    def bleMac(self) -> str | None:
        return ble_transport.ble_mac(self)

    async def bleMqtt(self, mqtt: mqtt_client.Client) -> bool:
        """Set the MQTT server for the device via BLE."""
        return await ble_transport.ble_mqtt(self, mqtt)

    async def power_get(self) -> bool:
        if self.lastseen < datetime.now():
            self.lastseen = datetime.min
            self.setStatus()

        self.actualKwh = self.availableKwh.asNumber

        if not self.online or self.socSet.asNumber == 0 or self.kWh == 0:
            self.state = DeviceState.OFFLINE
        elif self.socLimit.asInt == DeviceState.SOCFULL.value or self.electricLevel.asInt >= self.socSet.asNumber:
            self.state = DeviceState.SOCFULL
        elif self.socLimit.asInt == DeviceState.SOCEMPTY.value or self.electricLevel.asInt <= self.minSoc.asNumber:
            self.state = DeviceState.SOCEMPTY
        else:
            self.state = DeviceState.ACTIVE

        self.update_power_flow_state()
        return self.state != DeviceState.OFFLINE

    async def charge(self, _power: int) -> int:
        """Set the power output/input."""
        return 0

    async def power_charge(self, power: int) -> int:
        """Set charge power."""
        power = min(0, max(power, self.charge_limit))
        if power == 0 and self.state == DeviceState.SOCEMPTY and self.bypass.is_active:
            _LOGGER.debug("Power charge %s => no action [SOCEMPTY bypass hold]", self.name)
            return self.acPort.grid_consumption
        if abs(power + self.acPort.power) <= SmartMode.POWER_TOLERANCE:
            _LOGGER.info("Power charge %s => no action [power %s]", self.name, power)
            return self.acPort.grid_consumption
        return await self.charge(power)

    async def discharge(self, _power: int) -> int:
        """Set the power output/input."""
        return 0

    async def power_discharge(self, power: int) -> int:
        """Set discharge power."""
        power = max(0, min(power, self.discharge_limit))
        if abs(power - self.acPort.power) <= SmartMode.POWER_TOLERANCE:
            _LOGGER.info("Power discharge %s => no action [power %s]", self.name, power)
            return self.acPort.feed_in
        return await self.discharge(power)

    async def power_off(self) -> None:
        """Set the power off."""

    @property
    def online(self) -> bool:
        """Check if device is online."""
        return self.connectionStatus.asInt >= SmartMode.CONNECTED

    @property
    def pwr_offgrid(self) -> int:
        """Sicherer Zugriff auf Offgrid-Leistung (0 wenn nicht vorhanden)."""
        return self.offGrid.asInt if self._has_offgrid else 0

    @property
    def offgrid_power(self) -> int:
        """Offgrid netto: positiv = Verbrauch, negativ = Einspeisung."""
        return self.offgridPort.power if self.offgridPort else 0

    def update_power_flow_state(self) -> None:
        """Bestimmt den Ist-Zustand basierend auf Port-Daten."""
        # WAKEUP bleibt, bis abs(batteryPort.power) > POWER_START
        if self.power_flow_state == PowerFlowState.WAKEUP:
            if abs(self.batteryPort.power) <= SmartMode.POWER_START:
                return
            # Gerät hat geantwortet → Übergangszeitpunkt für Ramping merken
            self.wakeup_entered = datetime.now()
        prev_state = self.power_flow_state

        if self.state == DeviceState.OFFLINE:
            self.power_flow_state = PowerFlowState.OFF
            self.power_flow_sensor.update_value(self.power_flow_state.value)
            return

        if self.state == DeviceState.SOCFULL:
            self.power_flow_state = PowerFlowState.DISCHARGE if self.batteryPort.is_discharging else PowerFlowState.IDLE
        elif self.state == DeviceState.SOCEMPTY:
            self.power_flow_state = PowerFlowState.CHARGE if self.batteryPort.is_charging else PowerFlowState.IDLE
        else:  # DeviceState.ACTIVE
            if self.batteryPort.is_charging:
                self.power_flow_state = PowerFlowState.CHARGE
            elif self.batteryPort.is_discharging:
                self.power_flow_state = PowerFlowState.DISCHARGE
            else:
                self.power_flow_state = PowerFlowState.IDLE

        if self.power_flow_state != prev_state:
            _LOGGER.debug("PowerFlow %s: %s → %s (state=%s soc=%s)",
                          self.name, prev_state.name, self.power_flow_state.name,
                          self.state.name, self.electricLevel.asInt)
        self.power_flow_sensor.update_value(self.power_flow_state.value)

    @property
    def pwr_produced(self) -> int:
        """Power produced internally (negative = generation). Computed from ports."""
        solar = self.solarPort.total_raw_solar if self.solarPort else 0
        offgrid_feed = self.offgridPort.feed_in if self.offgridPort else 0
        return min(0,
                   self.batteryPort.discharge_power + self.acPort.grid_consumption
                   - self.batteryPort.charge_power - self.acPort.feed_in - solar - offgrid_feed)


class ZendureLegacy(ZendureDevice):
    """Zendure Legacy class for devices."""

    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, model: str, definition: dict[str, str], parent: str | None = None) -> None:
        """Initialize Device."""
        super().__init__(hass, deviceId, name, model, definition, parent)
        self.connection = ZendureRestoreSelect(self, "connection", {0: "cloud", 1: "local"}, self.mqttSelect, 0)
        self.mqttReset = ZendureButton(self, "mqttReset", self.button_press)
        self.bleAdapter = ZendureRestoreSelect(self, "bleAdapter", ble_transport.ble_adapter_options(self), self.bleAdapterSelect, 0)

    async def bleAdapterSelect(self, _select: ZendureRestoreSelect, _value: Any) -> None:
        # Refresh available sources whenever selection changes or is restored.
        if self.bleAdapter is not None:
            self.bleAdapter.setDict(ble_transport.ble_adapter_options(self))

    async def button_press(self, button: ZendureButton) -> None:
        match button.translation_key:
            case "mqtt_reset":
                _LOGGER.info("Resetting MQTT for %s", self.name)
                await self.bleMqtt(self.api.mqtt_cloud if self.connection.value == 0 else self.api.mqtt_local)

    async def dataRefresh(self, _update_count: int) -> None:
        """Refresh the device data."""
        if self.lastseen != datetime.min:
            self.mqttPublish(self.topic_read, {"properties": ["getAll"]}, self.mqtt)
        else:
            self.mqttPublish(self.topic_read, {"properties": ["getAll"]}, self.api.mqtt_cloud)
            self.mqttPublish(self.topic_read, {"properties": ["getAll"]}, self.api.mqtt_local)

    def mqttMessage(self, topic: str, payload: Any) -> bool:
        if topic == "register/replay":
            _LOGGER.info("Register replay for %s => %s", self.name, payload)
            return True

        return super().mqttMessage(topic, payload)


# Re-export for backward compatibility — canonical location: zendure_sdk.py
from .zendure_sdk import ZendureZenSdk as ZendureZenSdk  # noqa: F401


@dataclass
class DeviceSettings:
    device_id: str
    fuseGroup: str
    limitCharge: int
    limitDischarge: int
    maxSolar: int
    kWh: float = 0.0
    socSet: float = 100
    minSoc: float = 0
