"""Zendure MQTT protocol: message formatting, routing, and broker callbacks."""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .api import ZendureApi
    from .device import ZendureDevice
    from .entity import EntityZendure

_LOGGER = logging.getLogger(__name__)
_PERF = logging.getLogger("custom_components.zendure_ha.power_strategy.perf")


def _perf(tag: str, **kw: object) -> None:
    if _PERF.isEnabledFor(logging.DEBUG):
        _PERF.debug("PERF %s t=%.3f %s", tag, _time.monotonic(),
                    " ".join(f"{k}={v}" for k, v in kw.items()))


# ---------------------------------------------------------------------------
#  Outgoing: publish / invoke / entity-write
# ---------------------------------------------------------------------------

def mqtt_publish(device: ZendureDevice, topic: str, command: Any, client: Any | None = None) -> None:
    """Format and publish a command via MQTT."""
    command["messageId"] = device._messageid
    command["deviceId"] = device.deviceId
    command["timestamp"] = int(datetime.now().timestamp())
    payload = json.dumps(command, default=lambda o: o.__dict__)

    if client is not None:
        client.publish(topic, payload)
    elif device.mqtt is not None:
        device.mqtt.publish(topic, payload)
    _perf("CMD_SENT", dev=device.deviceId, transport="mqtt")


def mqtt_invoke(device: ZendureDevice, command: Any) -> None:
    """Format and publish a function invoke via MQTT."""
    device._messageid += 1
    command["messageId"] = device._messageid
    command["deviceKey"] = device.deviceId
    command["timestamp"] = int(datetime.now().timestamp())
    mqtt_publish(device, device.topic_function, command)


async def mqtt_entity_write(device: ZendureDevice, entity: EntityZendure, value: Any) -> None:
    """Format and publish a property write via MQTT."""
    if entity.translation_key is None:
        _LOGGER.error("Entity %s has no translation_key, cannot write property", entity.name)
        return

    _LOGGER.info("Writing property %s %s => %s", device.name, entity.propertyName, value)
    device._messageid += 1
    payload = json.dumps(
        {
            "deviceId": device.deviceId,
            "messageId": device._messageid,
            "timestamp": int(datetime.now().timestamp()),
            "properties": {entity.propertyName: value},
        },
        default=lambda o: o.__dict__,
    )
    if device.mqtt is not None:
        device.mqtt.publish(device.topic_write, payload)


# ---------------------------------------------------------------------------
#  Incoming: property processing and topic routing
# ---------------------------------------------------------------------------

async def mqtt_properties(device: ZendureDevice, payload: Any) -> None:
    """Process incoming MQTT property updates and battery pack data."""
    from .battery import ZendureBattery

    if device.lastseen == datetime.min:
        device.lastseen = datetime.now() + timedelta(minutes=5)
        device.setStatus()
    else:
        device.lastseen = datetime.now() + timedelta(minutes=5)

    if (properties := payload.get("properties", None)) and len(properties) > 0:
        for key, value in properties.items():
            device.entityUpdate(key, value)

    # update the battery properties
    if batprops := payload.get("packData", None):
        for b in batprops:
            if (sn := b.get("sn", None)) is None:
                continue

            if (bat := device.batteries.get(sn, None)) is None:
                device.batteries[sn] = ZendureBattery(device.hass, sn, device)

            elif bat and b:
                for key, value in b.items():
                    if key != "sn":
                        bat.entityUpdate(key, value)

        # Recalculate total capacity after every packData update
        device.kWh = sum(0 if b is None else b.kWh for b in device.batteries.values())
        device.totalKwh.update_value(device.kWh)
        device.availableKwh.update_value((device.electricLevel.asNumber - device.minSoc.asNumber) / 100 * device.kWh)

    # Re-evaluate power flow state after every report so WAKEUP→CHARGE/DISCHARGE
    # transitions are picked up without waiting for the next classify cycle.
    # Must run after the entityUpdate loop above so batteryPort.power is fresh.
    device.update_power_flow_state()


