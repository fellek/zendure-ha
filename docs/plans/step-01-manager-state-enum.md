# Step 01: ManagerState erweitern

## Kontext

`ManagerState` wird aktuell nur als Manager-Gesamtzustand genutzt (IDLE, CHARGE, DISCHARGE, OFF).
Er soll zusätzlich als Device-Ist-Zustand (`d.op_state`) dienen. OFF deckt "Manager aus" und
"Gerät offline" ab.

## Abhängigkeiten

- Keine (erster Schritt der Kette)

## Änderung

**Datei:** `custom_components/zendure_ha/const.py`

```python
class ManagerState(Enum):
    IDLE = 0
    CHARGE = 1
    DISCHARGE = 2
    OFF = 3           # Bestehend: Manager aus / Gerät offline
    BYPASS = 4        # NEU: SOCEMPTY + kein Laden/Entladen, Strom fließt passiv durch
    WAKEUP = 5        # NEU: Übergang von Bypass -> aktiver Zustand (von Strategy gesetzt)
    SOCEMPTY = 6      # NEU: SOCEMPTY + komplett inaktiv
```

## Verifikation

- `python -m py_compile custom_components/zendure_ha/const.py`
- Bestehende Nutzung von `ManagerState.IDLE/CHARGE/DISCHARGE/OFF` bleibt unverändert
