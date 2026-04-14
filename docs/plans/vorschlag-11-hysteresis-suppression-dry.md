# Vorschlag 11: `HysteresisFilter.apply_device_suppression()` — Charge/Discharge-Zweige vereinheitlichen

## Priorität: Niedrig

## Problem

`power_strategy.py:210-243` enthält zwei strukturell identische Zweige:

```python
def apply_device_suppression(self, pwr, is_charge, start, optimal, state, label, name) -> int:
    if is_charge:
        abs_start = abs(start); abs_optimal = abs(optimal); abs_pwr = abs(pwr)
        delta = abs_start * HYSTERESIS_START_FACTOR - abs_pwr
        if delta >= 0: self.accumulator = 0
        else: self.accumulator += int(-delta)
        if self.accumulator > abs_optimal: pwr = 0
        # Logging
    elif state != DeviceState.SOCFULL:
        delta = start * HYSTERESIS_START_FACTOR - pwr
        if delta <= 0: self.accumulator = 0
        else: self.accumulator += int(delta)
        if self.accumulator > optimal: pwr = 0
    return pwr
```

Beide Zweige machen dasselbe — einer arbeitet mit Beträgen, der andere mit
vorzeichenbehafteten Werten. Zusätzlich fehlt im Discharge-Zweig das Logging.

## Warum notwendig

- **Drift:** Ein Bugfix im Charge-Zweig landet nicht automatisch im Discharge-Zweig.
- **Fehlendes Logging** im Discharge-Zweig erschwert die Diagnose von Hysterese-
  Problemen beim Entladen.
- Der Parameter `state: DeviceState` wird nur im Discharge-Zweig benutzt —
  ungleichartige Behandlung.

## Warum hilfreich

- Ein gemeinsamer Berechnungspfad — die Richtung wird nur einmal entschieden.
- Einheitliches Logging auf beiden Seiten.
- Neue Quirks (z. B. `WAKEUP`-Sonderbehandlung) lassen sich an einer Stelle
  einfügen.

## Lösungsskizze

```python
def apply_device_suppression(self, pwr, direction, start, optimal, state, label, name) -> int:
    if direction == Direction.DISCHARGE and state == DeviceState.SOCFULL:
        return pwr  # SOCFULL: keine Discharge-Suppression

    sign = 1 if direction == Direction.CHARGE else -1  # nur Berechnung
    abs_pwr = abs(pwr); abs_start = abs(start); abs_optimal = abs(optimal)

    delta = abs_start * SmartMode.HYSTERESIS_START_FACTOR - abs_pwr
    if delta >= 0:
        self.accumulator = 0
    else:
        self.accumulator += int(-delta)

    suppressed = self.accumulator > abs_optimal
    _LOGGER.debug("%s[%s] dir=%s abs_pwr=%s thresh=%s acc=%s/%s => %s",
                  label, name, direction.name, abs_pwr,
                  abs_start * SmartMode.HYSTERESIS_START_FACTOR,
                  self.accumulator, abs_optimal,
                  "SUPPRESSED" if suppressed else "OK")
    return 0 if suppressed else pwr
```

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `power_strategy.py` | 210-243 |

## Risiko

**Gering bis mittel.** Logik ist klein und gut isoliert; Subtilität liegt im
`state != SOCFULL`-Guard, der korrekt erhalten bleiben muss.

## Abwägung

- **Pain Point heute:** Zwei Stellen pflegen; Diagnose-Logging asymmetrisch.
- **Kosten Nicht-Handeln:** Gering, solange keiner der Zweige angefasst wird.
- **Kosten des Refactorings:** Minimal (~20 Zeilen).
- **Netto-Nutzen:** Mittel; vor allem Klarheit und besseres Logging.
- **Alternative:** Nur das Logging vom Charge- in den Discharge-Zweig kopieren
  (symmetrisch halten), Struktur unverändert.

## Abhängigkeiten

- Unabhängig.
- Gut **vor** `vorschlag-08` (Context-Dataclass) als kleinerer Vorübung.
