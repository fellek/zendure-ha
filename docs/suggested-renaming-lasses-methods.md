# Naming-Review: Klassen, Methoden und Verbesserungsvorschlaege
  Die häufigsten Probleme im Überblick:

  1. ~30 Stellen mit camelCase statt snake_case (PEP8): entityUpdate, mqttPublish, setLimits, loadDevices, powerChanged, httpGet, asNumber, etc.
  2. ~15 async-Methoden ohne async_ Präfix (HA-Konvention): dataRefresh, entityWrite, mqttSelect, loadDevices, powerChanged, writeSimulation, etc.
  3. ~8 Abkürzungen statt volle Wörter: pwr → power, lvl → soc, grp → group, Calc → Calculated
  4. Invertierte Namenskonvention: EntityZendure / EntityDevice statt ZendureEntity / ZendureDeviceBase (widerspricht HA-Standard)

## 1. `const.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `AcMode` | `AcDirection` | Es beschreibt die Richtung (INPUT/OUTPUT), nicht einen "Mode" |
| Class | `DeviceState` | ok | -- |
| Class | `ManagerMode` | ok | -- |
| Class | `ManagerState` | ok | -- |
| Class | `SmartMode` | `SmartModeConstants` oder `PowerConfig` | Ist kein Mode, sondern eine Sammlung von Konstanten/Thresholds |
| Const | `CONF_HAKEY` | `CONF_HA_SECRET` | Klarere Bedeutung |

---

## 2. `entity.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Function | `snakecase()` | `to_snake_case()` | Verb-Praefix fuer Konvertierungsfunktionen |
| Class | `EntityZendure` | `ZendureEntity` | HA-Konvention: Praefixname zuerst (`SensorEntity`, `SwitchEntity`) |
| Class | `EntityDevice` | `ZendureDeviceBase` | Verwechslungsgefahr mit HA `Entity` - ist kein Entity sondern Device-Basis |
| Method | `entityUpdate()` | `update_entity()` oder `handle_property_update()` | snake_case, PEP8 |
| Method | `entityWrite()` | `write_entity()` | snake_case |
| Method | `updateVersion()` | `update_version()` | snake_case |
| Method | `dataRefresh()` | `async_refresh_data()` | snake_case + async-Praefix |
| Property | `hasPlatform` | `has_platform` | snake_case |
| Attr | `propertyName` | `property_name` | snake_case |
| Attr | `deviceId` | `device_id` | snake_case |
| Dict | `createEntity` | `ENTITY_DEFINITIONS` | Ist ein Klassen-Dict, kein Methodenaufruf |

---

## 3. `device.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureDevice` | ok | -- |
| Class | `ZendureLegacy` | `ZendureMqttDevice` oder `ZendureLegacyDevice` | Klarere Unterscheidung zum SDK-Device |
| Class | `DeviceSettings` | ok | -- |
| Method | `create_entities()` | ok | -- |
| Method | `_init_power_ports()` | ok | -- |
| Method | `setLimits()` | `set_limits()` | snake_case |
| Method | `setStatus()` | `update_connection_status()` | snake_case + beschreibender |
| Method | `entityUpdate()` | `update_entity()` | snake_case |
| Method | `calcRemainingTime()` | `calculate_remaining_time()` | snake_case, nicht abkuerzen |
| Method | `entityWrite()` | `async_write_entity()` | snake_case + async-Praefix |
| Method | `button_press()` | ok | -- |
| Method | `mqttPublish()` | `mqtt_publish()` | snake_case |
| Method | `mqttInvoke()` | `mqtt_invoke()` | snake_case |
| Method | `mqttProperties()` | `async_handle_mqtt_properties()` | snake_case + async |
| Method | `mqttMessage()` | `handle_mqtt_message()` | snake_case |
| Method | `mqttSelect()` | `async_select_mqtt_connection()` | Beschreibender |
| Property | `bleMac` | `ble_mac` | snake_case |
| Method | `bleMqtt()` | `async_configure_ble_mqtt()` | snake_case + async + beschreibender |
| Method | `power_get()` | `async_update_power_state()` | Beschreibender - liest nicht nur, sondern setzt auch State |
| Method | `power_charge()` | ok | -- |
| Method | `power_discharge()` | ok | -- |
| Property | `pwr_offgrid` | `offgrid_power` | Ausgeschrieben |
| Attr | `pwr_max` | `max_power` oder `allocated_power` | Ausgeschrieben |
| Attr | `pwr_produced` | `produced_power` | Ausgeschrieben |
| Attr | `actualKwh` | `actual_kwh` | snake_case |
| Attr | `snNumber` | `serial_number` oder einfach `sn` | Redundant (sn = serial number) |
| Attr | `prodkey` | `product_key` | Ausgeschrieben |
| Attr | `fuseGrp` | `fuse_group` | snake_case + ausgeschrieben |
| Attr | `ipAddress` | `ip_address` | snake_case |
| Method | `bleAdapterSelect()` | `async_on_ble_adapter_changed()` | snake_case + beschreibender |
| Attr | `mqttReset` | `mqtt_reset_button` | snake_case + Typ |

