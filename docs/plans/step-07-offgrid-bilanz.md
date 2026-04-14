# Step 07: pwr_produced + Offgrid-Einspeisung bilanzieren

## Kontext

`pwr_produced` berücksichtigt Offgrid-Einspeisung nicht. Wenn ein externer Akku/MWR über
Offgrid einspeist, ist das eine unsichtbare Energiequelle (wie DC-Solar — nicht im P1-Zähler
sichtbar), die in die Bilanz einfließen muss.

## Abhängigkeiten

- Step 2 (OffGridPowerPort mit feed_in)
- Step 3 (offgrid_power als Device-Property)
- Step 6 (_classify_single_device auf op_state)

## Änderung 1: `pwr_produced` erweitern (device.py)

```python
@property
def pwr_produced(self) -> int:
    """Intern produzierte Leistung (negativ = Erzeugung). Inkl. Solar + Offgrid-Einspeisung."""
    solar = self.solarPort.total_solar_power if self.solarPort else 0
    offgrid_feed = self.offgridPort.power_production if self.offgridPort else 0
    return min(0,
               self.batteryPort.discharge_power + self.acPort.power_consumption
               - self.batteryPort.charge_power - self.acPort.power_production
               - solar - offgrid_feed)
```

## Änderung 2: `_classify_devices()` aufräumen (power_strategy.py)

```python
async def _classify_devices(mgr: ZendureManager) -> tuple[int, float, int]:
    available_kwh: float = 0
    setpoint = mgr.grid_port.power
    power = 0

    # offgrid_map entfällt komplett

    for d in mgr.devices:
        if not await d.update_state():  # setzt d.state + d.op_state
            continue

        mgr.produced -= d.pwr_produced
        home, setpoint_delta = _classify_single_device(mgr, d)
        setpoint += setpoint_delta
        available_kwh += d.actualKwh
        power += d.offgrid_power + home + d.pwr_produced

    return setpoint, available_kwh, power
```

## Bilanz-Erklärung

- `d.offgrid_power` ist netto (positiv = Last, negativ = Einspeisung)
- Offgrid-Last erhöht Gesamtverbrauch (`power`)
- Offgrid-Einspeisung reduziert Gesamtverbrauch (`power`)
- Offgrid-Einspeisung steckt in `pwr_produced` als unsichtbare Quelle -> erhöht `mgr.produced`
- Bei SOCFULL + Offgrid-Einspeisung: unvermeidbare Netzeinspeisung wird bilanziert

## Verifikation

- `python -m py_compile` auf device.py + power_strategy.py
- Gerät ohne Offgrid: `pwr_produced` identisch zum bisherigen Wert
- Gerät mit Offgrid-Einspeisung (negativ): `pwr_produced` enthält feed_in
- `mgr.produced` steigt korrekt wenn Offgrid-Einspeisung aktiv
- Kein Verweis auf `offgrid_map`, `solar_map` mehr in power_strategy.py
