# Vorschlag 10: `power_charge` / `power_discharge` — Symmetrie-Duplikate zusammenführen

## Priorität: Mittel

## Problem

`device.py:294-315` enthält zwei nahezu spiegelbildliche Methoden:

```python
async def power_charge(self, power: int) -> int:
    power = min(0, max(power, self.charge_limit))
    if power == 0 and self.state == DeviceState.SOCEMPTY and self.bypass.is_active:
        ...
        return self.connectorPort.power_consumption
    if abs(power + self.connectorPort.power) <= SmartMode.POWER_TOLERANCE:
        ...
        return self.connectorPort.power_consumption
    return await self.charge(power)

async def power_discharge(self, power: int) -> int:
    power = max(0, min(power, self.discharge_limit))
    if abs(power - self.connectorPort.power) <= SmartMode.POWER_TOLERANCE:
        ...
        return self.connectorPort.power_production
    return await self.discharge(power)
```

Die Vorzeichen-Asymmetrie (`+` vs. `-`, `min(0,…)` vs. `max(0,…)`) ist
fehleranfällig; die BYPASS-Sonderbehandlung existiert nur in `power_charge`.

## Warum notwendig

- **Bug-Historie:** Bug #6 (BYPASS-Oszillation, siehe
  `memory/bug_bypass_oscillation.md`) und der SF2400-Stop-Charge-Quirk
  (`memory/quirk_sf2400_stop_charge.md`) beruhen genau auf dieser Asymmetrie.
- **Drift-Risiko:** Wenn eine Seite eine neue Regel bekommt (z. B. SOCFULL-
  Handling), wird die andere leicht vergessen.
- **Lesbarkeit:** Der Leser muss Vorzeichen-Spiegelungen im Kopf nachvollziehen.

## Warum hilfreich

- Eine Richtungs-Enum macht den Unterschied explizit: `Direction.CHARGE` /
  `Direction.DISCHARGE`.
- Zentraler Einstiegspunkt zum Einhängen von Hardware-Quirks (BYPASS-Hold,
  SOCEMPTY-Ausnahme, SF2400-Workarounds).
- Etwaige zukünftige Quirks (z. B. AB2000 Mindest-Leistung) werden an **einer**
  Stelle gesammelt.

## Lösungsskizze

```python
async def _apply_power(self, power: int, direction: Direction) -> int:
    """Vereinheitlichter Eintritt für Lade-/Entlade-Kommandos mit Quirks."""
    if direction == Direction.CHARGE:
        power = min(0, max(power, self.charge_limit))
        target_delta = power + self.connectorPort.power
        fallback = self.connectorPort.power_consumption
        hardware_call = self.charge
    else:
        power = max(0, min(power, self.discharge_limit))
        target_delta = power - self.connectorPort.power
        fallback = self.connectorPort.power_production
        hardware_call = self.discharge

    # Quirks (zentral)
    if self._should_hold_bypass(direction, power):
        return fallback
    if abs(target_delta) <= SmartMode.POWER_TOLERANCE:
        return fallback

    return await hardware_call(power)

async def power_charge(self, power: int) -> int:
    return await self._apply_power(power, Direction.CHARGE)

async def power_discharge(self, power: int) -> int:
    return await self._apply_power(power, Direction.DISCHARGE)
```

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `device.py` | 294-315 |

## Risiko

**Mittel.** Hochgradig verhaltensrelevanter Code — jede subtile Vorzeichen-
Änderung kann BYPASS-Oszillation oder SF2400-Stop-Charge-Bug wieder auslösen.
Ohne Tests nur mit sorgfältigem manuellem Vergleich.

## Abwägung

- **Pain Point heute:** Ein bekannt bugträchtiger Bereich mit Copy-Paste-Charakter.
- **Kosten Nicht-Handeln:** Nächster Quirk muss zweimal eingebaut werden.
- **Kosten des Refactorings:** Mittel — Logik ist kompakt, aber kritisch.
- **Netto-Nutzen:** Hoch, wenn erst mit automatisierten Tests abgesichert.
- **Alternative:** Nur den `target_delta`-Ausdruck als gemeinsame Helper-
  Funktion extrahieren, Rest belassen — reduziert Risiko, bringt weniger Nutzen.

## Abhängigkeiten

- Unabhängig. Bezieht sich inhaltlich auf Bugs #6 und `quirk_sf2400_stop_charge`.
- Profitiert von `vorschlag-08` (Testbarkeit).
