# Vorschlag 05: Variablen und Klassen umbenennen

## Priorität: Niedrig

## Betroffene Umbenennungen

Gesammelt aus @todo-Kommentaren im Code:

### power_strategy.py

| Aktuell | Vorschlag | Stelle | Begründung |
|---------|-----------|--------|------------|
| `home` | `pwr_consumed` | Zeile ~361 | `home` ist unklar — es ist die vom Gerät gelieferte/verbrauchte Leistung, nicht das "Zuhause" |

### device.py

| Aktuell | Vorschlag | Stelle | Begründung |
|---------|-----------|--------|------------|
| `kWh` | `capacity` | Zeile ~67 | `kWh` ist eine Einheit, kein Variablenname. Meint die Gesamtkapazität |
| `actualKwh` | `actual_capacity` | Zeile ~82 | Analog zu `kWh` |
| `AcPowerPort` (acPort) | `ConnectorPowerPort` | Zeile ~140 | Gemeint ist sowohl DC- als auch AC-Ausgang. Ein DC-Ausgang ist vermutlich nur in Entladerichtung nutzbar (muss verifiziert werden) |

### power_port.py

| Aktuell | Vorschlag | Stelle | Begründung |
|---------|-----------|--------|------------|
| `GridPowerPort` | `GridSmartMeter` | Zeile ~32 | Repräsentiert den P1-Zähler, nicht generisch "Grid" |

### const.py

| Aktuell | Vorschlag | Stelle | Begründung |
|---------|-----------|--------|------------|
| Fehlende Konstante | `C_RATE_OPTIMAL` | device.py:165 | C-Rate als charge_optimum Konstante einführen |

## Umsetzungshinweise

- Alle Umbenennungen sollten in einem Commit zusammengefasst werden
- `replace_all` in IDE nutzen um alle Referenzen zu treffen
- Tests nach jeder Umbenennung laufen lassen
- `ConnectorPowerPort`-Umbenennung erfordert Prüfung ob DC-Ausgang nur entladen kann

## Risiko

Gering (rein kosmetisch), aber Merge-Konflikte mit anderen Branches möglich.
Am besten als letzten Schritt nach funktionalen Änderungen umsetzen.
