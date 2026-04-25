"""Coordinator for Zendure integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time as _time
import traceback
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from math import sqrt
from typing import Any
from pathlib import Path

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.providers import homeassistant as auth_ha
from homeassistant.components import bluetooth, persistent_notification
from homeassistant.components.number import NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.loader import async_get_integration

from .api import ZendureApi
from .const import (
    CONF_AUTO_MQTT_USER,
    CONF_P1METER,
    DOMAIN,
    FuseGroupType,
    ManagerMode,
    SmartMode,
)
from .device import DeviceSettings, ZendureDevice, ZendureLegacy
from .entity import EntityDevice
from .fusegroup import FuseGroup
from .number import ZendureRestoreNumber
from . import power_strategy
from .power_strategy import HysteresisFilter
from .select import ZendureRestoreSelect, ZendureSelect
from .sensor import ZendureSensor
from .power_port import GridSmartmeter, PowerPort

SCAN_INTERVAL = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)
_PERF = logging.getLogger(__name__ + ".perf")


def _perf(tag: str, **kw: object) -> None:
    if _PERF.isEnabledFor(logging.DEBUG):
        _PERF.debug("PERF %s t=%.3f %s", tag, _time.monotonic(),
                    " ".join(f"{k}={v}" for k, v in kw.items()))


type ZendureConfigEntry = ConfigEntry[ZendureManager]

class ZendureManager(DataUpdateCoordinator[None], EntityDevice):
    """Class to regular update devices."""

    def __init__(self, hass: HomeAssistant, entry: ZendureConfigEntry) -> None:
        """Initialize Zendure Manager."""
        super().__init__(hass, _LOGGER, name="Zendure Manager", update_interval=SCAN_INTERVAL, config_entry=entry)
        EntityDevice.__init__(self, hass, "Zendure Manager", "Zendure Manager")

        self.devices: list[ZendureDevice] = []
        self.fuse_groups: list[FuseGroup] = []
        self.simulation: bool = False

        self.operation: ManagerMode = ManagerMode.OFF
        self.zero_next = datetime.min
        self.zero_fast = datetime.min
        self.check_reset = datetime.min
        self.p1meterEvent: Callable[[], None] | None = None
        self.p1_history: deque[int] = deque([25, -25], maxlen=8)
        self.grid_smartmeter = GridSmartmeter()
        self.p1_factor = 1
        self.update_count = 0

        self.charge: list[ZendureDevice] = []
        self.charge_limit = 0
        self.charge_optimal = 0
        self.charge_weight = 0

        self.discharge: list[ZendureDevice] = []
        self.discharge_bypass = 0
        self.discharge_produced = 0
        self.discharge_limit = 0
        self.discharge_optimal = 0
        self.discharge_weight = 0

        self.idle: list[ZendureDevice] = []
        self.idle_lvlmax = 0
        self.idle_lvlmin = 0
        self.produced = 0

        self.hysteresis = HysteresisFilter()

        self.device_ports: dict[str, list[PowerPort]] = {}

    # -------------------------------------------------------------------------
    # Helper Methoden für loadDevices
    # -------------------------------------------------------------------------

    async def _ensure_mqtt_user(self, device_id: str) -> None:
        """
        Stellt sicher, dass für das Gerät ein MQTT-Benutzer in Home Assistant existiert.
        """
        try:
            # Passwort-Hash generieren
            psw = hashlib.md5(device_id.encode()).hexdigest().upper()[8:24]  # noqa: S324

            # Auth-Provider holen
            provider: auth_ha.HassAuthProvider = auth_ha.async_get_provider(self.hass)

            # Credentials prüfen oder erstellen
            credentials = await provider.async_get_or_create_credentials({"username": device_id.lower()})
            user = await self.hass.auth.async_get_user_by_credentials(credentials)

            if user is None:
                # User existiert nicht -> Neu anlegen
                # local_only=True ist wichtig für technische MQTT-Accounts
                user = await self.hass.auth.async_create_user(
                    device_id,
                    group_ids=[GROUP_ID_USER],
                    local_only=True
                )
                await provider.async_add_auth(device_id.lower(), psw)
                await self.hass.auth.async_link_user(user, credentials)
                _LOGGER.info("Created new MQTT user for device: %s", device_id)
            else:
                # User existiert -> Passwort aktualisieren (falls sich Config geändert hat)
                await provider.async_change_password(device_id.lower(), psw)
                _LOGGER.debug("Updated password for existing MQTT user: %s", device_id)

        except Exception as err:
            # Wir lassen das Initialisieren des Gerätes nicht daran scheitern,
            # falls das User-Management schiefgeht.
            _LOGGER.error("Failed to manage MQTT user for %s: %s", device_id, err)

    def _setup_manager_entities(self) -> None:
        """Erstellt die Entitäten (Sensoren/Selects) für den Manager selbst."""
        self.operationmode = ZendureRestoreSelect(
            self,
            "Operation",
            {0: "off", 1: "manual", 2: "smart", 3: "smart_discharging", 4: "smart_charging", 5: "store_solar"},
            self.update_operation
        )
        self.power_flow_sensor = ZendureSensor(self, "power_flow_state")
        self.manualpower = ZendureRestoreNumber(
            self, "manual_power", None, None, "W", "power", 12000, -12000,
            NumberMode.BOX, True
        )
        self.availableKwh = ZendureSensor(self, "available_kwh", None, "kWh", "energy", None, 1)
        self.totalKwh = ZendureSensor(self, "total_kwh", None, "kWh", "energy", "measurement", 2)
        self.power = ZendureSensor(self, "power", None, "W", "power", "measurement", 0)

    async def _load_single_device(self, dev: dict) -> None:
        """Initialisiert ein einzelnes Gerät und konfiguriert es."""
        try:
            deviceId = dev.get("deviceKey")
            prodModel = dev.get("productModel")

            if deviceId is None or prodModel is None:
                return

            _LOGGER.info("Adding device: %s %s => %s", deviceId, prodModel, dev)

            # 1. Geräte-Klasse finden
            init = ZendureApi.createdevice.get(prodModel.lower().strip())
            if init is None:
                _LOGGER.info("Device %s is not supported!", prodModel)
                return

            # 2. Instanziieren
            device = init(self.hass, deviceId, dev.get("deviceName", prodModel), dev)

            # 3. Verknüpfen
            device.api = self.api
            self.api.devices[deviceId] = device
            device.discharge_start = device.discharge_limit // 10
            device.discharge_optimal = device.discharge_limit // 4

            # 4. Optional: MQTT User anlegen
            auto_mqtt = self.config_entry.data.get(CONF_AUTO_MQTT_USER, False)
            if auto_mqtt and self.api.local_server:
                await self._ensure_mqtt_user(deviceId)
            elif auto_mqtt:
                _LOGGER.debug("Skipping auto MQTT user creation for %s: Local server not configured.", deviceId)

        except Exception as e:
            _LOGGER.error("Unable to create device %s!", e)
            _LOGGER.error(traceback.format_exc())

    async def _probe_devices_startup(self) -> None:
        """Sendet Initial-Requests an Geräte um Status aufzulösen."""
        from .device import ZendureZenSdk

        for device in self.devices:
            try:
                if isinstance(device, ZendureZenSdk) and device.connection.value == SmartMode.ZENSDK:
                    result = await device.httpGet("properties/report")
                    if result:
                        await device.mqttProperties(result)
                else:
                    # Standard MQTT Probe
                    device.mqttPublish(device.topic_read, {"properties": ["getAll"]}, self.api.mqtt_cloud)
                    if self.api.mqtt_local is not None and self.api.mqtt_local.is_connected():
                        device.mqttPublish(device.topic_read, {"properties": ["getAll"]}, self.api.mqtt_local)
            except Exception as err:
                _LOGGER.debug("Startup probe failed for %s: %s", device.name, err)

    async def _trigger_initial_power_distribution(self) -> None:
        """Löst die erste Leistungsverteilung nach dem Start aus, falls nötig."""
        if self.operation == ManagerMode.OFF or not any(d.online for d in self.devices):
            return

        _LOGGER.info("Startup: triggering initial power distribution for %s", self.operation)
        try:
            self._reset_power_state()
            p1 = 0
            # Versuche P1 zu lesen
            if (entity := self.hass.states.get(self.config_entry.data.get(CONF_P1METER, ""))) is not None:
                try:
                    p1 = int(self.p1_factor * float(entity.state))
                except (ValueError, TypeError):
                    pass
            await self.powerChanged(p1, False, datetime.now())
        except Exception as err:
            _LOGGER.error("Startup power distribution failed: %s", err)
        finally:
            # Timer-Reset
            now = datetime.now()
            self.zero_fast = now + timedelta(seconds=SmartMode.TIMEFAST)
            self.zero_next = now + timedelta(seconds=SmartMode.TIMEZERO)

    # -------------------------------------------------------------------------
    # Haupt-Initialisierungsmethode
    # -------------------------------------------------------------------------

    async def loadDevices(self) -> None:
        # 1. Verbindung zur API aufbauen & Version holen
        if self.config_entry is None or \
           (data := await ZendureApi.connect(self.hass, dict(self.config_entry.data), True)) is None:
            return
        if (mqtt := data.get("mqtt")) is None:
            return

        integration = await async_get_integration(self.hass, DOMAIN)
        if integration is None:
            _LOGGER.error("Integration not found for domain: %s", DOMAIN)
            return
        self.attr_device_info["sw_version"] = integration.manifest.get("version", "unknown")

        # 2. API Client initialisieren
        self.api = ZendureApi(self.hass, self.config_entry.data, mqtt)

        # 3. Manager-Entitäten erstellen (Sensoren, Selects)
        self._setup_manager_entities()

        # 4. Geräte laden und konfigurieren
        for dev in data.get("deviceList", []):
            await self._load_single_device(dev)

        self.devices = list(self.api.devices.values())
        _LOGGER.info("Loaded %s devices", len(self.devices))

        # 5. Ports initialisieren
        self.device_ports: dict[str, list[PowerPort]] = {}
        for device in self.devices:
            if device.ports:
                self.device_ports[device.deviceId] = device.ports

        # 6. FuseGroups und P1 Meter finalisieren
        await self.update_fusegroups()
        self.update_p1meter(self.config_entry.data.get(CONF_P1METER, "sensor.power_actual"))

        # 7. Geräte anpingen (Startup Probe)
        await self._probe_devices_startup()

        await asyncio.sleep(1)  # Kurze Pause

        # 8. Initiale Leistungsverteilung triggern
        await self._trigger_initial_power_distribution()

    async def update_fusegroups(self) -> None:
        _LOGGER.info("Update fusegroups")

        # updateFuseGroup callback
        async def updateFuseGroup(_entity: ZendureRestoreSelect, _value: Any) -> None:
            await self.update_fusegroups()

        fuse_groups: dict[str, FuseGroup] = {}
        for device in self.devices:
            try:
                if device.fuseGroup.onchanged is None:
                    device.fuseGroup.onchanged = updateFuseGroup

                fgt = FuseGroupType.from_label(device.fuseGroup.state)
                if fgt is None:
                    _LOGGER.debug("Device %s has unsupported fuseGroup state: %s", device.name, device.fuseGroup.state)
                    continue
                if fgt is FuseGroupType.UNUSED:
                    if self.operation != ManagerMode.OFF:
                        await device.power_off()
                    continue
                fg = FuseGroup(device.name, fgt.maxpower, fgt.minpower)
                fg.devices.append(device)
                fuse_groups[device.deviceId] = fg
            except AttributeError as err:
                _LOGGER.error("Device %s missing fuseGroup attribute: %s", device.name, err)
            except Exception as err:
                _LOGGER.error("Unable to create fusegroup for device %s (%s): %s", device.name, device.deviceId, err, exc_info=True)

        # Update the fusegroups and select optins for each device
        for device in self.devices:
            try:
                fusegroups: dict[Any, str] = FuseGroupType.as_select_dict()
                for deviceId, fg in fuse_groups.items():
                    if deviceId != device.deviceId:
                        fusegroups[deviceId] = f"Part of {fg.name} fusegroup"
                device.fuseGroup.setDict(fusegroups)
            except AttributeError as err:
                _LOGGER.error("Device %s missing fuseGroup attribute: %s", device.name, err)
            except Exception as err:
                _LOGGER.error("Unable to update fusegroup options for device %s (%s): %s", device.name, device.deviceId, err, exc_info=True)

        # Add devices to fusegroups
        for device in self.devices:
            if fg := fuse_groups.get(device.fuseGroup.value):
                device.fuseGrp = fg
                fg.devices.append(device)
            device.setStatus()

        # check if we can split fuse groups
        self.fuse_groups.clear()
        for fg in fuse_groups.values():
            if len(fg.devices) > 1 and fg.maxpower >= sum(d.discharge_limit for d in fg.devices) and fg.minpower <= sum(d.charge_limit for d in fg.devices):
                for d in fg.devices:
                    self.fuse_groups.append(FuseGroup(d.name, d.discharge_limit, d.charge_limit, [d]))
            else:
                for d in fg.devices:
                    d.fuseGrp = fg
                self.fuse_groups.append(fg)

    async def update_operation(self, entity: ZendureSelect, _operation: Any) -> None:
        operation = ManagerMode(entity.value)
        _LOGGER.info("Update operation: %s from: %s", operation, self.operation)

        self.operation = operation

        #@todo: startup-race is not solved
        #2026-04-12 09:14:32.048 DEBUG (ImportExecutor_0) [custom_components.zendure_ha.entity] Entity empty has no device, skipping initialization.
        #2026-04-12 09:14:32.322 INFO (MainThread) [custom_components.zendure_ha.manager] Update operation: ManagerMode.MATCHING from: ManagerMode.OFF
        #2026-04-12 09:14:32.323 WARNING (MainThread) [custom_components.zendure_ha.manager] No devices online, not possible to start the operation

        # Check if devices are available (applies even without p1meterEvent during restore)
        if operation != ManagerMode.OFF and (len(self.devices) == 0 or all(not d.online for d in self.devices)):
            startup_race = len(self.devices) > 0 and all(d.lastseen == datetime.min for d in self.devices)
            if startup_race:
                # Devices haven't sent MQTT yet — normal at startup, operation will activate on first P1 event
                _LOGGER.debug("Devices not yet seen (startup), operation %s stored for next P1 event", operation)
            else:
                _LOGGER.warning("No devices online, not possible to start the operation")
                persistent_notification.async_create(self.hass, "No devices online, not possible to start the operation", "Zendure", "zendure_ha")
            return

        match self.operation:
            case ManagerMode.OFF:
                if len(self.devices) > 0:
                    for d in self.devices:
                        await d.power_off()
            case _:
                # Trigger immediate power distribution so the new mode
                # takes effect without waiting for the next P1 event.
                # (Only if p1meterEvent is registered, otherwise store mode for later)
                if self.p1meterEvent is not None:
                    try:
                        self._reset_power_state()
                        p1 = 0
                        if (state := self.hass.states.get(self.config_entry.data.get(CONF_P1METER, ""))) is not None:
                            try:
                                p1 = int(self.p1_factor * float(state.state))
                            except (ValueError, TypeError):
                                pass
                        _LOGGER.info("Operation changed to %s, triggering power distribution with p1=%s", operation, p1)
                        await self.powerChanged(p1, False, datetime.now())
                    except Exception as err:
                        _LOGGER.error("Power distribution after mode change failed: %s", err)
                    finally:
                        now = datetime.now()
                        self.zero_fast = now + timedelta(seconds=SmartMode.TIMEFAST)
                        self.zero_next = now + timedelta(seconds=SmartMode.TIMEZERO)

    async def _async_update_data(self) -> None:

        def isBleDevice(device: ZendureDevice, si: bluetooth.BluetoothServiceInfoBleak) -> bool:
            for d in si.manufacturer_data.values():
                try:
                    if d is None or len(d) <= 1:
                        continue
                    sn = d.decode("utf8")[:-1]
                    if device.snNumber.endswith(sn):
                        _LOGGER.info("Found Zendure Bluetooth device: %s", si)
                        device.attr_device_info["connections"] = {("bluetooth", str(si.address))}
                        return True
                except Exception:  # noqa: S112
                    continue
            return False

        time = datetime.now()
        kwh = 0
        for device in self.devices:
            kwh += device.kWh
            if isinstance(device, ZendureLegacy) and device.bleMac is None:
                for si in bluetooth.async_discovered_service_info(self.hass, False):
                    if isBleDevice(device, si):
                        break

            _LOGGER.debug("Update device: %s (%s)", device.name, device.deviceId)
            await device.dataRefresh(self.update_count)
            if device.hemsState.is_on and (time - device.hemsStateUpdated).total_seconds() > SmartMode.HEMSOFF_TIMEOUT:
                device.hemsState.update_value(0)
            device.setStatus()
        self.update_count += 1
        self.totalKwh.update_value(kwh)

    def update_p1meter(self, p1meter: str | None) -> None:
        """Update the P1 meter sensor."""
        _LOGGER.debug("Updating P1 meter to: %s", p1meter)
        if self.p1meterEvent:
            self.p1meterEvent()
        if p1meter:
            self.p1meterEvent = async_track_state_change_event(self.hass, [p1meter], self._p1_changed)
            if (entity := self.hass.states.get(p1meter)) is not None and entity.attributes.get("unit_of_measurement", "W") in ("kW", "kilowatt", "kilowatts"):
                self.p1_factor = 1000
        else:
            self.p1meterEvent = None

    async def writeSimulation(self, time: datetime, p1: int) -> None:
        """Write simulation data to CSV in a background thread to avoid blocking the event loop."""
        if not self.simulation:
            return

        # 1. Daten sauber sammeln (ohne self im Hintergrund)
        time_str = time.isoformat()
        rows = [f"{time_str};{p1};{self.operation.value}"]  # Enums erben von Natur aus einem String

        for d in self.devices:
            tbattery = d.batteryPort.power
            tsolar = d.solarPort.total_solar_power if d.solarPort else 0
            tpower = d.connectorPort.power
            rows.append(f";{tbattery};{tsolar};{tpower};{d.electricLevel.asInt}")
        rows.append(f";{self.manualpower.asNumber}")

        # 2. CSV-String generieren
        csv_content = "\n".join(rows) + "\n"
        # 3. Sicheres Abkoppeln: Keine self-Abhängigkeit im Hintergrund!
        await self.hass.async_add_executor_job(self._sync_write_sim, self.hass.config.path("simulation.csv"), csv_content, devices=self.devices)

    @staticmethod
    def _sync_write_sim(path: Path, content: str, devices: list[ZendureDevice] | None = None) -> None:
        """Synchronous file writer for background execution."""
        write_header = not path.exists()
        header = ""

        # Header nur generieren, wenn die Datei neu erstellt wird
        if write_header and devices:
            header = "Time;P1;Operation;Battery;Solar;Home;SetPoint;--;" + ";".join([
                f"bat;Prod;Home;{json.dumps(DeviceSettings(d.name, d.fuseGrp.name, d.charge_limit, d.discharge_limit, d.maxSolar, d.kWh, d.socSet.asNumber, d.minSoc.asNumber, default=vars))}"
                for d in devices
                ]) + "\n"

        # Datei öffnen und schreiben
        with open(path, "a") as f:
            if write_header:
                f.write(header)
            f.write(content)
                        
    async def _p1_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self.hass.is_running or (new_state := event.data["new_state"]) is None: return
        try: p1 = int(self.p1_factor * float(new_state.state))
        except ValueError: return

        _perf("P1_IN", p1=p1)
        # NEU: Zustand an Port delegieren
        self.grid_smartmeter.update_state(p1)

        # Get time & update simulation
        time = datetime.now()
        if self.simulation:
            self.writeSimulation(time, p1)

        # Check for fast delay
        if time < self.zero_fast:
            self.p1_history.append(p1)
            return

        # calculate the standard deviation and smoothed average
        avg = p1  # Fallback, falls die History noch zu kurz ist
        if len(self.p1_history) > 1:
            avg = int(sum(self.p1_history) / len(self.p1_history))
            stddev = SmartMode.P1_STDDEV_FACTOR * max(SmartMode.P1_STDDEV_MIN, sqrt(sum([pow(i - avg, 2) for i in self.p1_history]) / len(self.p1_history)))
            isFast = abs(p1 - avg) > stddev or abs(p1 - self.p1_history[0]) > stddev
            # HINWEIS: Das "self.p1_history.clear()" wurde bewusst entfernt!
            # Wenn wir die History löschen, verlieren wir den gleitenden Durchschnitt
            # und der Ping-Pong-Effekt beginnt von vorn.
            if isFast: self.p1_history.clear()
        else:
            isFast = False
        self.p1_history.append(p1)

        # check minimal time between updates
        if isFast or time > self.zero_next:
            try:
                # prevent updates during power distribution changes
                self._reset_power_state()
                # WICHTIG: Wir übergeben jetzt 'avg' (geglättet) statt 'p1' (roh)
                await self.powerChanged(avg, isFast, time)
            except Exception as err:
                _LOGGER.error("Error in power distribution: %s", err)
                _LOGGER.error(traceback.format_exc())
            time = datetime.now()
            # Vorschlag 03: slow down dispatch when nothing can change — all online
            # devices at minSoC, no solar, and current demand (avg>0) means discharge
            # is impossible. An `isFast` P1 spike still bypasses `zero_next` (line 507).
            if avg > 0 and power_strategy.all_devices_blocked_no_solar(self):
                self.zero_next = time + timedelta(seconds=SmartMode.SLOW_POLL_INTERVAL)
            else:
                self.zero_next = time + timedelta(seconds=SmartMode.TIMEZERO)
            self.zero_fast = time + timedelta(seconds=SmartMode.TIMEFAST)

    def _reset_power_state(self) -> None:
        """Reset all power distribution lists and counters before recalculating."""
        power_strategy.reset_power_state(self)

    async def powerChanged(self, p1: int, isFast: bool, time: datetime) -> None:
        """Classify devices and distribute power."""
        await power_strategy.classify_and_dispatch(self, p1, isFast, time)