def mqtt_message(device: ZendureDevice, topic: str, payload: Any) -> bool:
    """Route incoming MQTT topic to the appropriate handler."""
    try:
        match topic:
            case "properties/report":
                asyncio.run_coroutine_threadsafe(mqtt_properties(device, payload), device.hass.loop)

            case "register/replay":
                _LOGGER.info("Register replay for %s => %s", device.name, payload)
                if device.mqtt is not None:
                    device.mqtt.publish(f"iot/{device.prodkey}/{device.deviceId}/register/replay", None, 1, True)

            case "time-sync":
                return True

            case "properties/energy":
                device.hemsState.update_value(1)
                device.hemsStateUpdated = datetime.now()
                device.setStatus()
                return True

            case "event/device" | "event/error":
                return True

            case "properties/read" | "function/invoke/reply" | "properties/read/reply" | "config" | "log" | "function/invoke":
                return False

            case _:
                return False
    except Exception as err:
        _LOGGER.error(err)

    return True


def entity_update_side_effects(device: ZendureDevice, key: Any, value: Any) -> None:
    """Handle side effects of entity property changes (aggregation, limits, status)."""
    import traceback
    try:
        match key:
            case "packState":
                if value == 0:
                    device.aggrSwitchCount.update_value(1 + device.aggrSwitchCount.asNumber)
            case "outputPackPower":
                if not device.heatState.is_on:
                    device.aggrCharge.aggregate(dt_util.now(), value)
                device.aggrDischarge.aggregate(dt_util.now(), 0)
                device.batInOut.update_value(device.batteryPort.power)
            case "packInputPower":
                device.aggrCharge.aggregate(dt_util.now(), 0)
                device.aggrDischarge.aggregate(dt_util.now(), value)
                device.batInOut.update_value(device.batteryPort.power)
            case "solarInputPower":
                device.aggrSolar.aggregate(dt_util.now(), value)
            case "gridInputPower":
                device.aggrHomeInput.aggregate(dt_util.now(), value)
            case "outputHomePower":
                device.aggrHomeOut.aggregate(dt_util.now(), value)
            case "gridOffPower":
                if hasattr(device, 'offGrid'):
                    device.offGrid.update_value(value)
                if hasattr(device, 'aggrOffGrid'):
                    device.aggrOffGrid.aggregate(dt_util.now(), value)
            case "inverseMaxPower":
                device.setLimits(device.charge_limit, value)
            case "chargeLimit" | "chargeMaxLimit":
                device.setLimits(-value, device.discharge_limit)
            case "hemsState" | "socStatus":
                device.setStatus()
                if key == "socStatus" and device.socStatus.asInt == 0:
                    device.nextCalibration.update_value(dt_util.now() + timedelta(days=30))
            case "electricLevel" | "minSoc" | "socLimit":
                if device.electricLevel.asInt == 100:
                    device.nextCalibration.update_value(dt_util.now() + timedelta(days=30))
                device.availableKwh.update_value((device.electricLevel.asNumber - device.minSoc.asNumber) / 100 * device.kWh)
    except Exception as e:
        _LOGGER.error("EntityUpdate error %s %s %s!", device.name, key, e)
        _LOGGER.error(traceback.format_exc())


# ---------------------------------------------------------------------------
#  Broker callbacks (extracted from api.py)
# ---------------------------------------------------------------------------

def on_connect(api: ZendureApi, client: Any, userdata: Any, _flags: Any, rc: Any, _props: Any) -> None:
    """Handle MQTT broker connect."""
    _LOGGER.info("Client %s connected to MQTT broker, return code: %s", userdata, rc)
    if userdata == "zendure":
        for device in api.devices.values():
            if client == device.zendure:
                client.subscribe(f"iot/{device.prodkey}/{device.deviceId}/#")
                if api.mqtt_cloud.is_connected():
                    api.mqtt_cloud.unsubscribe(f"/{device.prodkey}/{device.deviceId}/#")
                    api.mqtt_cloud.unsubscribe(f"iot/{device.prodkey}/{device.deviceId}/#")
    else:
        for device in api.devices.values():
            client.subscribe(f"/{device.prodkey}/{device.deviceId}/#")
            client.subscribe(f"iot/{device.prodkey}/{device.deviceId}/#")