---

## 4. `zendure_sdk.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureZenSdk` | `ZendureSdkDevice` oder `ZendureHttpDevice` | "Zen" redundant, Kernmerkmal ist HTTP-Transport |
| Method | `mqttSelect()` | `async_select_connection()` | snake_case |
| Method | `entityWrite()` | `async_write_entity()` | snake_case + async |
| Method | `dataRefresh()` | `async_refresh_data()` | snake_case + async |
| Method | `power_get()` | `async_update_power_state()` | Konsistent mit device.py |
| Method | `doCommand()` | `async_send_command()` | snake_case + async + beschreibender |
| Method | `httpGet()` | `async_http_get()` | snake_case + async |
| Method | `httpPost()` | `async_http_post()` | snake_case + async |
| Attr | `httpid` | `http_request_id` | Ausgeschrieben |

---

## 5. `manager.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureManager` | ok | -- |
| Type | `ZendureConfigEntry` | ok | -- |
| Method | `_ensure_mqtt_user()` | ok | -- |
| Method | `_setup_manager_entities()` | ok | -- |
| Method | `_load_single_device()` | ok | -- |
| Method | `_probe_devices_startup()` | ok | -- |
| Method | `_trigger_initial_power_distribution()` | ok | -- |
| Method | `loadDevices()` | `async_load_devices()` | snake_case + async-Praefix |
| Method | `update_fusegroups()` | `async_update_fuse_groups()` | async + Worttrennung |
| Method | `update_operation()` | `async_update_operation()` | async-Praefix |
| Method | `update_p1meter()` | `update_p1_meter()` | Worttrennung |
| Method | `writeSimulation()` | `async_write_simulation()` | snake_case + async |
| Method | `_sync_write_sim()` | `_sync_write_simulation()` | Nicht abkuerzen |
| Method | `_p1_changed()` | ok | -- |
| Method | `_reset_power_state()` | ok | -- |
| Method | `powerChanged()` | `async_on_power_changed()` | snake_case + async + Event-Praefix |
| Attr | `zero_next` | `next_update_time` | Beschreibender |
| Attr | `zero_fast` | `fast_update_cooldown` | Beschreibender |
| Attr | `check_reset` | -- (unused?) | Pruefen ob genutzt |
| Attr | `p1meterEvent` | `p1_meter_unsubscribe` | snake_case + beschreibt was es ist (Unsubscribe-Callback) |
| Attr | `p1_factor` | ok | -- |
| Attr | `discharge_bypass` | ok | -- |
| Attr | `discharge_produced` | ok | -- |
| Attr | `idle_lvlmax` | `idle_soc_max` | "lvl" ist unklar, "soc" ist Domaenenbegriff |
| Attr | `idle_lvlmin` | `idle_soc_min` | Gleich |
| Attr | `pwr_low` | `hysteresis_accumulator` | Beschreibt die tatsaechliche Funktion |
| Local | `isBleDevice()` | `is_ble_device()` | snake_case |
| Local | `isFast` | `is_fast_change` | snake_case + beschreibender |

