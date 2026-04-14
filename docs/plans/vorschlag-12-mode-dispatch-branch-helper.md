# Vorschlag 12: `_dispatch_to_mode()` — Vorzeichen-Branching als Helper

## Priorität: Niedrig

## Problem

`_dispatch_to_mode` (`power_strategy.py:419-445`) enthält dreimal das gleiche
Muster „setpoint-Vorzeichen entscheidet Charge vs. Discharge":

```python
case ManagerMode.MATCHING:
    if setpoint < 0:
        await distribute_charge(mgr, setpoint, time)
    else:
        await distribute_discharge(mgr, setpoint, time)

case ManagerMode.MATCHING_CHARGE | ManagerMode.STORE_SOLAR:
    if setpoint > 0 and mgr.produced > SmartMode.POWER_START and ...:
        await distribute_discharge(mgr, min(mgr.produced, setpoint), time)
    elif setpoint > 0:
        await distribute_discharge(mgr, 0, time)     # Ziel vorschlag-01
    else:
        await distribute_charge(mgr, min(0, setpoint), time)

case ManagerMode.MANUAL:
    if (setpoint := int(mgr.manualpower.asNumber)) > 0:
        await distribute_discharge(mgr, setpoint, time)
    else:
        await distribute_charge(mgr, setpoint, time)
```

## Warum notwendig

- **Wiederholung:** Jede Mode-Erweiterung muss das Muster erneut ausschreiben.
- **Lesbarkeit:** Die Mode-spezifische Logik verschwindet hinter dem immer
  gleichen Verzweigungs-Boilerplate.
- **Konsistenz:** Die Grenze (`< 0` vs. `> 0` vs. `!= 0`) ist in jedem Case leicht
  unterschiedlich — schwer zu verifizieren, ob das Absicht ist.

## Warum hilfreich

- Ein Helper dokumentiert die Konvention einmal („positiv = Discharge").
- Vorzeichen-bezogene Bugfixes (siehe `plan-03-manual-mode-sign.md`) werden
  zentral gepflegt.
- Mode-Handler werden kürzer und fokussierter.

## Lösungsskizze

```python
async def _branch_by_sign(
    mgr: ZendureManager,
    setpoint: int,
    time: datetime,
    *,
    charge_cap: int = 0,       # Obergrenze für Charge-Setpoint
    discharge_floor: int = 0,  # Untergrenze für Discharge-Setpoint
) -> None:
    """Routet positives setpoint → discharge, negatives → charge."""
    if setpoint > 0:
        await distribute_discharge(mgr, max(discharge_floor, setpoint), time)
    elif setpoint < 0:
        await distribute_charge(mgr, min(charge_cap, setpoint), time)
    # setpoint == 0: nichts tun (Verhalten prüfen!)
```

Dann:

```python
case ManagerMode.MATCHING:
    await _branch_by_sign(mgr, setpoint, time)

case ManagerMode.MANUAL:
    await _branch_by_sign(mgr, int(mgr.manualpower.asNumber), time)
```

(`MATCHING_CHARGE`/`STORE_SOLAR` bleibt individuell wegen der `produced`-Logik.)

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `power_strategy.py` | 419-445 |

## Risiko

**Gering.** Reine Extraktion; Verhalten prüft der Leser am Vergleich „vorher/
nachher".

## Abwägung

- **Pain Point heute:** Kleiner Wartungs-Overhead, keine akute Bug-Quelle.
- **Kosten Nicht-Handeln:** Minimal.
- **Kosten des Refactorings:** Minimal (~15 Zeilen).
- **Netto-Nutzen:** Niedrig bis mittel; vor allem Lesbarkeit.
- **Alternative:** Nur `MATCHING` und `MANUAL` vereinheitlichen, `MATCHING_CHARGE`
  komplett belassen — trennt die einfachen Fälle von dem komplizierten.

## Abhängigkeiten

- Passt gut **nach** `vorschlag-01` (MATCHING_CHARGE Early-return), damit der
  `elif setpoint > 0`-Zweig bereits entfernt ist.
- Teil-überlappend mit `plan-03-manual-mode-sign.md` (zentraler MANUAL-Einstieg).
