# Step 06: _classify_single_device auf match d.op_state umstellen

## Kontext

Die Zustandserkennung ist jetzt im Device (Step 4). Die Funktion wird vereinfacht: sie liest
`d.op_state` und sortiert in Manager-Listen.

## Abhängigkeiten

- Step 4 (Device hat `op_state`)
- Step 5 (Nur noch `mgr.idle`-Liste)

## Änderungen

**Datei:** `custom_components/zendure_ha/power_strategy.py`

### `_classify_single_device()` — neue Logik:

```python
def _classify_single_device(mgr: ZendureManager, d: ZendureDevice) -> tuple[int, int]:
    """Sortiert Device in Manager-Listen basierend auf dem Ist-Zustand."""
    offgrid_load = d.offgridPort.consumption if d.offgridPort else 0

    match d.op_state:
        case ManagerState.BYPASS | ManagerState.SOCEMPTY | ManagerState.OFF:
            mgr.idle.append(d)
            mgr.idle_lvlmax = max(mgr.idle_lvlmax, d.electricLevel.asInt)
            mgr.idle_lvlmin = min(mgr.idle_lvlmin, d.electricLevel.asInt)
            return 0, 0

        case ManagerState.CHARGE | ManagerState.WAKEUP:
            home = -d.acPort.grid_consumption + offgrid_load
            mgr.charge.append(d)
            return home, -d.acPort.grid_consumption

        case ManagerState.DISCHARGE:
            home = d.acPort.feed_in
            mgr.discharge.append(d)
            mgr.discharge_bypass -= d.pwr_produced if d.state == DeviceState.SOCFULL else 0
            mgr.discharge_produced -= d.pwr_produced
            net_battery = home - offgrid_load
            return home, (0 if home == 0 and net_battery <= 0 else home)

        case ManagerState.IDLE:
            mgr.idle.append(d)
            mgr.idle_lvlmax = max(mgr.idle_lvlmax, d.electricLevel.asInt)
            mgr.idle_lvlmin = min(mgr.idle_lvlmin, d.electricLevel.asInt
                                  if d.state != DeviceState.SOCFULL else 100)
            return 0, 0

    return 0, 0
```

### Aufräumarbeiten

- `offgrid_power`-Parameter entfällt aus Signatur
- `offgrid_map` Lookup in `_classify_devices()` entfällt
- `OffGridPowerPort` Import entfällt
- `ManagerState` Import hinzufügen

### Betroffene Stellen `pwr_offgrid` -> `offgridPort.consumption`:

| Zeile | Aktuell | Neu |
|-------|---------|-----|
| ~348 | `d.pwr_offgrid == 0` | `d.offgridPort.consumption == 0 if d.offgridPort else True` |
| ~369 | `max(0, d.pwr_offgrid)` | `d.offgridPort.consumption if d.offgridPort else 0` |
| ~532 | `max(0, d.pwr_offgrid)` | `d.offgridPort.consumption if d.offgridPort else 0` |
| ~540 | `max(0, d.pwr_offgrid)` | `d.offgridPort.consumption if d.offgridPort else 0` |

## Verifikation

- `python -m py_compile custom_components/zendure_ha/power_strategy.py`
- Classification-Ergebnisse identisch zur vorherigen Logik
- Kein Verweis auf `offgrid_map`, `offgrid_power`-Parameter, `woken_socempty`
