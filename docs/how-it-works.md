# How the Zendure Home Assistant Integration Works

This guide explains what the Zendure HA integration does and how it connects your Zendure devices to Home Assistant.

## Table of Contents

- [What is Zendure HA?](#what-is-zendure-ha)
- [Supported Devices](#supported-devices)
- [How Data Flows](#how-data-flows)
- [Entities Created](#entities-created)
- [Smart Mode Explained](#smart-mode-explained)
- [Fuse Groups](#fuse-groups)
- [Automation Examples](#automation-examples)

---

## What is Zendure HA?

The Zendure Home Assistant integration is a **bridge** between your Zendure power devices (batteries, solar inverters) and Home Assistant, a home automation platform. It:

- **Monitors** power flow in real-time (charge/discharge rates, battery level, grid import/export)
- **Controls** device settings remotely (charge/discharge targets, operation modes)
- **Automates** energy management (match household consumption, optimize solar usage, respond to grid signals)
- **Provides data** for energy monitoring and historical analysis

Think of it as a smart controller for your energy system that integrates with the rest of your home.

---

## Supported Devices

### Zendure Legacy Devices (MQTT + BLE)

These devices communicate via the Zendure Cloud MQTT broker and optional local Bluetooth:

- **Hyper2000** (all-in-one inverter/battery)
- **Hub1200**, **Hub2000** (battery hubs)
- **ACE1500**, **AIO2400** (compact inverters)

**Authentication**: Requires a Zendure account token (obtained via the app).

### Zendure ZenSDK Devices (MQTT + HTTP fallback)

Newer devices use the modern ZenSDK protocol and can also be discovered locally:

- **SolarFlow series**: 800 / 800 Pro / 800 Plus / 1600 AC+ / 2400 AC / 2400 AC+ / 2400 Pro
- **SuperBase V4600** (battery-only unit)
- **SuperBase V6400** (tentative support)

**Authentication**: Token (cloud MQTT) + optional local MQTT broker setup for offline operation.

---

## How Data Flows

```
┌─────────────────────────────────────────────────────────────┐
│ Zendure Device (Hyper2000, SolarFlow, etc.)                │
└────────────────────┬────────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
    MQTT Cloud   Local MQTT   HTTP/BLE
        │            │            │
        └────────────┼────────────┘
                     │
        ┌────────────▼────────────┐
        │  Home Assistant         │
        │  zendure_ha integration │
        └────────────┬────────────┘
                     │
        ┌────────────▼────────────────────────────────────┐
        │ Sensors (battery %, power W, grid import kWh)   │
        │ Selects (operation mode, charge limit)          │
        │ Numbers (manual setpoint, standby power)        │
        │ Switches (power on/off)                         │
        └─────────────────────────────────────────────────┘
```

### Communication Methods

1. **Cloud MQTT** (always available)
   - All devices report to Zendure's MQTT broker via your account token
   - Reliable but adds ~500ms latency
   - Works globally, no local network needed

2. **Local MQTT** (legacy devices only, optional)
   - Hyper2000 and Hub devices can report directly to a local MQTT broker
   - Faster response (~50ms)
   - Requires MQTT broker in your home network

3. **HTTP** (ZenSDK devices, fallback)
   - SolarFlow and SuperBase can fetch data via local HTTP if cloud is unavailable
   - Slower than MQTT but better than nothing

The integration automatically tries cloud MQTT first, then falls back to local methods if configured.

---

## Entities Created

When you add a device, the integration creates these entities (varies by device type):

### Sensors (read-only, reports data)

| Entity | Example | Purpose |
|--------|---------|---------|
| `battery_level` | 65% | Current battery state of charge |
| `battery_input_power` | 500 W | Current charging power into battery |
| `battery_output_power` | 1200 W | Current discharging power from battery |
| `pack_input_power` | 0 W | Grid power to charger (legacy devices) |
| `total_kwh` | 45.3 kWh | Total energy managed (cumulative) |

### Selects (choices)

| Entity | Options | Purpose |
|--------|---------|---------|
| `operation_mode` | OFF, MANUAL, SMART, SMART_CHARGING, SMART_DISCHARGING, STORE_SOLAR | How the device behaves |
| `min_soc` | 0-100% | Battery never discharges below this level |
| `socset` | 0-100% | Target charge level (aim to stay here when not in use) |

### Numbers (sliders/inputs)

| Entity | Range | Purpose |
|--------|-------|---------|
| `manual_power` | -2400 to +2400 W | (MANUAL mode) request specific charge/discharge |
| `output_limit` | 0-2400 W | Max power to draw from battery |
| `charge_limit` | 0-2400 W | Max power allowed into battery |

### Switches (on/off)

| Entity | Purpose |
|--------|---------|
| `power` | Turn device on/off |

*Note: Exact entities depend on device type. Check Home Assistant's Devices & Services page for your device.*

---

## Smart Mode Explained

Smart Mode is where the integration gets intelligent. Instead of manually controlling power, it:

1. **Reads** a P1 meter (grid import/export sensor)
2. **Calculates** how much power your house needs
3. **Distributes** that load across your devices
4. **Respects** battery safety limits (min SOC, max charge/discharge rates)

### How It Works (Simplified)

```
P1 Meter: "House is drawing 500W from grid"
         ↓
Smart Mode: "I need to provide 500W from battery"
         ↓
Device: "Discharging at 500W"
         ↓
P1 Meter: "House is now pulling only 100W from grid"
         ↓
Smart Mode: "Reduce to 100W discharge"
         ↓
Device: "Discharging at 100W"
```

### Operation Modes

- **OFF**: Device is powered off (no charging or discharging)
- **MANUAL**: You set the power manually via `manual_power` entity
- **SMART**: Balance charge and discharge based on P1 meter (if surplus solar, charge; if grid import, discharge)
- **SMART_CHARGING**: Only charge, never discharge
- **SMART_DISCHARGING**: Only discharge, never charge
- **STORE_SOLAR**: Charge from solar, ignore house consumption (maximize battery level)

### Requirements

Smart Mode needs:
- ✅ A P1 meter entity configured (e.g., `sensor.p1_meter_power`)
- ✅ Zendure devices in operation
- ✅ Home Assistant 2025.4 or later

If P1 is missing, Smart Mode won't work (but MANUAL mode is always available).

---

## Fuse Groups

If you have **multiple devices** or a **limited grid connection**, Fuse Groups prevent exceeding your electrical circuit limits.

### What is a Fuse Group?

A logical grouping of devices that share a circuit breaker. Example:

```
Circuit Breaker: 3×20A = 6000W total
├─ Device 1 (SolarFlow): max 2400W
├─ Device 2 (Hyper2000): max 2400W
└─ Device 3 (Hub2000): max 2400W

When all three try to charge, the breaker can only handle 6000W combined.
```

### How to Configure

In Home Assistant, go to **Settings → Devices & Services → Zendure HA → Configure**:

1. Add a Fuse Group: name it (e.g., "Main Breaker"), set limit (e.g., 6000W)
2. Assign devices to it
3. Save

Now Smart Mode respects the total limit and distributes power fairly across devices.

---

## Automation Examples

Here are some practical automations using the Zendure integration:

### Example 1: Charge During Cheap Hours

```yaml
automation:
  - alias: "Charge battery during cheap energy hours"
    trigger:
      platform: time
      at: "22:00:00"  # 10 PM = cheap hours start
    action:
      - service: select.select_option
        target:
          entity_id: select.hyper2000_operation_mode
        data:
          option: "smart_charging"

  - alias: "Return to smart mode after cheap hours"
    trigger:
      platform: time
      at: "06:00:00"  # 6 AM = cheap hours end
    action:
      - service: select.select_option
        target:
          entity_id: select.hyper2000_operation_mode
        data:
          option: "smart"
```

### Example 2: Manual Power During Peak Demand

```yaml
automation:
  - alias: "Discharge at fixed power when grid is stressed"
    trigger:
      platform: numeric_state
      entity_id: sensor.grid_frequency
      below: 49.9  # Grid stress signal (frequency drops)
    action:
      - service: select.select_option
        target:
          entity_id: select.hyper2000_operation_mode
        data:
          option: "manual"
      - service: number.set_value
        target:
          entity_id: number.hyper2000_manual_power
        data:
          value: 2000  # Discharge at 2000W to help grid
```

### Example 3: Notifications

```yaml
automation:
  - alias: "Alert when battery is low"
    trigger:
      platform: numeric_state
      entity_id: sensor.hyper2000_battery_level
      below: 10
    action:
      - service: notify.mobile_app_iphone
        data:
          title: "Battery Low"
          message: "Hyper2000 is at {{ states('sensor.hyper2000_battery_level') }}%"
```

---

## Troubleshooting

### Integration doesn't connect

1. **Check your token**: Settings → Zendure HA → Options → re-enter your token
2. **Verify device is online**: Check the device's app or LED status
3. **Check logs**: Enable debug logging (see [CONTRIBUTING.md](../CONTRIBUTING.md)) and look for errors

### Entities are unavailable

- Device may be offline (network issue, power issue)
- Token may have expired
- Check device status in the Zendure app

### Smart Mode not working

- Confirm P1 meter entity is configured: Settings → Zendure HA → Configure
- Ensure P1 entity has a valid numeric state
- Check Home Assistant logs for Smart Mode errors

### Slow response time

- If using cloud MQTT only, expect 500ms+ latency
- Set up local MQTT for faster response (~50ms)
- See `docs/development.md` for local MQTT setup

---

## More Information

- **For developers**: See [`docs/architecture.md`](architecture.md) for technical details
- **For advanced power management**: Read [`docs/smart-mode.md`](smart-mode.md)
- **To set up development environment**: See [`docs/development.md`](development.md)
- **Contributing**: See [`CONTRIBUTING.md`](../CONTRIBUTING.md)