---

## 6. `api.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureApi` | ok | -- |
| Dict | `createdevice` | `DEVICE_FACTORIES` oder `device_factory_map` | Beschreibender |
| Method | `_setup_mqtt_client()` | ok | -- |
| Method | `connect()` | `async_connect()` | async-Praefix |
| Method | `shutdown()` | ok | -- |
| Property | `mqtt_msg_device` | `on_device_message` | Beschreibender |

---

## 7. `api_auth.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Function | `api_ha()` | `async_authenticate()` oder `async_fetch_devices()` | "api_ha" sagt nichts aus |

---

## 8. `mqtt_protocol.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Function | `mqtt_publish()` | ok | -- |
| Function | `mqtt_invoke()` | ok | -- |
| Function | `mqtt_entity_write()` | `async_mqtt_write_property()` | async + beschreibender |
| Function | `mqtt_properties()` | `async_process_properties()` | async + beschreibender |
| Function | `mqtt_message()` | `route_mqtt_message()` | Verb-Praefix |
| Function | `entity_update_side_effects()` | `handle_entity_side_effects()` | Verb-Praefix |
| Function | `on_connect()` | ok | -- |
| Function | `on_disconnect()` | ok | -- |
| Function | `on_msg_cloud()` | `on_cloud_message()` | Konsistenter |
| Function | `on_msg_local()` | `on_local_message()` | Konsistenter |
| Function | `on_msg_device()` | `on_device_message()` | Konsistenter |

---

## 9. `sensor.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureSensor` | ok | -- |
| Class | `ZendureRestoreSensor` | ok | -- |
| Class | `ZendureCalcSensor` | `ZendureCalculatedSensor` | Nicht abkuerzen |
| Property | `asNumber` | `as_number` | snake_case |
| Property | `asInt` | `as_int` | snake_case |
| Method | `calculate_version()` | ok | -- |
| Attr | `lastValueUpdate` | `last_value_update` | snake_case |
| Attr | `last_value` | ok | -- |

---

## 10. `number.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureNumber` | ok | -- |
| Class | `ZendureRestoreNumber` | ok | -- |
| Property | `asNumber` | `as_number` | snake_case |
| Attr | `doupdate` | `update_on_set` | Beschreibender |
| Param | `onwrite` | `on_write` | snake_case |

---

## 11. `select.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureSelect` | ok | -- |
| Class | `ZendureRestoreSelect` | ok | -- |
| Method | `setDict()` | `set_options_from_dict()` | snake_case + beschreibender |
| Method | `setList()` | `set_options_from_list()` | snake_case + beschreibender |
| Attr | `onchanged` | `on_changed` | snake_case |

---

## 12. `switch.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureSwitch` | ok | -- |
| Attr | `_onwrite` | `_on_write` | snake_case |

---

## 13. `binary_sensor.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureBinarySensor` | ok | -- |

---

## 14. `button.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureButton` | ok | -- |

---

## 15. `config_flow.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureConfigFlow` | ok | -- |
| Class | `ZendureOptionsFlowHandler` | ok | -- |
| Class | `ZendureConnectionError` | ok | -- |

---

## 16. `migration.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `Migration` | `EntityMigration` | Spezifischer |
| Method | `check_device()` | `migrate_device_if_needed()` | Beschreibt was es wirklich tut |
| Method | `_migrate_updater()` | `_async_update_references()` | async + beschreibender |
| Local | `change_id()` | `replace_id_recursive()` | Beschreibender |
| Local | `update_file()` | ok | -- |
| Local | `_update_files()` | ok | -- |

---

## 17. `battery.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `ZendureBattery` | ok | -- |

---

## 18. `power_port.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `PowerPort` | ok | -- |
| Class | `GridSmartmeter` | ok | -- |
| Class | `OffGridPowerPort` | ok | -- |
| Class | `DcSolarPowerPort` | ok | -- |
| Property | `total_raw_solar` | `total_solar_power` | Konsistenter mit `power` Property |

---

