# Plan 02: Bypass-Keepalive & Wakeup-Routine

## Status: Offen — überarbeitet nach Log-Analyse (2026-04-14)

## Kontext / Zielsetzung

Der Bypass-Zustand (`pass=2`/`pass=3`) wird **ausschließlich von der Geräte-Firmware**
geschaltet und kann von der Integration nicht beeinflusst werden. Ziel dieses Plans
ist daher **nicht** "Bypass verlassen", sondern: **das Gerät im Bypass reaktionsfähig
halten**, damit der nächste Charge/Discharge-Befehl unmittelbar greift, sobald die
Firmware den Bypass von sich aus verlässt.

## Problem

Während `pass=3` (BYPASS-INPUT) bestimmt `inputLimit` die Wakeup-Latenz:

| Zustand im Bypass | Wakeup-Dauer | Cmd → Charge |
|---|---|---|
| `inputLimit > 0` (beliebig) | 15–30 s | **5–20 s** |
| `inputLimit = 0` | 30–115 s | verzögert oder kein Start |
| `inputLimit = 0`, kein Cmd (P1 > 0) | Stunden | nie |

Nach `power_off()` oder beim Mode-Wechsel setzt die Integration `inputLimit=0` und
`outputLimit=0`. Das Gerät verliert die Reaktionsfähigkeit für einen vollen
Relay-Umschaltzyklus (2–3 Minuten).

## Bereits vorhandener Code (nicht duplizieren!)

Die Integration hat bereits drei Wakeup-Pfade, die Plan 02 kennen und **abgrenzen**
muss:

| Pfad | Ort | Trigger | Befehl |
|---|---|---|---|
| Discharge-Idle-Wakeup | `power_strategy.py:490–500` | `mgr.idle` ∧ setpoint > 10 W ∧ kein Discharge | `power_discharge(10)` |
| Bypass-wake SOCEMPTY | `power_strategy.py:708–720` | `_need_wakeup(d)` ∧ `DeviceState.SOCEMPTY` | `power_charge(-(10+device_power))` |
| Bypass-wake SOCFULL | `power_strategy.py:722–729` | `_need_wakeup(d)` ∧ `DeviceState.SOCFULL` | `power_discharge(10+device_power)` |

Die bestehenden Bypass-Wakeups greifen **nur** bei SOC-Extremen (SOCEMPTY/SOCFULL).
**Lücke:** Der "normale" Bypass (SOC dazwischen, Firmware wechselt z. B. wegen
Lastprofil oder Temperatur in Bypass) wird von keinem Pfad abgedeckt.

## Fakten aus Log-Analyse (Archiv 2026-03-19 bis 2026-04-14)

1. **`pass=3` mit `gridInputPower=0` tritt NIE auf.** Eigenverbrauch + `offgridPort`-Last
   halten `homeInput` konstant auf 27–70 W. Eine Bedingung `power_consumption == 0`
   würde in der Praxis nie feuern — Plan muss gegen MQTT-Rohwert `inputLimit == 0`
   prüfen.
2. **`outputLimit=10` in MQTT-Reports stammt aus der Integration** (`SmartMode.POWER_IDLE_OFFSET`
   in den drei Wakeup-Pfaden oben), nicht aus der Firmware.
3. **`pass=2` (Bypass-Output)** tritt bei `smartMode=0` (Offgrid-Modus) mit
   `outputHomePower > 0, gridInputPower=0` auf. Option-B-Bedingung muss auf
   `power_production` statt `power_consumption` laufen.
4. **`packState` oszilliert im 5-s-Takt (2↔0)** während Bypass. Ursache unklar —
   Firmware-Watchdog oder Reaktion auf Integration-Commands.

## Offene Fragen (Recherche vor Implementierung)

1. **Wakeup-Latenz quantifizieren:** Im Archiv fehlen saubere
   `(command → packState=1)`-Paare. Gezielten A/B-Test durchführen:
   `inputLimit=0` vs. `inputLimit=10` beim selben SOC/Temperatur/smartMode.
