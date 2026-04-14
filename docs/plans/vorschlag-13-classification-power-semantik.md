# Vorschlag 13: `_classify_single_device()` — Semantik von `connector_power` vs. `net_battery` klären

## Priorität: Niedrig

## Problem

`_classify_single_device()` (`power_strategy.py:363-395`) enthält offene `@todo`-
Kommentare, die Design-Unsicherheit dokumentieren:

```python
case PowerFlowState.CHARGE:
    # @todo should it be d.batteryPort.power?
    connector_power = -d.connectorPort.power_consumption + offgrid_load
    ...

case PowerFlowState.DISCHARGE:
    connector_power = d.connectorPort.power_production
    ...
    # @todo should it be d.batteryPort.power?
    net_battery = connector_power - offgrid_load
```

Zusätzlich der Kommentar in `_classify_devices`:
```python
#@todo encapsulate power values:
```

Drei verwandte, aber nicht deckungsgleiche Größen werden verwendet:
- `connectorPort.power_consumption` / `power_production` — am AC-Anschluss gemessen
- `batteryPort.power` — am Batterie-DC-Anschluss gemessen
- `offgrid_load` — Verbrauch an der Offgrid-Buchse

Je nach Gerätetyp/Verlust unterscheiden sich diese Werte; welcher als
Klassifizierungs-Basis „richtig" ist, ist nicht dokumentiert.

## Warum notwendig

- **Korrektheit unklar:** Die offenen Fragen in den Kommentaren bedeuten, dass
  niemand mit Sicherheit sagen kann, ob die aktuelle Klassifizierung richtig ist.
- **Bilanz-Auswirkung:** Der Rückgabewert fließt direkt in `setpoint` und damit
  in die Leistungsverteilung. Falsche Semantik = falsche Setpoints.
- **Inverter-Verluste:** Mit `InverterLossPowerPort` existiert nun ein
  quantifizierter Verlustwert — eine Neuberechnung auf dieser Basis ist möglich.

## Warum hilfreich

- Explizite Entscheidung statt `@todo` im Code; Architektur-Entscheidung wird
  dokumentiert.
- Benannte Power-Modi erlauben einfaches Experimentieren und A/B-Vergleich.
- Offgrid-Bilanz (step-07) wird konsistenter.

## Lösungsskizze

Schritt 1: Semantik festlegen (Design-Entscheidung, keine Code-Änderung):

| Mode | Definition | Anwendungsfall |
|------|------------|----------------|
| `CONNECTOR_ONLY` | `connectorPort.power` | Matching gegen P1-Zähler (heute) |
| `NET_BATTERY` | `connectorPort.power - offgrid_load` | Bilanz gegen produzierte Energie |
| `BATTERY_PORT` | `batteryPort.power` | DC-nahe Sicht, inkl. Inverter-Verluste |

Schritt 2: Enum + benannte Funktion:

```python
class PowerCalculationMode(Enum):
    CONNECTOR_ONLY = 1
    NET_BATTERY = 2
    BATTERY_PORT = 3

def _classification_power(d: ZendureDevice, mode: PowerCalculationMode) -> int:
    """Berechnet den Klassifizierungs-Leistungswert gemäß gewählter Semantik."""
    ...
```

Schritt 3: `_classify_single_device` nutzt explizite Mode-Konstante.

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `power_strategy.py` | 343-395 |
| `const.py` | neue Enum `PowerCalculationMode` |

## Risiko

**Mittel.** Falsche Wahl ändert die Klassifizierungsgröße und damit das
Verhalten an P1-nahen Arbeitspunkten. Ohne Messung am realen System nicht
abschließend verifizierbar.

## Abwägung

- **Pain Point heute:** Offene Fragen im Code ohne Verantwortlichen.
- **Kosten Nicht-Handeln:** Gering akut, aber wächst mit jedem Bug, dessen
  Ursache „unklare Leistungs-Semantik" sein könnte.
- **Kosten des Refactorings:** Mittel — Messung + Review der Semantik.
- **Netto-Nutzen:** Mittel; vor allem Klarheit und Vertrauen in die Klassifizierung.
- **Alternative:** Nur die `@todo`s auflösen und eine Entscheidungsnotiz in
  `docs/` ablegen — ohne Code-Änderung.

## Abhängigkeiten

- Profitiert von `step-04` und `step-06` (op_state-Refactor).
- Teil-überlappend mit `step-07` (offgrid-Bilanz).
