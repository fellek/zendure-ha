# Plan 01: BYPASS aus PowerFlowState entkoppeln

## Status: Offen

## Problem

`PowerFlowState.BYPASS` (value 4) ist ein Peer von CHARGE/DISCHARGE/IDLE im Enum und
impliziert damit einen exklusiven Zustand. In Wirklichkeit beschreibt BYPASS eine
*Hardware-Relay-Bedingung*, die orthogonal zum Lade-/Entlade-Zustand ist. Das MQTT-Feld
`pass` (0 = normal, 2 = BYPASS-REVERSE, 3 = BYPASS-INPUT) ist der native Indikator,
wird aber für die Power-Logik ignoriert.

## Log-Beobachtungen

- Schwere BYPASS-Oszillation: CHARGE -> BYPASS -> DISCHARGE -> BYPASS Zyklen über ~10 min
- `batcur: 65534` während BYPASS = unsigned 16-bit two's-complement -2 (nahe Null)
- `smartMode` fällt von 1 auf 0 während BYPASS; steigt bei CHARGE-Wiedereintritt auf 1
- `inputLimit: 0` / `outputLimit: 0` während BYPASS — Gerät ignoriert Lade-Befehle
- Wake-Latenz: Gerät bleibt 2-3 Minuten in BYPASS vor nächstem CHARGE-Eintritt
- `pass`-Werte: 0 (normal), 2 (BYPASS-REVERSE), 3 (BYPASS-INPUT). Wert 1 nicht verwendet.

## Geplante Änderungen

### 1. `const.py` — BYPASS aus Enum entfernen

```python
class PowerFlowState(Enum):
    OFF      = 0
    CHARGE   = 1
    DISCHARGE = 2
    IDLE     = 3
    WAKEUP   = 5   # bleibt als Übergangszustand
```

### 2. `device.py` — `is_bypassing` Property hinzufügen

```python
@property
def is_bypassing(self) -> bool:
    """True wenn MQTT 'pass'-Feld Bypass aktiv meldet (Werte 2 oder 3)."""
    return bool(self.byPass.is_on)
```

Alle drei Branches in `update_power_flow_state()` die `PowerFlowState.BYPASS` zuweisen
werden durch `PowerFlowState.IDLE` ersetzt.

### 3. `power_strategy.py` — Caller umstellen

- `_classify_single_device`: Kommentar anpassen (BYPASS entfernt)
- `_wake_idle_devices` Pass 1: `d.power_flow_state != PowerFlowState.BYPASS` -> `not d.is_bypassing`

## Betroffene Dateien

| Datei | Zeilen |
|-------|--------|
| `const.py` | 47-56 (Enum + Kommentar) |
| `device.py` | ~80 (Property), 337, 345, 355 (BYPASS -> IDLE) |
| `power_strategy.py` | 198 (Kommentar), 454, 465 (Caller) |

## Verifikation

1. `grep -r "PowerFlowState.BYPASS"` liefert 0 Ergebnisse
2. Runtime: `pass: 3` -> `device.is_bypassing == True`, `power_flow_state` bleibt IDLE
3. Wake-Pfad: `_wake_idle_devices` Pass 1 weckt Gerät bei `is_bypassing=True`
