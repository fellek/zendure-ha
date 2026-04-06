# Power Classification: Wirkweise, Beispielwerte und Reaktionszeiten

Diese Datei beschreibt, wie `power_strategy.py` Geräte in Zyklen klassifiziert, welche Aktionen
je nach Klasse ausgelöst werden, und wie lange es dauert bis der Strom nach einer Änderung
tatsächlich dem Sollwert entspricht.

---

## Übersicht: Klassifikation pro Zyklus

Jeder Zyklus läuft so ab:

```
P1-Messung
  → reset_power_state()          alle Listen geleert
  → _classify_devices()          jedes Gerät einmalig eingeordnet
  → _update_group_limits()       Grenzen berechnet
  → distribute_charge/discharge()  Setpoints verteilt + Wakeup
```

**Zyklusintervalle** (aus `SmartMode`):

| Zustand       | Intervall          | Auslöser                                  |
|---------------|--------------------|-------------------------------------------|
| Normal        | 4 s (`TIMEZERO`)   | Zeitgesteuerter Regelzyklus               |
| Schnell (Fast)| 1,5 s (`TIMEFAST`) | Abrupte P1-Änderung (Standardabweichung > 3,5× stddev oder > 15 W) |
| Minimum       | 400 ms             | Untergrenze zwischen zwei Updates         |

---

## Klassifikation CHARGE

### Erkennungsbedingung

```
homeInput > offgrid_power
→ home = -(homeInput - offgrid_power) < 0
```

Das Gerät bezieht aktiv Strom aus dem Hausnetz, mehr als es aus dem Offgrid-Port produziert.

### Beispielwerte

| Feld          | Wert   | Bedeutung                            |
|---------------|--------|--------------------------------------|
| homeInput     | 800 W  | Gerät zieht 800 W aus dem Netz       |
| offgrid_power | 0 W    | Kein Solar-Eingang am DC-Port        |
| home (netto)  | -800 W | → CHARGE, setpoint_delta = -800 W    |
| electricLevel | 42 %   | SoC                                  |
| state         | INACTIVE / SOCEMPTY | Firmware-Zustand          |

### Was passiert

- Gerät landet in `mgr.charge`
- `mgr.charge_limit` erhöht sich um `pwr_max` des Geräts
- `distribute_charge()` verteilt den negativen Setpoint proportional nach Gewicht
- Mehrere CHARGE-Geräte teilen sich den Überschuss nach: `Gewicht = pwr_max × (100 − SoC)`

### Reaktionszeit (IDLE → CHARGE)

```
Zyklus 0:  Gerät ist IDLE → Wakeup: power_charge(-50W) gesendet
           Hysterese startet Cooldown-Timer (5 s oder 20 s)
Zyklus 1:  (nach ~4 s) Gerät antwortet, homeInput steigt
           Hysterese hält Setpoint bei 0 während Cooldown
Zyklus 2+: Hysterese läuft ab → voller Setpoint wird verteilt
```

| Szenario                       | Cooldown | Gesamtzeit bis voller Strom |
|-------------------------------|----------|-----------------------------|
| Letzte Ladung > 5 min her     | 5 s (Fast) | ~10–12 s                 |
| Letzte Ladung < 5 min her     | 20 s (Slow)| ~25–27 s                 |

---

## Klassifikation DISCHARGE

### Erkennungsbedingung

```
homeOutput > 0  ODER  offgrid_power > 0
→ home = homeOutput
```

Das Gerät liefert aktiv Strom ins Hausnetz oder an den Offgrid-Port.

### Sonderfälle

| Zustand               | homeOutput | offgrid | Klassifikation         |
|-----------------------|------------|---------|------------------------|
| Normal aktiv          | 400 W      | 0 W     | DISCHARGE (ACTIVE)     |
| Weckzustand           | 0 W        | 20 W    | DISCHARGE (WAKEUP)     |
| SOCFULL Solar-Bypass  | 0 W        | 200 W   | DISCHARGE (WAKEUP), kein setpoint_delta |

### Beispielwerte (Normalbetrieb)

| Feld          | Wert   | Bedeutung                    |
|---------------|--------|------------------------------|
| homeOutput    | 450 W  | Gerät liefert 450 W ins Netz |
| offgrid_power | 0 W    | Kein aktiver AC-Ausgang      |
| home (netto)  | 450 W  | setpoint_delta = 450 W       |
| electricLevel | 71 %   | SoC                          |
| state         | INACTIVE | Normalbetrieb                |

### Was passiert

- Gerät landet in `mgr.discharge`
- `mgr.discharge_limit` erhöht sich um `pwr_max`
- `mgr.discharge_produced` sinkt um `pwr_produced` (bereits gelieferte Leistung)
- `distribute_discharge()` verteilt proportional nach: `Gewicht = pwr_max × SoC`

### Reaktionszeit (IDLE → DISCHARGE, Cold-start)

