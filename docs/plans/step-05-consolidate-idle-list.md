# Step 05: socempty + woken_socempty entfernen, eine mgr.idle-Liste

## Kontext

Aktuell gibt es `mgr.idle`, `mgr.socempty` und `mgr.woken_socempty`. Da `d.op_state` jetzt
Auskunft über den Gerätezustand gibt, braucht die Strategy keine separaten Listen und kein
Wakeup-Tracking mehr.

## Abhängigkeiten

- Step 4 (Device hat `op_state`)

## Änderungen

### `manager.py`: Listen/Sets entfernen

- `mgr.socempty` Liste entfernen (Init + `reset_power_state()`)
- `mgr.woken_socempty` Set entfernen (Init + `reset_power_state()`)

### `power_strategy.py` — `reset_power_state()`:

- `mgr.socempty.clear()` entfernen

### `power_strategy.py` — `_wake_idle_devices()`:

Die Wake-up-Routine unterscheidet per `d.op_state` statt per Liste:

```python
async def _wake_idle_devices(mgr, dev_start, is_charge):
    needs_wake = (dev_start < 0) if is_charge else (dev_start > 0)
    if not needs_wake:
        return

    if is_charge:
        for d in sorted(mgr.idle, key=lambda d: d.electricLevel.asInt, reverse=True):
            offgrid_load = d.offgridPort.power_consumption if d.offgridPort else 0
            match d.op_state:
                case ManagerState.BYPASS | ManagerState.SOCEMPTY:
                    await d.power_charge(-SmartMode.POWER_START - offgrid_load)
                case ManagerState.IDLE if d.state != DeviceState.SOCFULL:
                    await d.power_charge(-SmartMode.POWER_START - offgrid_load)
                case _:
                    continue
            if (dev_start := dev_start - d.charge_optimal * 2) >= 0:
                break
    else:
        for d in sorted(mgr.idle, key=lambda d: d.electricLevel.asInt):
            min_soc_limit = int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
            if d.electricLevel.asInt <= min_soc_limit:
                continue
            await d.power_discharge(SmartMode.POWER_START)
            if (dev_start := dev_start - d.discharge_optimal * 2) <= 0:
                break
    mgr.hysteresis.reset_accumulator()
```

### Warum `woken_socempty` entfällt

Nach einem Wake-Kommando meldet das Device beim nächsten Zyklus:
- `op_state = CHARGE` (weil `is_charging` true wird) -> Strategy klassifiziert als CHARGE
- Oder bleibt `BYPASS`/`SOCEMPTY` -> Strategy versucht erneut zu wecken

## Verifikation

- `python -m py_compile` auf manager.py + power_strategy.py
- Kein Verweis auf `socempty` oder `woken_socempty` mehr im Code
- Wake-up-Verhalten: BYPASS-Geräte werden geweckt, reagieren im nächsten Zyklus mit CHARGE
