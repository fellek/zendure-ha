# Vorschlag 02: Stop-Kommando nur wenn Gerät nicht IDLE

## Status: Umgesetzt (2026-04-14)

Umsetzung in `power_strategy.py:150-152` (Early-Return in `apply_assignment`):

```python
# Skip redundant stop commands when device is already idle (Vorschlag 02).
if d.power_flow_state == PowerFlowState.IDLE:
    return 0
```

Greift für `STOP_CHARGE` und `STOP_DISCHARGE`. Der SF 2400-Quirk-Guard in
`device.py` bleibt als zweite Sicherungslinie aktiv.

Regression-Tests: `tests/test_power_strategy_regressions.py`
(`test_stop_charge_on_idle_device_is_noop`,
`test_stop_discharge_on_idle_device_is_noop`,
`test_stop_charge_on_charging_device_still_fires`).

## Priorität: Mittel

## Problem

`STOP_CHARGE` und `STOP_DISCHARGE` in `apply_assignment` (power_strategy.py) werden auch
an Geräte gesendet, die bereits im Ziel-Zustand (IDLE) sind. Das führt zu 300+ No-Op-
Aufrufen pro Stunde.

## Evidenz

**Log 13. April, 03:00-07:00:**
```
Stopping charge SolarFlow 2400 AC with 0
Power discharge SolarFlow 2400 AC => no action [power 0]
```

Dieses Muster wiederholt sich ca. alle 15 Sekunden, 300x pro Stunde, obwohl das Gerät
bereits idle ist und `gridConsumption=0`. Der SF2400-Quirk-Guard in `device.py` fängt
den Aufruf ab (`no action`), aber der Overhead bleibt (Log-Spam, SDK-Call-Overhead).

## Ursache

Die Strategy erkennt `gridConsumption > 0` (vermutlich Rauschen oder Eigenverbrauch)
-> klassifiziert als CHARGE -> ruft Stop -> Gerät ist aber bereits IDLE.

## Lösung (Bug #8)

In `apply_assignment` oder `_distribute_power` vor dem Stop-Kommando prüfen:

```python
async def apply_assignment(d, assignment) -> int:
    ...
    if cmd == Command.STOP_CHARGE:
        if d.power_flow_state == PowerFlowState.IDLE:
            return 0  # Bereits idle, kein Stop nötig
        stop_pwr = SmartMode.POWER_IDLE_OFFSET if offgrid_consumption > 0 else 0
        _LOGGER.info("Stopping charge %s with %s", d.name, stop_pwr)
        return await d.power_discharge(stop_pwr)

    # Analog für STOP_DISCHARGE
    if d.power_flow_state == PowerFlowState.IDLE:
        return 0
    ...
```

## Betroffene Dateien

| Datei | Stelle |
|-------|--------|
| `power_strategy.py` | `apply_assignment()`, ca. Zeile 130-140 |

## Risiko

Gering. Ein Gerät das bereits IDLE ist, braucht kein Stop-Kommando. Der Check verhindert
nur redundante Aufrufe.