```
Zyklus 0:  mgr.discharge leer, mgr.idle hat Geräte, setpoint > 50 W
           → Cold-start Wakeup: power_discharge(50W) gesendet (1 Gerät)
Zyklus 1:  (nach ~4 s) Gerät antwortet, homeOutput > 0
           → Gerät jetzt in mgr.discharge
           → voller Setpoint wird verteilt
```

**Gesamtzeit IDLE → voller Discharge-Strom: ~5–10 s**

### Reaktionszeit (DISCHARGE Setpoint-Änderung, laufendes Gerät)

```
P1 ändert sich abrupt (isFast = True):
  Nächster Zyklus: ~1,5 s
  Gerät empfängt neues MQTT-Kommando
  Inverter regelt nach: ~1–3 s
  Messgerät meldet neuen Wert: ~1 s
```

**Gesamtzeit Setpoint-Änderung: ~3–6 s**

---

## Klassifikation IDLE

### Erkennungsbedingung

```
homeInput == 0  UND  homeOutput == 0  UND  offgrid_power == 0
UND  state != SOCEMPTY
```

Das Gerät ist betriebsbereit, liefert oder verbraucht aber keinen messbaren Strom.

### Beispielwerte

| Feld          | Wert    | Bedeutung                                |
|---------------|---------|------------------------------------------|
| homeInput     | 0 W     | Kein Netzbezug                           |
| homeOutput    | 0 W     | Keine Netzeinspeisung                    |
| offgrid_power | 0 W     | Kein DC-Ausgang                          |
| state         | INACTIVE | Firmware meldet Standby                 |
| electricLevel | 51 %    | SoC                                      |

### Was passiert

- Gerät landet in `mgr.idle`
- `mgr.idle_lvlmax` und `mgr.idle_lvlmin` werden aktualisiert
- Kein MQTT-Kommando wird gesendet solange kein Wakeup nötig ist

### Wann wird IDLE geweckt?

**Charge-Wakeup** (über `_wake_idle_devices`, Charge-Pfad):
```
Bedingung: dev_start < 0 (aktive CHARGE-Geräte sind überlastet)
Sortierung: Höchster SoC zuerst (voll → wird am schnellsten geladen)
Kommando:  power_charge(-POWER_START - max(0, pwr_offgrid))
           = power_charge(-50W) bei pwr_offgrid=0
```

**Discharge-Wakeup** (über `_wake_idle_devices`, Discharge-Pfad):
```
Bedingung: dev_start > 0 (aktive DISCHARGE-Geräte sind nicht genug)
Sortierung: Niedrigster SoC zuerst (bitte zuerst entladen)
Schutz:    Kein Wakeup wenn SoC ≤ minSoc + 2% (DISCHARGE_SOC_BUFFER)
Kommando:  power_discharge(POWER_START) = power_discharge(50W)
```

**Cold-start Discharge-Wakeup** (in `distribute_discharge`, wenn kein Gerät aktiv):
```
Bedingung: mgr.discharge leer  UND  mgr.idle nicht leer  UND  setpoint > 50 W
Sortierung: Höchster SoC zuerst
Schutz:    Gleicher minSoc-Guard wie oben
Kommando:  power_discharge(50W) — 1 Gerät pro Zyklus
```

### Reaktionszeit (CHARGE → IDLE → DISCHARGE)

Beim Wechsel von Laden zu Entladen muss das Gerät zweimal klassifiziert werden:

```
Zyklus 0:  Gerät ist CHARGE → distribute_discharge stoppt: power_discharge(0W)
Zyklus 1:  (nach ~4 s) homeInput = 0 → Gerät jetzt IDLE
Zyklus 2:  Cold-start Wakeup: power_discharge(50W)
Zyklus 3:  (nach ~4 s) homeOutput > 0 → Gerät jetzt DISCHARGE → voller Strom
```

**Gesamtzeit CHARGE → voller Discharge-Strom: ~12–20 s**

---

## Klassifikation SOCEMPTY

### Erkennungsbedingung

```
d.state == DeviceState.SOCEMPTY
```

Der Firmware-Zustand des Geräts meldet `SOCEMPTY` (Akku bei oder unter `minSoc`).
Zwei Unterfälle werden unterschieden:

### Unterfall A: Bypass / Pass-Through

```
state == SOCEMPTY  UND  homeInput > 0  UND  offgrid_power > 0
```

Das Relais ist im Durchleitungsmodus: Strom fließt vom Netz direkt zum Offgrid-Verbraucher,
ohne über den Akku zu gehen.

| Feld          | Wert   | Bedeutung                                |
|---------------|--------|------------------------------------------|
| homeInput     | 68 W   | Netzstrom für den Offgrid-Verbraucher    |
| offgrid_power | 54 W   | DC-Ausgang am Offgrid-Port               |
| state         | SOCEMPTY | Akku leer                              |
| electricLevel | 2 %    | SoC am Minimum                           |

### Unterfall B: Vollständig idle

```
state == SOCEMPTY  UND  homeInput == 0  UND  batteryInput == 0  UND  homeOutput == 0
```

