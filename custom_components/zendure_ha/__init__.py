"""Initialize the Zendure component."""

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_MQTTLOG, CONF_P1METER, CONF_SIM, DOMAIN
from .device import ZendureDevice
from .manager import ZendureConfigEntry, ZendureManager

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ZendureConfigEntry) -> bool:
    """Set up Zendure as config entry."""
    manager = ZendureManager(hass, entry)
    entry.runtime_data = manager

    # WICHTIG: shutdown() stoppt die MQTT Threads (loop_stop), wenn HA neu startet
    if hasattr(manager, 'api'):
        entry.async_on_unload(manager.api.shutdown)

    # 1. ZUERST Plattformen laden (das setzt die .add, .update etc. Variablen in den Entitäten)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 2. DANN Geräte laden (jetzt können die Entitäten sich sicher selbst anmelden)
    await manager.loadDevices()

    # 3. Zuletzt den Coordinator starten
    await manager.async_config_entry_first_refresh()

    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def update_listener(_hass: HomeAssistant, entry: ZendureConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.debug("Updating Zendure config entry: %s", entry.entry_id)
    entry.runtime_data.api.mqtt_logging = entry.data.get(CONF_MQTTLOG, False)
    entry.runtime_data.simulation = entry.data.get(CONF_SIM, False)
    entry.runtime_data.update_p1meter(entry.data.get(CONF_P1METER, "sensor.power_actual"))


async def async_unload_entry(hass: HomeAssistant, entry: ZendureConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Zendure config entry: %s", entry.entry_id)
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if result:
        manager = entry.runtime_data
        if hasattr(manager, 'api'):
            manager.api.shutdown()
        manager.update_p1meter(None)
        manager.fuse_groups.clear()
        manager.devices.clear()
    return result


async def async_remove_config_entry_device(_hass: HomeAssistant, entry: ZendureConfigEntry, device_entry: dr.DeviceEntry) -> bool:
    """Remove a device from a config entry."""
    manager = entry.runtime_data

    # check for device to remove
    for d in manager.devices:
        if d.name == device_entry.name:
            manager.devices.remove(d)
            if d.deviceId in manager.api.devices:
                del manager.api.devices[d.deviceId]
            return True


        if isinstance(d, ZendureDevice) and (bat := next((b for b in d.batteries.values() if b.name == device_entry.name), None)) is not None:
            d.batteries.pop(bat.deviceId)
            return True

    return True