## 19. `fusegroup.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Class | `FuseGroup` | ok | -- |
| Attr | `maxpower` | `max_power` | snake_case mit Worttrennung |
| Attr | `minpower` | `min_power` | snake_case mit Worttrennung |

---

## 20. `power_strategy.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Dataclass | `_DistDirection` | `_DistributionDirection` | Nicht abkuerzen |
| Function | `reset_power_state()` | ok | -- |
| Function | `classify_and_dispatch()` | ok | -- |
| Function | `_distribute_power()` | ok | -- |
| Function | `distribute_charge()` | `async_distribute_charge()` | async-Praefix |
| Function | `distribute_discharge()` | `async_distribute_discharge()` | async-Praefix |
| Const | `CHARGE_DIR` | ok | -- |
| Const | `DISCHARGE_DIR` | ok | -- |

---

## 21. `ble.py`

| Typ | Name | Vorschlag | Grund |
|-----|------|-----------|-------|
| Function | `ble_mac()` | ok | -- |
| Function | `_scanner_source()` | ok | -- |
| Function | `_scanner_ble_device()` | ok | -- |
| Function | `ble_sources()` | ok | -- |
| Function | `ble_device_from_source()` | ok | -- |
| Function | `ble_adapter_options()` | ok | -- |
| Function | `selected_ble_source()` | ok | -- |
| Function | `ble_mqtt()` | `async_configure_mqtt_via_ble()` | async + beschreibender |
| Function | `ble_command()` | `async_send_ble_command()` | async + beschreibender |

---

## 22. Device-Dateien (`devices/*.py`)

| Datei | Klasse | Vorschlag | Grund |
|-------|--------|-----------|-------|
| `ace1500.py` | `ACE1500` | ok | Produktname |
| `aio2400.py` | `AIO2400` | ok | Produktname |
| `hub1200.py` | `Hub1200` | ok | Produktname |
| `hub2000.py` | `Hub2000` | ok | Produktname |
| `hyper2000.py` | `Hyper2000` | ok | Produktname |
| `solarflow800.py` | `SolarFlow800` / `SolarFlow800Plus` / `SolarFlow800Pro` | ok | Produktname |
| `solarflow1600.py` | `SolarFlow1600` | ok | Produktname |
| `solarflow2400.py` | `SolarFlow2400AC` / `SolarFlow2400AC_Plus` / `SolarFlow2400Pro` | ok | Produktname |
| `superbasev4600.py` | `SuperBaseV4600` | ok | Produktname |
| `superbasev6400.py` | `SuperBaseV6400` | ok | Produktname |

Methoden in allen Device-Dateien:

| Methode | Vorschlag | Grund |
|---------|-----------|-------|
| `batteryUpdate()` (Hub1200) | `on_battery_changed()` | snake_case + Event-Praefix |
| `charge()` | ok | -- |
| `discharge()` | ok | -- |
| `power_off()` | ok | -- |

---

## Zusammenfassung der haeufigsten Probleme

1. **camelCase statt snake_case** (~30 Stellen): `entityUpdate`, `entityWrite`, `setLimits`, `setStatus`, `mqttPublish`, `mqttInvoke`, `mqttSelect`, `bleMac`, `bleMqtt`, `calcRemainingTime`, `loadDevices`, `powerChanged`, `writeSimulation`, `doCommand`, `httpGet`, `httpPost`, `setDict`, `setList`, `asNumber`, `asInt`, `hasPlatform`, `updateVersion`, `dataRefresh`, `bleAdapterSelect`, `lastValueUpdate`, `snNumber`, `prodkey`, `ipAddress`, `fuseGrp`, `pwr_max`, `pwr_produced`, `actualKwh`

2. **Fehlende `async_` Praefixe** bei async-Methoden (~15 Stellen): HA-Konvention ist `async_` fuer alle Coroutinen

3. **Abkuerzungen statt volle Woerter** (~8 Stellen): `pwr`, `lvl`, `aggr`, `Calc`, `Dist`, `sim`, `grp`

4. **`EntityZendure` / `EntityDevice` Namenskonvention**: Widerspricht HA-Standard (`ZendureEntity` / `ZendureDeviceBase`)
