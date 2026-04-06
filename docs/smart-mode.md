# Smart Mode Deep Dive

This document explains how Smart Mode calculates and distributes power. It's for users who want to understand the algorithm and developers who work on power management.

## Table of Contents

- [Overview](#overview)
- [P1 Meter Input & Smoothing](#p1-meter-input--smoothing)
- [Device Classification](#device-classification)
- [Setpoint Calculation](#setpoint-calculation)
- [Power Distribution](#power-distribution)
- [Fuse Group Limiting](#fuse-group-limiting)
- [Hysteresis & Debouncing](#hysteresis--debouncing)
- [State Machine & Mode Transitions](#state-machine--mode-transitions)
- [Troubleshooting Smart Mode](#troubleshooting-smart-mode)

---

## Overview

Smart Mode automates energy management by:

1. **Reading** a P1 meter (grid import/export)
2. **Classifying** each device (charging, discharging, idle)
3. **Calculating** a global setpoint (total power to request)
4. **Distributing** that setpoint to devices (weighted by capacity)
5. **Respecting** safety limits (min SOC, fuse groups)

The cycle repeats ~every 5 seconds (when P1 meter reports new data).

---

## P1 Meter Input & Smoothing

### What is P1?

The P1 meter measures real-time grid power flow:
- **P1 > 0**: House is importing (drawing from grid)
- **P1 < 0**: House is exporting (feeding to grid)
- **P1 = 0**: Balanced (house and battery are equal)

Example:
```
P1 = +500W  →  House needs 500W, pull from battery
P1 = -200W  →  House produces surplus 200W, charge battery
```

### Smoothing Algorithm

Raw P1 data is noisy (spikes from devices turning on/off). The integration uses a **rolling average**:

```python
# manager.py: p1_update()
p1_history = deque([...last 8 values...], maxlen=8)

# Calculate rolling average
if len(p1_history) > 1:
    avg = sum(p1_history) / len(p1_history)
else:
    avg = p1  # Not enough history yet
    
# Detect fast changes (outliers)
stddev = STDDEV_FACTOR * sqrt(variance)
isFast = abs(p1 - avg) > stddev or abs(p1 - p1_history[0]) > stddev

# Use average, not raw value
await powerChanged(avg, isFast)  # avg passed, not p1!
```

**Why smoothing?** Prevents **ping-pong** effect where the setpoint oscillates wildly every second.

### Fast Event Detection

If a change is faster than 2× the moving standard deviation, it's marked `isFast=True`:

```python
setpoint = avg
if isFast:
    # Clear history for next smooth ramp
    p1_history.clear()
    # But don't skip hysteresis checks — safety first!
```

**isFast is used by hysteresis logic** (see below) to decide if we should wait or respond immediately.

---

## Device Classification

### Overview

Each device is classified into one of four categories, which determines its role in power distribution:

| Category | Meaning | Example |
|----------|---------|---------|
| **CHARGE** | Battery is accepting power | SOC 50%, charger ready |
| **DISCHARGE** | Battery is supplying power | SOC 80%, grid needs it |
| **IDLE** | Battery is waiting, no action | SOC 50%, P1 is 0 |
| **SOCEMPTY** | Battery critically low | SOC ≤ minSOC, wait for charge only |

### Classification Logic (power_strategy.py)

```python
def _classify_single_device(mgr, device, offgrid_power):
    """Determine device's role: CHARGE / DISCHARGE / IDLE / SOCEMPTY."""
    
    # Rule 1: SOCEMPTY with bypass (low battery + passthrough relay active)
    if device.state == SOCEMPTY and device.homeInput > 0 and offgrid_power > 0:
        mgr.socempty.append(device)
        return 0, 0  # No setpoint delta
    
    # Rule 2: SOCEMPTY idle (completely quiescent)
    if device.state == SOCEMPTY and device.homeInput == 0 and device.batteryInput == 0:
        mgr.socempty.append(device)
        return 0, 0  # Waiting for charge pulse
    
    # Rule 3: CHARGE (grid power flowing to battery)
    home = -device.homeInput + offgrid_power
    if home < 0:  # homeInput dominates → charging
        mgr.charge.append(device)
        return home, -device.homeInput  # setpoint_delta
    
    # Rule 4: DISCHARGE (battery supplying power to grid or home)
    if device.homeOutput > 0 or offgrid_power > 0:
        mgr.discharge.append(device)
        net_battery = device.homeOutput - offgrid_power
        return device.homeOutput, net_battery
    
    # Rule 5: IDLE (no activity)
    mgr.idle.append(device)
    return 0, 0
```

**Key insight**: `setpoint_delta` is the device's contribution to the global setpoint. CHARGE devices contribute negative values (request power), DISCHARGE devices contribute positive values (supply power).

### Device States

Each device has an internal state machine:

```
┌─────────┐
│ OFFLINE │  (no communication)
└────┬────┘
     │ (reconnect)
     ▼
┌─────────────┐
│ SOCEMPTY    │  SOC ≤ minSOC (battery critically low)
├─────────────┤
│ INACTIVE    │  SOC between minSOC and socSet
├─────────────┤
│ SOCFULL     │  SOC ≥ socSet (target reached, stop charging)
└─────────────┘
     │
     └─ ACTIVE (above minSOC, charging/discharging)
```

---

## Setpoint Calculation

### Formula

```
Global Setpoint = P1 (grid power) + Σ(device.setpoint_delta)
```

Example with 2 devices:

```
P1 = +500W (house drawing from grid)
Device 1 (CHARGE): setpoint_delta = -300W (accepting charge)
Device 2 (IDLE): setpoint_delta = 0W

Global Setpoint = 500 + (-300) + 0 = +200W
Interpretation: Need to discharge 200W from remaining devices
```

### Special Case: Bypass Devices

Devices with passthrough relays active return `setpoint_delta = 0` to prevent contaminating the global setpoint with grid-passthrough power.

---

## Power Distribution

### Overview

Once setpoint is calculated, the integration distributes it to devices:

```
Global Setpoint (e.g., +500W to discharge)
         │
         ├─ CHARGE devices: aggregate capacity = 2000W
         ├─ DISCHARGE devices: aggregate capacity = 5000W
         └─ IDLE devices: waiting
         │
         ▼ (Only discharge devices are active)
         
Weighted distribution across discharge devices:
- Device A (80% SOC): get 60% of 500W = 300W
- Device B (60% SOC): get 40% of 500W = 200W
         │
         ▼
Apply FuseGroup limits (if total > circuit capacity, reduce equally)
         │
         ▼
Send commands: power_discharge(300), power_discharge(200)
```

### Weight Calculation

Devices are weighted by **State of Charge (SOC)**:

```python
# Discharge: prefer higher SOC (full batteries discharge first)
weight = (device.SOC - min_SOC) / (max_SOC - min_SOC)

# Charge: prefer lower SOC (empty batteries charge first)
weight = (max_SOC - device.SOC) / (max_SOC - min_SOC)

# Clamp to [0, 1]
weight = max(0, min(1, weight))
```

**Why**: Load-balances devices and respects SOC limits.

### Clamping

Each device's final power is clamped to safe limits:

```python
final_power = max(device.min_power, min(device.max_power, calculated_power))
```

Example:
```
Device has:
  - min_power = -2000W (min discharge)
  - max_power = +2400W (max charge)
  - charge_limit = 1500W (overridden by user)

Calculated: +2200W charge
Final: clamp(2200, -2000, min(2400, 1500)) = +1500W
```

---

## Fuse Group Limiting

### The Problem

Multiple devices on one circuit breaker can't exceed the breaker's capacity:

```
Circuit: 20A @ 230V = 4600W max
Device A wants: +1500W (charge)
Device B wants: +1200W (charge)
Total requested: +2700W ✓ OK

But if Device C also wants +2000W:
Total would be: +4700W ✗ EXCEEDS LIMIT
```

### Solution: FuseGroup

```python
class FuseGroup:
    def __init__(self, name, capacity_positive, capacity_negative):
        self.name = name
        self.capacity_charge = capacity_positive
        self.capacity_discharge = capacity_negative
        self.devices = [...]  # devices in this group
```

During distribution, the integration:

1. Groups devices by FuseGroup
2. Sums requested power per group
3. If sum exceeds capacity, scales down equally:

```python
if sum_requested > group.capacity:
    scale_factor = group.capacity / sum_requested
    for device in group:
        device.final_power *= scale_factor
```

---

## Hysteresis & Debouncing

### The Problem

Without debouncing, the system oscillates: charge → P1 changes → discharge → P1 changes → repeat. This stresses hardware.

### Solution: Hysteresis Timers

```python
class SmartMode:
    HYSTERESIS_FAST_COOLDOWN = 5       # seconds, quick change
    HYSTERESIS_LONG_COOLDOWN = 300     # seconds, sustained change
    HYSTERESIS_START_FACTOR = 1.5      # wait until overload is 50% higher
```

**Logic**:

```python
if is_charge_needed() and time_since_last_discharge < HYSTERESIS_FAST_COOLDOWN:
    return  # Still in cooldown, don't flip direction yet

if is_discharge_needed() and time_since_last_charge > HYSTERESIS_LONG_COOLDOWN:
    # After 5 min of discharge, allow charge again
    proceed_with_charge()
```

**Why**: Prevents bouncing between charge and discharge 10× per minute.

---

## State Machine & Mode Transitions

### Mode Flow Diagram

```
OFF mode (device powered off)
  │ (user enables)
  ▼
MANUAL mode (user sets power manually)
  │ (user switches to smart)
  ▼
SMART mode
├─ If P1 > threshold: DISCHARGE (supply to grid)
├─ If P1 < -threshold: CHARGE (absorb surplus)
└─ Else: IDLE
```

### Transition Logic

```python
async def update_operation(self, entity: Select, operation: ManagerMode):
    """User changed operation mode."""
    
    if operation == OFF:
        # Power off all devices
        for device in self.devices:
            await device.power_off()
    
    elif operation == MANUAL:
        # Apply manual setpoint immediately
        manual_power = self.manualpower.value  # e.g., +500W
        for device in self.devices:
            if manual_power > 0:
                await device.power_discharge(manual_power)
            else:
                await device.power_charge(abs(manual_power))
    
    elif operation in [SMART, SMART_CHARGING, SMART_DISCHARGING, STORE_SOLAR]:
        # Register for P1 meter updates; Smart Mode takes over
        self.update_p1meter(self.p1_entity)
```

---

## Troubleshooting Smart Mode

### Smart Mode Not Responding

**Symptom**: You change P1 consumption, but devices don't adjust.

**Check**:
1. Is P1 meter entity configured? (`Settings → Zendure HA → Configure`)
2. Is P1 entity showing a valid numeric value? (Check in States page)
3. Are devices in a supported mode? (Not OFF)
4. Check logs for errors:
   ```yaml
   logger:
     logs:
       custom_components.zendure_ha.power_strategy: debug
   ```

### Oscillating Power (Ping-Pong Effect)

**Symptom**: Device power toggles every few seconds (500W → 0W → 500W).

**Cause**: P1 meter is noisy, hysteresis not kicking in.

**Fix**:
1. Ensure smoothing is enabled: rolling average should average noisy spikes
2. Check logs for `isFast` events — too many suggests noise, not real changes
3. Adjust P1 meter sampling if possible (some meters can average)

### Devices Not Classified Correctly

**Symptom**: Device stuck in SOCEMPTY even though SOC > minSOC.

**Check**:
1. Verify device's `state` attribute (in Home Assistant states list)
2. Check `minSOC` setting matches device's minSoc (case-sensitive in API)
3. Look for classification logs:
   ```
   Classify SolarFlow 2400 AC => SOCEMPTY (bypass): homeInput=72 offgrid=51 soc=21%
   ```

### Fuse Group Limiting Too Aggressive

**Symptom**: Devices limited to less power than expected.

**Fix**:
1. Verify FuseGroup capacity is set correctly (`Settings → Configure`)
2. Check which devices are assigned to the group
3. Monitor via entity `sensor.fuse_group_*_usage` (if available)

---

## Advanced Topics

### Why Passthrough Power Matters

Devices with hardware bypass relays (e.g., SolarFlow in passthrough mode) can:
- Accept grid power via relay (not into battery)
- Output offgrid power simultaneously
- This creates `homeInput > 0` without actual charging

**The bug**: If classified as CHARGE based on `homeInput` alone, the setpoint gets contaminated. **The fix**: Guard checks `batteryInput == 0` to detect pure passthrough.

### Hysteresis Timing

The timings (5s fast, 300s long) are empirically tuned for:
- **5s**: Allows P1 meter to settle (typical reporting period)
- **300s**: Prevents rapid mode flip-flopping due to oscillating loads

Devices with slow ramp rates (like large inverters) may need longer cooldowns.

---

## References

For more detail on specific algorithms:

- **Power cycle flow**: See [`power-cycle-flow.md`](power-cycle-flow.md)
- **Classification algorithm**: See [`power-classifications-description.md`](power-classifications-description.md)
- **Constants & tuning**: See [`const-documentation.md`](const-documentation.md)
- **SF2400 AC specifics**: See [`power-start-charging-SF2400AC.md`](power-start-charging-SF2400AC.md)

---

**Questions?** Open a [GitHub Discussion](https://github.com/zendure/zendure-ha/discussions) or check [CONTRIBUTING.md](../CONTRIBUTING.md).
