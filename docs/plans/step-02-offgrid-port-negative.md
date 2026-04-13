# Step 02: OffGridPowerPort — negative Werte zulassen

## Kontext

`OffGridPowerPort.power` klemmt negative Werte ab mit `max(0, ...)`. Negativ = Einspeisung
von externem Akku/Mikrowechselrichter. Das muss erhalten bleiben.

## Abhängigkeiten

- Keine (unabhängig von Step 1)

## Änderung

**Datei:** `custom_components/zendure_ha/power_port.py`

```python
class OffGridPowerPort(PowerPort):
    @property
    def power(self) -> int:
        """Offgrid netto: positiv = Verbrauch, negativ = Einspeisung (externer Akku/MWR)."""
        return self.device.pwr_offgrid

    @property
    def consumption(self) -> int:
        """Reine Last an Offgrid-Steckdose (>= 0)."""
        return max(0, self.device.pwr_offgrid)

    @property
    def feed_in(self) -> int:
        """Einspeisung über Offgrid (>= 0). Externer Akku oder Mikrowechselrichter."""
        return max(0, -self.device.pwr_offgrid)
```

## Offgrid-Einspeisung: Auswirkung auf Gerätezustand

| Szenario | Was passiert | Zustand |
|----------|-------------|---------|
| feed_in < Eigenverbrauch Device | Offgrid deckt Teil, Rest aus Batterie/Grid | DISCHARGE oder CHARGE |
| feed_in > Eigenverbrauch, Akku nicht voll | Überschuss lädt Akku | CHARGE |
| feed_in > Eigenverbrauch, Akku SOCFULL | Überschuss fließt ins Netz | **DISCHARGE (Netzeinspeisung!)** |

**Problem Einspeisen=verboten:** Bei SOCFULL + Offgrid-Einspeisung ist Netzeinspeisung
unvermeidbar — inkompatibel mit Null-Einspeisung. Strategy muss das als unvermeidbar bilanzieren.

## Noch nicht geändert in diesem Schritt

Die Konsumenten in `power_strategy.py` werden erst in Step 6 umgestellt. Dieser Schritt
ändert nur die Port-Klasse.

## Verifikation

- `python -m py_compile custom_components/zendure_ha/power_port.py`
- Bestehende Aufrufe von `offgridPort.power` liefern jetzt netto statt max(0,...) —
  Downstream-Code muss ggf. angepasst werden (Steps 3-7)
