# Vorschlag 03: Polling-Intervall bei minSoC-Block reduzieren

## Status: Umgesetzt (2026-04-14)

Umsetzung verteilt auf drei Stellen:

- `const.py:95` — neue Konstante `SmartMode.SLOW_POLL_INTERVAL = 60`.
- `power_strategy.py:75-89` — neuer Helper `all_devices_blocked_no_solar(mgr)`:
  prüft alle **online** Geräte auf `SoC <= minSoC + DISCHARGE_SOC_BUFFER` und
  `solarPort.total_solar_power == 0`.
- `manager.py:516-523` — in `_p1_changed` wird `zero_next` auf
  `SLOW_POLL_INTERVAL` gesetzt, wenn `avg > 0` (Demand) und der Helper `True`
  liefert. Die bestehende `isFast`-Logik (`manager.py:507`) bleibt unberührt,
  d. h. plötzliche P1-Spikes umgehen den Slow-Poll weiterhin.

Abweichungen vom ursprünglichen Vorschlag:

- **`avg > 0` statt `setpoint > 0`** — der Manager hat keinen direkten
  Setpoint-Zugriff, `avg` ist der geglättete Wert, der an `powerChanged`
  übergeben wurde.
- **Online-Filter (`d.online`)** — inaktive Geräte dürfen den Slow-Poll nicht
  blockieren (wären sonst Trap für jedes offline-Gerät).

Tests: `tests/test_slow_poll.py` — 10 Fälle (leere Geräteliste, Boundary bei
`minSoC + DISCHARGE_SOC_BUFFER`, Offline-Geräte in beide Richtungen, Solar als
Blocker des Slow-Poll, gemischte Batches).

## Priorität: Niedrig

## Problem

Wenn alle Geräte durch minSoC blockiert sind und kein Solar vorhanden ist, pollt die
Strategy weiterhin alle 4-5 Sekunden (fast mode) ohne dass sich etwas ändern kann.

## Evidenz

**Log 13. April, 07:45-14:00+ (6+ Stunden):**
```
Discharge => setpoint 100W
Discharge Wakeup blocked: SolarFlow 2400 AC SoC=11% at minSoc limit
Discharge: distributing setpoint=0 across 0 devices
```

Dieses Muster wiederholt sich alle 4-5 Sekunden. Kein Hardware-Befehl wird gesendet,
keine Zustandsänderung ist möglich. Der einzige Weg aus diesem Zustand ist:
- SoC steigt (nur möglich bei Solar oder Grid-Ladung)
- minSoC wird geändert (manuell durch Nutzer)

## Lösung

Wenn alle Geräte minSoC-blockiert und kein Solar vorhanden, Polling-Intervall auf
z.B. 60 Sekunden reduzieren:

```python
# In der Hauptschleife, nach _classify_devices():
all_blocked = all(
    d.electricLevel.asInt <= int(d.minSoc.asNumber) + SmartMode.DISCHARGE_SOC_BUFFER
    for d in mgr.devices if d.online
)
no_solar = all(
    (d.solarPort.total_solar_power if d.solarPort else 0) == 0
    for d in mgr.devices if d.online
)
if all_blocked and no_solar and setpoint > 0:
    # Nächster Poll erst in 60s statt 5s
    return SLOW_POLL_INTERVAL
```

## Nebeneffekt

Löst auch den "Setpoint-Churn in der Nacht" — nachts sind Geräte ebenfalls bei minSoC
blockiert ohne Solar. Statt alle 5-15 Sekunden nutzlos zu rechnen, reicht ein Check
pro Minute.

## Betroffene Dateien

| Datei | Stelle |
|-------|--------|
| `power_strategy.py` | Hauptschleife / Return-Wert |
| `manager.py` | Polling-Steuerung (je nach Implementierung) |

## Risiko

Gering-Mittel. Wenn Solar plötzlich einsetzt, dauert es bis zu 60s bis das System reagiert.
Das ist akzeptabel, da der Ladestart ohnehin MQTT-getriggert werden kann.
