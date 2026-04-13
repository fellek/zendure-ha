# Plan 03: MANUAL-Modus Vorzeichen-Problem (Bug #7)

## Status: Offen

## Problem

Der Nutzer wählt "Manuelles Laden 100W" in der HA-UI, aber `manualpower.asNumber` wird
als +100 gesetzt. In `_dispatch_to_mode` gilt:

```python
case ManagerMode.MANUAL:
    if (setpoint := int(mgr.manualpower.asNumber)) > 0:   # +100 -> Entlade-Pfad!
        await distribute_discharge(mgr, setpoint, time)
```

+100 wird als Entladung interpretiert, nicht als Ladung. Für "Manuelles Laden" müsste
der Wert -100 sein.

## Evidenz

**Log 13. April 2026, 08:00:**
```
Set Manual power discharging: isFast:True, setpoint:100W stored:0W
Discharge => setpoint 100W
Discharge Wakeup blocked: SolarFlow 2400 AC SoC=11% at minSoc limit
```

8 Stunden lang keine Aktion trotz explizitem "Laden"-Befehl. Doppelte Blockade:
falsches Vorzeichen + minSoC-Limit verhindert auch die (fälschliche) Entladung.

## Analyse-Schritte

1. **Wie wird `manualpower` gesetzt?** Prüfen ob es eine `number`-Entity ist und wie
   die UI den Wert setzt. Vermutlich `manager.py` Zeile ~140 bei der Entity-Definition.

2. **Hat die UI ein separates "Laden/Entladen"-Dropdown?** Oder entscheidet nur das
   Vorzeichen des number-Werts?

3. **Konvention prüfen:** Ist die Konvention `positiv = Entladung` gewollt und dem
   Nutzer nicht klar? Oder soll die UI die Invertierung übernehmen?

## Lösungsansätze

### Option A — UI-Ebene: Vorzeichenumkehr beim Setzen

Wenn die UI "Laden" anbietet, muss sie `-abs(value)` setzen. Der Dispatch-Code bleibt
unverändert (positiv = Entladung, negativ = Ladung).

### Option B — Dispatch-Ebene: Richtung aus Modus-Label ableiten

Statt `manualpower.asNumber > 0` prüft der Code ein separates Entity-Attribut
(z.B. `manual_direction`) und erzwingt das korrekte Vorzeichen.

### Option C — Immer |manualpower| nehmen, Richtung separat

```python
case ManagerMode.MANUAL:
    pwr = abs(int(mgr.manualpower.asNumber))
    if mgr.manual_is_discharge:
        await distribute_discharge(mgr, pwr, time)
    else:
        await distribute_charge(mgr, -pwr, time)
```

## Betroffene Dateien

| Datei | Relevanz |
|---|---|
| `manager.py` | `manualpower` Entity-Definition, Zeile ~140 |
| `power_strategy.py` | `_dispatch_to_mode` MANUAL-Zweig, Zeile 438-444 |
| ggf. `select.py` / `number.py` | UI-Entity für manuelle Richtung |

## Verifikation

1. "Manuelles Laden 100W" -> Log zeigt `Set Manual power charging: setpoint:-100W`
2. "Manuelles Entladen 100W" -> Log zeigt `Set Manual power discharging: setpoint:100W`
3. Umschalten zwischen Laden/Entladen im laufenden Betrieb funktioniert
