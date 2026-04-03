"""Zendure BLE (Bluetooth Low Energy) transport layer."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from bleak import BleakClient
from bleak.exc import BleakError

try:
    from bleak_retry_connector import establish_connection
except ImportError:
    establish_connection = None

from homeassistant.components import bluetooth, persistent_notification
from paho.mqtt import client as mqtt_client

if TYPE_CHECKING:
    from .device import ZendureDevice

_LOGGER = logging.getLogger(__name__)

SF_COMMAND_CHAR = "0000c304-0000-1000-8000-00805f9b34fb"


def ble_mac(device: ZendureDevice) -> str | None:
    """Return the Bluetooth MAC address for the device, if available."""
    if (conn := device.attr_device_info.get("connections", None)) is not None:
        for connection_type, mac_address in conn:
            if connection_type == "bluetooth":
                return mac_address
    return None


def _scanner_source(scanner_device: Any) -> str | None:
    """Extract scanner source identifier from a BluetoothScannerDevice-like object."""
    source = getattr(scanner_device, "source", None)
    if source:
        return str(source)

    if scanner := getattr(scanner_device, "scanner", None):
        source = getattr(scanner, "source", None)
        if source:
            return str(source)

    if service_info := getattr(scanner_device, "service_info", None):
        source = getattr(service_info, "source", None)
        if source:
            return str(source)

    return None


def _scanner_ble_device(scanner_device: Any) -> Any | None:
    """Extract BLEDevice from a BluetoothScannerDevice-like object."""
    device = getattr(scanner_device, "ble_device", None)
    if device is not None:
        return device

    device = getattr(scanner_device, "device", None)
    if device is not None:
        return device

    if service_info := getattr(scanner_device, "service_info", None):
        device = getattr(service_info, "device", None)
        if device is not None:
            return device

    return None


def ble_sources(device: ZendureDevice) -> list[str]:
    """Get available Bluetooth source identifiers from Home Assistant."""
    sources: set[str] = set()
    mac = ble_mac(device)

    # Prefer scanner sources for this specific device.
    try:
        if mac and (scanner_devices_by_address := getattr(bluetooth, "async_scanner_devices_by_address", None)):
            for scanner_device in scanner_devices_by_address(device.hass, mac, True):
                if source := _scanner_source(scanner_device):
                    sources.add(source)
    except Exception as err:
        _LOGGER.debug("Could not read bluetooth scanner sources for %s: %s", device.name, err)

    # Fallback: derive sources from all discovered connectable advertisements.
    try:
        if discovered_service_info := getattr(bluetooth, "async_discovered_service_info", None):
            for info in discovered_service_info(device.hass, True):
                if source := getattr(info, "source", None):
                    sources.add(str(source))
    except Exception as err:
        _LOGGER.debug("Could not derive bluetooth sources for %s: %s", device.name, err)

    return sorted(sources)


def ble_device_from_source(device: ZendureDevice, mac: str, source: str) -> Any | None:
    """Return a BLEDevice for an address constrained to a specific scanner source."""
    if scanner_devices_by_address := getattr(bluetooth, "async_scanner_devices_by_address", None):
        try:
            for scanner_device in scanner_devices_by_address(device.hass, mac, True):
                if _scanner_source(scanner_device) != source:
                    continue
                if dev := _scanner_ble_device(scanner_device):
                    return dev
        except Exception as err:
            _LOGGER.debug("Could not get BLE device for %s on source %s: %s", device.name, source, err)

    return None


def ble_adapter_options(device: ZendureDevice) -> dict[int, str]:
    """Build selectable BLE adapter/source options for this device."""
    options = {0: "auto"}
    for idx, source in enumerate(ble_sources(device), start=1):
        options[idx] = source
    return options


def selected_ble_source(device: ZendureDevice) -> str | None:
    """Return configured BLE source for this device or None for auto selection."""
    if device.bleAdapter is None:
        return None

    device.bleAdapter.setDict(ble_adapter_options(device))
    source = device.bleAdapter.current_option
    return None if source in (None, "", "auto") else str(source)


async def ble_mqtt(device: ZendureDevice, mqtt: mqtt_client.Client) -> bool:
    """Set the MQTT server for the device via BLE."""
    msg: str | None = None
    try:
        if device.api.wifi_psw == "" or device.api.wifi_ssid == "":
            msg = "No WiFi credentials or connections found"
            return False

        if (mac := ble_mac(device)) is None:
            msg = "No BLE MAC address available"
            return False

        # get the bluetooth device
        source = selected_ble_source(device)
        ble_dev = None
        if source is not None:
            ble_dev = ble_device_from_source(device, mac, source)

        if ble_dev is None:
            ble_dev = bluetooth.async_ble_device_from_address(device.hass, mac, True)

        if ble_dev is None:
            _LOGGER.warning("BLE device %s not found%s", mac, f" on source {source}" if source else "")
            return False

        try:
            _LOGGER.info("Set mqtt %s to %s", device.name, mqtt.host)
            if establish_connection is not None:
                client = await establish_connection(BleakClient, ble_dev, device.name)
            else:
                client = BleakClient(ble_dev)
                await client.connect()

            try:
                await ble_command(
                    client,
                    device.name,
                    {
                        "iotUrl": mqtt.host,
                        "messageId": 1002,
                        "method": "token",
                        "password": device.api.wifi_psw,
                        "ssid": device.api.wifi_ssid,
                        "timeZone": "GMT+01:00",
                        "token": "abcdefgh",
                    },
                )

                await ble_command(
                    client,
                    device.name,
                    {
                        "messageId": 1003,
                        "method": "station",
                    },
                )
            finally:
                # Ensure stale BLE sessions do not leak if command execution fails unexpectedly.
                if client.is_connected:
                    await client.disconnect()
        except TimeoutError:
            _LOGGER.warning("Timeout when trying to connect to the BLE device")
        except (AttributeError, BleakError) as err:
            _LOGGER.warning("Could not connect to %s: %s", device.name, err)
        except Exception as err:
            _LOGGER.warning("BLE error: %s", err)
        else:
            device.mqtt = mqtt
            if device.zendure is not None:
                device.zendure.loop_stop()
                device.zendure.disconnect()
                device.zendure = None

            device.mqttPublish(device.topic_read, {"properties": ["getAll"]}, device.mqtt)
            device.setStatus()

            return True
        return False

    finally:
        if msg is None: msg = f"Changing the MQTT server on {device.name} to {mqtt.host} was successful"
        else: msg = f"Error setting the MQTT server on {device.name} to {mqtt.host}, {msg}"
        persistent_notification.async_create(device.hass, (msg), "Zendure", "zendure_ha")

        _LOGGER.info("BLE update ready")


async def ble_command(client: BleakClient, name: str, command: Any) -> None:
    """Send a single BLE GATT command to the device."""
    try:
        payload = json.dumps(command, default=lambda o: o.__dict__)
        b = bytearray()
        b.extend(map(ord, payload))
        _LOGGER.info("BLE command: %s => %s", name, payload)
        await client.write_gatt_char(SF_COMMAND_CHAR, b, response=False)
    except Exception as err:
        _LOGGER.warning("BLE error: %s", err)