Gerät ist leer und vollständig passiv (kein Strom in irgendeiner Richtung).

| Feld          | Wert   | Bedeutung                                |
|---------------|--------|------------------------------------------|
| homeInput     | 0 W    | Kein Netzbezug                           |
| homeOutput    | 0 W    | Keine Einspeisung                        |
| batteryInput  | 0 W    | Akku wird nicht geladen                  |
| state         | SOCEMPTY | Akku leer                              |
| electricLevel | 2 %    | SoC am Minimum                           |

### Was passiert

- Gerät landet in `mgr.socempty` (nicht in `mgr.idle` oder `mgr.charge`)
- `mgr.idle_lvlmax`/`mgr.idle_lvlmin` werden trotzdem aktualisiert
- **Kein Entladen** — SOCEMPTY-Geräte werden im Discharge-Pfad übersprungen
- **Nur Laden** — Wakeup ausschließlich im Charge-Pfad

### Wann wird SOCEMPTY geweckt?

```
Charge-Pfad (distribute_charge → _wake_idle_devices, is_charge=True):
  Bedingung: Überschuss vorhanden (setpoint < 0 in MATCHING-Modus)
  Relay-Guard: Wenn offgrid_power > 0 → Gerät überspringen (Relay steuert selbst)
  Kommando:   power_charge(-50W - max(0, pwr_offgrid))
```

SOCEMPTY-Geräte werden **nicht** durch `distribute_discharge` geweckt — weder direkt
noch indirekt. Die frühere Oszillation (charge-stop-charge) ist damit eliminiert.

### Reaktionszeit (SOCEMPTY → CHARGE aktiv)

```
Zyklus 0:  Überschuss vorhanden → power_charge(-50W) an SOCEMPTY-Gerät gesendet
Zyklus 1:  (nach ~4 s) homeInput > 0 → Gerät wird als CHARGE klassifiziert
           Hysterese startet (5 s oder 20 s Cooldown)
Zyklus 2+: Voller Setpoint nach Cooldown
```

**Gesamtzeit SOCEMPTY → geladen: ~10–27 s** (je nach Hysterese-Zustand)

### Stoppsignal bei SOCEMPTY in mgr.charge

Wenn ein SOCEMPTY-Gerät bereits Strom bezieht (homeInput > 0, korrekt als CHARGE klassifiziert)
und der Modus auf Discharge wechselt:

```python
# distribute_discharge, Stop-Charge-Loop:
for d in mgr.charge:
    if d.state == DeviceState.SOCEMPTY:
        continue  # kein power_discharge(0) — Gerät darf laden
```

Das Gerät behält seinen Ladebefehl. Es wird erst gestoppt wenn der Überschuss wegfällt und
`distribute_charge` keinen negativen Setpoint mehr verteilt.

---

## Timing-Übersicht

| Übergang                               | Typische Gesamtzeit |
|----------------------------------------|---------------------|
| IDLE → DISCHARGE (Cold-start)          | 5–10 s              |
| IDLE → CHARGE                          | 10–27 s (+ Hysterese)|
| CHARGE → IDLE (Strom gestoppt)         | 4–8 s               |
| CHARGE → DISCHARGE                     | 12–20 s             |
| DISCHARGE Setpoint-Änderung            | 3–6 s               |
| SOCEMPTY → CHARGE (Überschuss da)      | 10–27 s             |
| SOCEMPTY Bypass → stabil               | 1 Zyklus (~4 s)     |

> **Hinweis:** Die Zyklus-Zeiten hängen davon ab, ob `isFast=True` ausgelöst wird
> (dann 1,5 s statt 4 s). Abrupte P1-Änderungen (> 3,5 × stddev) lösen Fast-Modus aus.
> Kleine Schwankungen (< 15 W) werden ignoriert.

---

## Hysterese-Schwellen im Überblick

| Konstante                    | Wert  | Wirkung                                              |
|------------------------------|-------|------------------------------------------------------|
| `POWER_START`                | 50 W  | Mindeststrom für Wakeup / Discharge-Erkennung        |
| `DISCHARGE_SOC_BUFFER`       | 2 %   | Stopp-Grenze: minSoc + 2 %                           |
| `HYSTERESIS_FAST_COOLDOWN`   | 5 s   | Ladepause wenn letzte Ladung > 5 Min zurück          |
| `HYSTERESIS_SLOW_COOLDOWN`   | 20 s  | Ladepause wenn letzte Ladung < 5 Min zurück          |
| `HYSTERESIS_LONG_COOLDOWN`   | 300 s | Grenzwert für Fast vs. Slow Entscheidung             |
| `WAKEUP_CAPACITY_FACTOR`     | 2×    | Multiplikator auf optimal für Wakeup-Entscheidung    |
| `SOC_IDLE_BUFFER`            | 3 %   | Puffer bei SoC-Vergleich idle_lvlmax vs. Gerät-SoC   |