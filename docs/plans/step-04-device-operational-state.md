# Step 04: update_operational_state() im Device

## Kontext

Das Device soll seinen Ist-Zustand selbst bestimmen, basierend auf Port-Daten.
Die Strategy liest dann nur `d.op_state`.

## Abhängigkeiten

- Step 1 (ManagerState-Enum mit BYPASS, WAKEUP, SOCEMPTY)
- Step 2 (OffGridPowerPort mit consumption/feed_in)
- Step 3 (offgrid_power als Device-Property)

## Änderungen

**Datei:** `custom_components/zendure_ha/device.py`

### Neues Attribut + Sensor in `__init__`:

```python
self.op_state: ManagerState = ManagerState.OFF
self.opStateSensor = ZendureSensor(self, "operational_state")
```

### Neue Methode:

```python
def update_operational_state(self) -> None:
    """Bestimmt den Ist-Zustand basierend auf Power-Flow-Daten der Ports."""
    if self.state == DeviceState.OFFLINE:
        self.op_state = ManagerState.OFF
        self.opStateSensor.update_value(self.op_state.value)
        return

    if self.state == DeviceState.SOCEMPTY:
        if not self.acPort.is_charging and not self.acPort.is_discharging \
           and self.batteryPort.charge_power == 0:
            self.op_state = (ManagerState.BYPASS
                             if (self.offgridPort and self.offgridPort.consumption > 0)
                             else ManagerState.SOCEMPTY)
        elif self.acPort.is_charging:
            self.op_state = ManagerState.CHARGE  # Gerät lädt trotz SOCEMPTY (woken)
        else:
            self.op_state = ManagerState.SOCEMPTY
        self.opStateSensor.update_value(self.op_state.value)
        return

    offgrid_load = self.offgridPort.consumption if self.offgridPort else 0

    if self.acPort.grid_consumption > offgrid_load:
        self.op_state = ManagerState.CHARGE
    elif self.acPort.feed_in > 0 or offgrid_load > 0:
        self.op_state = ManagerState.DISCHARGE
    else:
        self.op_state = ManagerState.IDLE

    self.opStateSensor.update_value(self.op_state.value)
```

### Aufruf am Ende von `power_get()`:

```python
async def power_get(self) -> bool:
    # ... bestehende Battery-Readiness-Logik ...
    self.update_operational_state()
    return self.state != DeviceState.OFFLINE
```

### WAKEUP-State

Wird von der Strategy gesetzt (nicht vom Device), nachdem ein Wake-Kommando geschickt wurde.
Device überschreibt beim nächsten `power_get()` mit dem tatsächlichen Zustand.

## Verifikation

- `python -m py_compile custom_components/zendure_ha/device.py`
- SOCEMPTY + is_charging -> CHARGE (nicht BYPASS)
- SOCEMPTY + nicht laden/entladen + offgrid_load > 0 -> BYPASS
- SOCEMPTY + komplett inaktiv -> SOCEMPTY
- HA-Dashboard zeigt `operational_state` Sensor