def on_disconnect(_api: ZendureApi, _client: Any, userdata: Any, _flags: Any, rc: Any, _props: Any) -> None:
    """Handle MQTT broker disconnect."""
    _LOGGER.info("Client %s disconnected from MQTT broker, return code: %s", userdata, rc)


def on_msg_cloud(api: ZendureApi, client: Any, _userdata: Any, msg: Any) -> None:
    """Handle incoming cloud MQTT messages."""
    if msg.payload is None or not msg.payload:
        return
    try:
        topics = msg.topic.split("/", 3)

        if len(topics) < 4:
            _LOGGER.warning("Invalid MQTT topic format: %s (expected 4 segments)", msg.topic)
            return

        deviceId = topics[2]

        if (device := api.devices.get(deviceId, None)) is not None:
            try:
                payload = json.loads(msg.payload.decode())
            except json.JSONDecodeError as err:
                _LOGGER.error("Failed to decode JSON from device %s: %s", deviceId, err)
                return
            except UnicodeDecodeError as err:
                _LOGGER.error("Failed to decode payload encoding from device %s: %s", deviceId, err)
                return

            if "isHA" in payload:
                return

            if api.mqtt_logging:
                safe_topic = msg.topic.replace(device.deviceId, device.name).replace(device.snNumber, "snxxx")
                _LOGGER.info("Topic: %s => %s", safe_topic, payload)

            if device.mqttMessage(topics[3], payload) and device.mqtt != client:
                device.mqtt = client
                device.setStatus()

        else:
            _LOGGER.debug("Unknown device: %s => %s", deviceId, msg.topic)

    except Exception:
        _LOGGER.exception("Unexpected error in MQTT cloud message handler")


def on_msg_local(api: ZendureApi, client: Any, _userdata: Any, msg: Any) -> None:
    """Handle incoming local MQTT messages."""
    if msg.payload is None or not msg.payload or len(api.devices) == 0:
        return
    try:
        topics = msg.topic.split("/", 3)

        if len(topics) < 4:
            _LOGGER.warning("Invalid local MQTT topic format: %s (expected 4 segments)", msg.topic)
            return

        deviceId = topics[2]

        if (device := api.devices.get(deviceId, None)) is not None:
            try:
                payload = json.loads(msg.payload.decode())
            except json.JSONDecodeError as err:
                _LOGGER.error("Failed to decode JSON from local device %s: %s", deviceId, err)
                return
            except UnicodeDecodeError as err:
                _LOGGER.error("Failed to decode local payload encoding from device %s: %s", deviceId, err)
                return

            if "isHA" in payload:
                return

            if api.mqtt_logging:
                safe_topic = msg.topic.replace(device.deviceId, device.name).replace(device.snNumber, "snxxx")
                _LOGGER.info("Local topic: %s => %s", safe_topic, payload)

            if device.mqttMessage(topics[3], payload):
                if device.mqtt != client:
                    device.mqtt = client
                    device.setStatus()

                if device.zendure is not None and device.zendure.is_connected():
                    payload["isHA"] = True
                    device.zendure.publish(msg.topic, json.dumps(payload, default=lambda o: o.__dict__))
        else:
            _LOGGER.debug("Local message from unknown device %s: %s", msg.topic, deviceId)

    except Exception:
        _LOGGER.exception("Unexpected error in MQTT local message handler")


def on_msg_device(api: ZendureApi, _client: Any, _userdata: Any, msg: Any) -> None:
    """Handle MQTT messages from device-specific broker connections."""
    if msg.payload is None or not msg.payload:
        return
    try:
        topics = msg.topic.split("/", 3)
        if len(topics) < 4:
            return

        deviceId = topics[2]

        if api.devices.get(deviceId, None) is not None and topics[0] == "iot":
            if api.mqtt_local is not None and api.mqtt_local.is_connected():
                api.mqtt_local.publish(msg.topic, msg.payload)

    except Exception as err:
        _LOGGER.error("Error in mqtt_msg_device: %s", err)
