# Vorschlag 08: `_distribute_power()` — Context-Objekt + Clock-Abstraktion

## Priorität: Mittel

## Problem

`_distribute_power()` in `power_strategy.py:514-520` hat 5 Parameter:

```python
async def _distribute_power(
    mgr: ZendureManager,
    devices: list,
    setpoint: int,
    direction: _DistDirection,
    time: datetime,
) -> None:
```

Zusätzlich ist `datetime.now()` an mehreren Stellen direkt genutzt (z. B.
`device.py:342` für `wakeup_entered`), was Unit-Tests zwingt, `datetime` zu
patchen.

## Warum notwendig

- **Testbarkeit:** Szenarien wie „Hysterese nach 301 s Cooldown" lassen sich
  nur mit echtem Sleep oder `freezegun` testen. Eine injizierbare Clock macht
  jeden Pfad deterministisch.
- **Lesbarkeit:** Wenn aus `_distribute_power` → `_compute_weights` →
  `apply_device_suppression` durchgereicht wird, wächst jede Signatur.
- **Erweiterbarkeit:** Neue Felder (z. B. `p1` für Mode-Entscheidungen innerhalb
  der Distribution) verlangen Änderungen in allen Aufrufern.

## Warum hilfreich

- Ein `PowerDistributionContext` sammelt die transienten Zustände eines Zyklus;
  alle Phasen (Assess, Classify, Dispatch, Distribute) teilen dasselbe Objekt.
- Eine `Clock`-Abstraktion (Protocol mit `now()`-Methode) ermöglicht schnelle,
  deterministische Tests.
- Verringert Zufälligkeit beim Debuggen: alle Zeitstempel eines Zyklus sind
  identisch.

## Lösungsskizze

```python
@dataclass(frozen=True, slots=True)
class PowerDistributionContext:
    mgr: ZendureManager
    devices: list[ZendureDevice]
    setpoint: int
    direction: _DistDirection
    time: datetime

async def _distribute_power(ctx: PowerDistributionContext) -> None: ...

class Clock(Protocol):
    def now(self) -> datetime: ...

class SystemClock:
    def now(self) -> datetime: return datetime.now()
```

`ZendureManager` bekommt ein `self.clock: Clock` (Default `SystemClock()`),
Tests injizieren `FakeClock`.

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `power_strategy.py` | 514-700 (Distribution-Kette) |
| `manager.py` | Clock-Injektion in den Ctor |
| `device.py` | `datetime.now()`-Aufrufe (z. B. 342) auf `mgr.clock.now()` umstellen |

## Risiko

**Gering.** Mechanisches Refactoring; Verhalten unverändert. Hauptrisiko:
vergessene `datetime.now()`-Stellen. Lösbar per Grep.

## Abwägung

- **Pain Point heute:** Kein ordentlicher Unit-Test für Hysterese und Cooldowns.
- **Kosten Nicht-Handeln:** Jede Regression wird weiterhin nur am echten Gerät
  entdeckt.
- **Kosten des Refactorings:** Niedrig — überschaubare Zahl an Call-Sites.
- **Netto-Nutzen:** Hoch, sobald eine Test-Suite existiert.
- **Alternative:** Nur Clock injizieren, Context-Dataclass später — schon das
  allein bringt 80 % des Testnutzens.

## Abhängigkeiten

- Unabhängig; kann jederzeit umgesetzt werden.
- Profitiert `vorschlag-11` und `vorschlag-12` (die dann auch ctx-basiert sind).
