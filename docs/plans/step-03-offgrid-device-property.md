# Step 03: offgrid_power als Device-Property

## Kontext

`offgrid_power` wird in der Strategy über eine `offgrid_map` aus `mgr.device_ports` aufgelöst.
Das Device hat den `offgridPort` aber bereits direkt. Die Map ist überflüssig.

## Abhängigkeiten

- Step 2 (OffGridPowerPort liefert netto-Werte)

## Änderung

**Datei:** `custom_components/zendure_ha/device.py`

Neue Property neben `pwr_offgrid`:

```python
@property
def offgrid_power(self) -> int:
    """Offgrid netto: positiv = Verbrauch, negativ = Einspeisung."""
    return self.offgridPort.power if self.offgridPort else 0
```

**Hinweis:** `pwr_offgrid` (Roh-Sensor) bleibt bestehen — wird intern von `OffGridPowerPort`
gelesen. `offgrid_power` ist der saubere Zugriffspunkt über den Port.

## Verifikation

- `python -m py_compile custom_components/zendure_ha/device.py`
- `d.offgrid_power` liefert denselben Wert wie `offgridPort.power if offgridPort else 0`
