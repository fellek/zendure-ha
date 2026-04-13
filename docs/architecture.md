# Architecture Overview

This document explains the code structure of the Zendure HA integration for developers and contributors.

## Table of Contents

- [High-Level Design](#high-level-design)
- [Directory Structure](#directory-structure)
- [Module Responsibilities](#module-responsibilities)
- [Device Hierarchy](#device-hierarchy)
- [Data Flow](#data-flow)
- [Key Classes & Concepts](#key-classes--concepts)
- [Entry Points for Contributors](#entry-points-for-contributors)

---

## High-Level Design

The integration follows a **layered architecture**:

```
┌─────────────────────────────────────────────────┐
│ Home Assistant Frontend / Automations            │
└──────────────────┬──────────────────────────────┘
                   │ (calls)
                   ▼
┌─────────────────────────────────────────────────┐
│ Integration Layer (config_flow, entities)       │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│ Manager (DataUpdateCoordinator)                 │
│ - Smart Mode logic                              │
│ - Power strategy & distribution                 │
│ - Entity updates                                │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│ Devices (ZendureDevice hierarchy)               │
│ - State & data parsing                          │
│ - Command execution (charge/discharge)          │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│ Communication Layer (MQTT, HTTP, BLE)           │
│ - mqtt_protocol.py (MQTT publish/subscribe)     │
│ - zendure_sdk.py (ZenSDK HTTP)                  │
│ - ble.py (Bluetooth)                            │
└─────────────────────────────────────────────────┘
```

**Key principle**: Each layer depends only on the layer below it. Higher layers don't need to know communication details.

---

## Directory Structure

```
custom_components/zendure_ha/
├── __init__.py                    # Integration setup, config_entry handling
├── manifest.json                  # HA metadata (version, dependencies, etc.)
│
├── Core Files
├── api.py                         # API client & device factory
├── api_auth.py                    # Authentication (token handling)
├── config_flow.py                 # UI configuration (wizard)
├── manager.py                     # DataUpdateCoordinator, Smart Mode logic
│
├── Device Classes
├── device.py                      # Base class: ZendureDevice, ZendureLegacy, ZendureZenSdk
├── battery.py                     # ZendureBattery base class
│
├── Device Implementations
├── devices/
│   ├── ace1500.py
│   ├── aio2400.py
│   ├── hyper2000.py
│   ├── hub1200.py, hub2000.py
│   ├── solarflow800.py, solarflow1600.py, solarflow2400.py
│   ├── superbasev4600.py, superbasev6400.py
│
├── Entity Types
├── binary_sensor.py               # Binary sensors (grid status, etc.)
├── button.py                      # Buttons (restart, reset, etc.)
├── number.py                      # Number inputs (setpoints, limits)
├── select.py                      # Select options (modes, settings)
├── sensor.py                      # Sensors (power, SOC, kWh)
├── switch.py                      # Switches (power on/off)
│
├── Communication
├── mqtt_protocol.py               # MQTT publish/subscribe, message parsing
├── zendure_sdk.py                 # ZenSDK HTTP client
├── ble.py                         # Bluetooth Low Energy
│
├── Power Management
├── power_strategy.py              # Smart Mode, power distribution algorithm
├── power_port.py                  # GridSmartmeter, OffGridPowerPort, DcSolarPowerPort
├── fusegroup.py                   # Fuse Group (circuit breaker) management
│
├── Supporting
├── entity.py                      # Base entity classes for HA
├── const.py                       # Constants (modes, states, thresholds)
├── migration.py                   # Config migration for version changes
│
└── translations/
    └── en.json                    # UI text translations
```

---

## Module Responsibilities

| Module | Responsibility |
|--------|-----------------|
| `__init__.py` | Entry point; sets up integration in HA; creates config_entry and coordinator |
| `api.py` | Device factory; determines device type from API response; creates ZendureDevice instances |
| `api_auth.py` | Token handling; Zendure account authentication |
| `config_flow.py` | Configuration UI (forms, validation) |
| `manager.py` | **Core coordinator**: polls devices, calculates Smart Mode setpoints, distributes power, updates entities |
| `device.py` | Base classes for all devices; handles state, power calculations |
| `battery.py` | Battery-specific base class |
| `devices/*.py` | Device-specific implementations (register entities, device quirks) |
| `mqtt_protocol.py` | Low-level MQTT send/receive; message parsing from Zendure |
| `zendure_sdk.py` | HTTP API client for ZenSDK devices |
| `ble.py` | Bluetooth communication (device discovery, setup) |
| `power_strategy.py` | **Smart Mode algorithm**: device classification, setpoint calculation, power distribution |
| `power_port.py` | Data models for grid/solar/offgrid power flows |
| `fusegroup.py` | Circuit breaker limits enforcement |
| `*_sensor.py, *_number.py, etc.` | Entity implementations for HA |
| `const.py` | Enums, constants (DeviceState, ManagerMode, thresholds) |

---

## Device Hierarchy

```
ZendureDevice (abstract base)
│
├─ ZendureLegacy (MQTT + BLE, older protocol)
│  ├─ Hyper2000
│  ├─ Hub1200
│  ├─ Hub2000
│  ├─ ACE1500
│  └─ AIO2400
│
└─ ZendureZenSdk (ZenSDK protocol, newer)
   ├─ ZendureBattery (base for battery-only devices)
   │  ├─ SuperBase V4600
   │  └─ SuperBase V6400
   │
   └─ SolarFlow (hybrid inverter/battery)
      ├─ SolarFlow 800
      ├─ SolarFlow 800 Pro
      ├─ SolarFlow 800 Plus
      ├─ SolarFlow 1600 AC+
      ├─ SolarFlow 2400 AC
      ├─ SolarFlow 2400 AC+
      └─ SolarFlow 2400 Pro
```

**Key differences**:
- **ZendureLegacy**: Uses MQTT cloud/local + optional BLE; older property names
- **ZendureZenSdk**: Uses ZenSDK (HTTP fallback); newer, cleaner API; supports local discovery

---

## Data Flow

### Startup

```
1. Home Assistant calls __init__.async_setup_entry()
   │
2. Creates DataUpdateCoordinator (manager.py)
   │
3. Coordinator calls api.get_devices()
   ├─ Connects to Zendure API with token
   ├─ Fetches device list
   ├─ Determines device type → creates ZendureDevice instance
   │
4. For each device:
   ├─ Sets up MQTT listeners (mqtt_protocol.py) OR HTTP polling (zendure_sdk.py)
   ├─ Registers entities (sensors, selects, etc.)
   │
5. Coordinator starts polling loop (update interval ~ 5 sec)
   └─ Calls device.power_get() to fetch latest state
```

### On P1 Meter Update (Smart Mode)

```
1. P1 meter value changes (grid import/export)
   │
2. manager.p1_update() is called by MQTT listener
   ├─ Calculates rolling average (smoothing)
   ├─ Detects fast changes (isFast flag)
   │
3. manager.powerChanged() is called
   ├─ Calls power_strategy.classify_and_dispatch()
   │
4. power_strategy classifies each device
   ├─ CHARGE: battery is charging
   ├─ DISCHARGE: battery is discharging
   ├─ IDLE: battery idle
   ├─ SOCEMPTY: battery empty (special handling)
   │
5. power_strategy calculates global setpoint
   ├─ If setpoint < 0: distribute charge across charge devices
   ├─ If setpoint > 0: distribute discharge across discharge devices
   │
6. distribute_charge() or distribute_discharge()
   ├─ Weighs each device by SOC level
   ├─ Applies fuse group limits
   ├─ Sends power_charge() / power_discharge() commands to devices
   │
7. Devices apply commands
   └─ entities are updated via manager.async_request_refresh()
```

---

## Key Classes & Concepts

### ZendureDevice (device.py)

Base class for all devices. Provides:

```python
class ZendureDevice:
    # Data attributes (updated from MQTT/API)
    state: DeviceState              # OFFLINE, SOCEMPTY, INACTIVE, SOCFULL, ACTIVE
    electricLevel: ZendureNumber    # Battery SOC (0-100%)
    batteryInput: ZendureNumber     # Current charge power (W)
    batteryOutput: ZendureNumber    # Current discharge power (W)
    homeInput: ZendureNumber        # Grid draw / charger input (W)
    homeOutput: ZendureNumber       # Power to house (W)
    
    # Methods
    async power_charge(power: int)   # Set charge setpoint
    async power_discharge(power: int) # Set discharge setpoint
    async power_off()               # Turn off device
    async power_get()               # Fetch fresh state from API/MQTT
```

### DataUpdateCoordinator (manager.py)

Coordinates polling and entity updates:

```python
class ZendureManager(DataUpdateCoordinator):
    async def _async_update_data(self):
        """Fetch data from all devices; called periodically by HA."""
        
    async def powerChanged(self, p1: int, isFast: bool, time: datetime):
        """Called when P1 meter changes; triggers power distribution."""
        
    def update_p1meter(self, p1_entity: str):
        """Register P1 meter entity; set up MQTT listener."""
```

### Power Strategy (power_strategy.py)

The brains of Smart Mode:

```python
async def classify_and_dispatch(mgr: ZendureManager, p1: int, isFast: bool, time: datetime):
    """
    Main entry point for Smart Mode.
    1. Classify each device (CHARGE/DISCHARGE/IDLE/SOCEMPTY)
    2. Calculate global setpoint
    3. Distribute power to devices
    """
    
def _classify_single_device(mgr, device, offgrid_power) -> tuple[int, int]:
    """Returns (home_power, setpoint_delta) for one device."""
    
async def distribute_charge(mgr: ZendureManager, setpoint: int, time: datetime):
    """Distribute charge setpoint across charge devices."""
    
async def distribute_discharge(mgr: ZendureManager, setpoint: int, time: datetime):
    """Distribute discharge setpoint across discharge devices."""
```

### MQTT Protocol (mqtt_protocol.py)

Handles subscribe/publish:

```python
def setup_mqtt_listener(hass, entity_path: str, callback: Callable):
    """Listen for MQTT messages on entity_path; call callback when data arrives."""
    
async def mqtt_publish_command(device_id: str, power: int, command_type: str):
    """Publish power command to device."""
```

---

## Entry Points for Contributors

### Adding a New Device Type

1. **Create a new file** in `devices/` (e.g., `devices/mynewdevice.py`)
2. **Define the class** inheriting from `ZendureZenSdk` or `ZendureLegacy`
3. **Register entities** in `async_setup_entries()` (sensors, selects, etc.)
4. **Update** `api.py` to instantiate your device based on API response
5. **Add to manifest.json** (supported devices list)
6. **Test** with `scripts/develop`

See `devices/solarflow2400.py` for a complete example.

### Fixing a Bug in Power Distribution

1. **Understand the issue**: Read `docs/power-cycle-flow.md` and `docs/power-classifications-description.md`
2. **Locate the code**: Usually in `power_strategy.py` (classification, distribution) or `manager.py` (p1_update logic)
3. **Add debug logs**: Insert `_LOGGER.debug()` calls; enable debug logging in HA
4. **Test**: Use `scripts/develop` with a test config
5. **Verify**: Check that Smart Mode still responds correctly to P1 changes

### Adding a New Entity

1. **Choose type**: `binary_sensor.py`, `sensor.py`, `number.py`, `select.py`, or `switch.py`
2. **Inherit** the appropriate Home Assistant class
3. **Register** in your device class's `async_setup_entries()`
4. **Add to manifest.json** if it's a core entity (not device-specific)

---

## Useful References

- **For detailed Smart Mode logic**: See [`smart-mode.md`](smart-mode.md)
- **For power cycle flow diagrams**: See [`power-cycle-flow.md`](power-cycle-flow.md)
- **For class dependencies**: See [`class-dependencies.svg`](class-dependencies.svg)
- **For device-specific docs**: See [`config/devices.docs.md`](../config/devices.docs.md)
- **For ZenSDK API reference**: See [`ZenSDK_Docs_merged_2026-04-05.md`](ZenSDK_Docs_merged_2026-04-05.md)

---

## Development Tips

- **Use the MQTT explorer**: Monitor device messages in real-time
  ```bash
  # If local MQTT is set up
  docker run -it --network host eclipse-mosquitto mosquitto_sub -h 127.0.0.1 -t 'BC8B7F/#' -v
  ```

- **Enable verbose logging**:
  ```yaml
  # In config/configuration.yaml
  logger:
    default: info
    logs:
      custom_components.zendure_ha: debug
  ```

- **Inspect device state**: Go to Settings → Devices & Services → Zendure HA → select your device

---

Questions? See [CONTRIBUTING.md](../CONTRIBUTING.md) or open a [GitHub Discussion](https://github.com/zendure/zendure-ha/discussions).