2. **Abgrenzung zu Bypass-wake SOCEMPTY/SOCFULL:** Soll Plan 02 diese Pfade
   erweitern (gleiche Funktion, breiterer Trigger) oder einen neuen Pass-0-Pfad
   einführen? Empfehlung: `_wake_idle_devices` um einen SOC-mittleren Zweig
   erweitern, um Redundanz zu vermeiden.
3. **Wechselwirkung mit `smartMode`:** Keepalive-Befehle während `smartMode=1`
   werden nicht in Flash geschrieben — das ist gewünscht (keine Abnutzung), aber
   greift der Befehl trotzdem am Relais?
4. **Einfluss auf Energiebilanz:** 10 W-Dauerkeepalive verändert P1 und Setpoint.
   Die bestehenden Bypass-Wakeups berücksichtigen `device_power` und `offgrid_load`
   in der Berechnung — der neue Pfad muss dieselbe Bilanz-Logik nutzen.
5. **`packState`-Oszillation:** Verursacht der Keepalive selbst die Oszillation?
   Ein Puls (Option C) könnte stabiler sein als Dauerstrom.

## Implementierungsoption (empfohlen)

**Erweiterung von `_wake_idle_devices` um SOC-mittleren Bypass-Zweig**, analog zu
SOCEMPTY/SOCFULL, zwischen den bestehenden `elif`-Ästen:

```python
# Neuer Zweig in power_strategy.py nach Zeile 729
else:  # SOC zwischen min und max
    # Gerät hängt im firmware-gesteuerten Bypass ohne SOC-Extremum.
    # Keepalive: outputLimit=10 hält Reaktionsfähigkeit bis Firmware pass=0 setzt.
    pwr = SmartMode.POWER_IDLE_OFFSET + device_power
    _LOGGER.debug("Bypass-wake NORMAL %s => power_discharge(%s) [bypass=%s]",
                  d.name, pwr, bypass_load)
    await d.power_discharge(pwr)
    d.wake_started_at = datetime.now()
    d.power_flow_state = PowerFlowState.WAKEUP
    pass1_woken.add(d)
```

Vorteile:
- Wiederverwendung der vorhandenen `_need_wakeup`-Gate-Logik und Cooldown (Zeile 697).
- `power_discharge(10)` ist das bereits etablierte Wakeup-Muster (kein neues Verhalten).
- Bilanz-Logik (`device_power`, `offgrid_load`) bleibt konsistent.

Alternative (falls A/B-Test zeigt, dass `inputLimit` wirksamer ist): stattdessen
`power_charge(-(10 + device_power))`.

## Betroffene Dateien

| Datei | Relevanz |
|---|---|
| `power_strategy.py` | `_wake_idle_devices()` — neuer SOC-mittlerer Zweig |
| `const.py` | ggf. neue Konstante `BYPASS_KEEPALIVE_W` (oder weiter `POWER_IDLE_OFFSET`) |
| `device.py` | `is_bypassing`-Property bleibt (Beobachtung, nicht Steuerung) |

## Verifikation

1. **A/B-Test**: Log mit `inputLimit=0` vs. `inputLimit=10` beim Bypass-Exit
   vergleichen. Metrik: Zeit von `pass=0` bis `packState=1`.
2. **Keine Regression in SOCEMPTY/SOCFULL-Pfad**: Bestehende Wakeup-Logs vorher/nachher
   identisch.
3. **P1-Stabilität**: 10 W-Keepalive darf Setpoint-Regler nicht destabilisieren
   (Setpoint-Oszillation < 50 W).
4. **Cooldown greift**: `wake_started_at`-Gate verhindert Command-Flut.
5. **`packState`-Oszillation**: Messen, ob der neue Zweig sie verstärkt oder
   entschärft.