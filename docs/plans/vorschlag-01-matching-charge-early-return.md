# Vorschlag 01: MATCHING_CHARGE — Early-return statt distribute_discharge(0)

## Priorität: Mittel

## Problem

In `_dispatch_to_mode` (Zeile 430-434) ruft `MATCHING_CHARGE` bei `setpoint > 0` ohne
nennenswerte Produktion `distribute_discharge(mgr, 0, time)` auf:

```python
case ManagerMode.MATCHING_CHARGE | ManagerMode.STORE_SOLAR:
    if setpoint > 0 and mgr.produced > SmartMode.POWER_START and ...:
        await distribute_discharge(mgr, min(mgr.produced, setpoint), time)
    elif setpoint > 0:
        await distribute_discharge(mgr, 0, time)   # <-- Problem
    else:
        await distribute_charge(mgr, min(0, setpoint), time)
```

Der Aufruf mit setpoint=0 durchläuft die gesamte `_distribute_power`-Pipeline:
Hysterese, Deadband, Geräte-Iteration — alles ohne Effekt.

## Evidenz

**Log 12. April, 13:01-13:44:** 43 Minuten lang `Discharge => setpoint 0W` alle 5 Sekunden.
Kein SDK-Aufruf wird gesendet, aber die Pipeline läuft trotzdem.

Schlimmer: In der alten Code-Version (vor Deadband-Fix) konnte dieser Pfad
`power_discharge(0)` an ein CHARGE-Gerät senden und die BYPASS-Oszillation auslösen.

## Lösung (Bug #6)

```python
case ManagerMode.MATCHING_CHARGE | ManagerMode.STORE_SOLAR:
    if setpoint > 0 and mgr.produced > SmartMode.POWER_START and ...:
        await distribute_discharge(mgr, min(mgr.produced, setpoint), time)
    elif setpoint > 0:
        pass  # Kein Discharge gewünscht im MATCHING_CHARGE Modus
    else:
        await distribute_charge(mgr, min(0, setpoint), time)
```

Alternativ den gesamten `elif`-Zweig entfernen, da `pass` keine Aktion hat.

## Betroffene Dateien

| Datei | Zeile |
|-------|-------|
| `power_strategy.py` | 433-434 |

## Risiko

Gering. Der Aufruf hat aktuell dank Deadband-Filter keinen Effekt. Das Entfernen spart
nur unnötige Funktionsaufrufe.
