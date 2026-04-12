# SOCEMPTY Wakeup: Verhalten, Befunde und neue Routine (SF2400 AC)

Dieses Dokument beschreibt das beobachtete Wakeup-Verhalten des SolarFlow 2400 AC
im SOCEMPTY-Zustand, die Ursachen von Oszillationen sowie die daraus abgeleitete
neue Wakeup-Implementierung.

---

## 1. Gerätezustand SOCEMPTY

Ein Gerät befindet sich im Zustand `DeviceState.SOCEMPTY`, wenn der Akku auf oder
unter `minSoc` gefallen ist. Im SF2400 AC führt das zur Aktivierung der `socLimit`-Sperre.

### MQTT-Kennwerte im SOCEMPTY-Zustand

| Feld              | Wert              | Bedeutung                                   |
|-------------------|-------------------|---------------------------------------------|
| `electricLevel`   | ≤ `minSoc` (z. B. 10 %) | Akku am oder unter Untergrenze       |
| `socLimit`        | `2`               | Firmware-Sperre aktiv                       |
| `pass`            | `3`               | Bypass-Relais: Strom fließt durch           |
| `packInputPower`  | `0`               | Akku lädt nicht                             |
| `outputPackPower` | `0`               | Akku entlädt nicht                          |
| `batcur` (raw)    | `65534`           | −0,2 A — Ruhestrom (kein aktiver Fluss)     |
| `packState`       | `2`               | Pack im Standby-Modus                       |

### SOCEMPTY-Sperre wird aufgehoben bei

```
electricLevel > minSoc  →  socLimit = 0
```

Beispiel: `minSoc = 10 %` → Sperre fällt bei `electricLevel = 11 %`.
Das ist nicht der `minSoc`-Wert selbst, sondern der erste diskrete Schritt darüber.

---

## 2. Erfolgreicher Weckzustand: MQTT-Erkennungsmatrix

Ein erfolgreicher Wakeup ist abgeschlossen, wenn der Akku tatsächlich lädt.
Die zuverlässigsten Einzelfelder und Kombinationen aus Log-Analyse (SF2400 AC):

### Einzelne Felder

| Feld              | Bedingung         | Zuverlässigkeit | Anmerkung                                              |
|-------------------|-------------------|-----------------|--------------------------------------------------------|
| `packInputPower`  | `> 0`             | ★★★★★           | Direktmessung: Strom fließt in den Akku                |
| `batcur` (signed) | `> 0`             | ★★★★★           | Echter Ladestrom; `65534` = −0,2 A = Ruhestrom         |
| `socLimit`        | `= 0`             | ★★★★☆           | Sperre aufgehoben; SOC > minSoc                        |
| `electricLevel`   | `> minSoc`        | ★★★★☆           | Langsam ändernd; Voraussetzung für Sperre-Ende         |
| `pass`            | `= 0`             | ★★★☆☆           | Wechselrichter aktiv; auch im normalen IDLE pass=0     |
| `packState`       | `= 1`             | ★★★☆☆           | Wechselt kurzfristig; allein nicht stabil genug        |
| `outputPackPower` | `≈ 0`             | ★★★☆☆           | Beim Laden sollte Entladeleistung nahe null sein        |

### Kombinationen (zuverlässigste Weck-Bestätigungen)

| Kombination | Bedingung                                              | Zuverlässigkeit | Bedeutung                                        |
|-------------|--------------------------------------------------------|-----------------|--------------------------------------------------|
| A           | `packInputPower > 50` AND `pass = 0`                  | ★★★★★           | Aktives Laden durch Wechselrichter               |
| B           | `batcur > 0` AND `packInputPower > 0`                 | ★★★★★           | Doppelt bestätigter Ladefluss                    |
| C           | `socLimit = 0` AND `batcur > 0`                       | ★★★★★           | Sperre weg + echter Ladestrom = erfolgreich geweckt |
| D           | `packInputPower > 50` AND `batcur > 0` AND `pass = 0` | ★★★★★           | Vollständige Bestätigung über alle drei Kanäle   |
| E           | `electricLevel > minSoc` AND `packInputPower > 0`     | ★★★★☆           | SOC über Grenze und Laden bestätigt              |

**Code-interne Schwelle** für den Übergang WAKEUP → CHARGE:
```python
abs(packInputPower - outputPackPower) > POWER_START (50 W)
```

---

## 3. Beobachtete Oszillationen und ihre Ursachen

### 3.1 Phasen im Log (SF2400 AC, SOC=10 %, minSoc=10 %)

| Zeit      | Phase                        | Ursache                                                              |
|-----------|------------------------------|----------------------------------------------------------------------|
| 09:05–09:13 | Passives WAKEUP             | Externes Hauslastrauschen ±15 W — **kein Code-Fehler**              |
| 09:13–09:25 | Aktive Oszillation           | Code-induziert (siehe unten)                                         |

### 3.2 Drei Code-Ursachen der Oszillation

**Ursache 1 — Regel 7: Wakeup-Befehl überschreitet verfügbaren Überschuss**

Der Wakeup-Befehl schickte mehr Ladeleistung als Solarüberschuss vorhanden war.
Beispiel aus dem Log:

```
Verfügbarer Überschuss:  −100 W (setpoint)
Pass 1 Befehl:           −(10 + 97 + 49) = −156 W
Pass 2 Befehl:           min(dev_start, −50) − 49 ≈ −180 W
```

Der MATCHING-Algorithmus regelte daraufhin die Leistung auf den tatsächlichen
Überschuss zurück. Das erzeugte den Oszillationseffekt: Wecken → Drosseln →
WAKEUP → Wecken.

**Ursache 2 — Doppelter Wakeup-Befehl pro Iteration**

In derselben Iteration feuerten Pass 1 (Bypass-Wake) und Pass 2 (Grid-Demand-Wake)
beide `power_charge()` für dasselbe SOCEMPTY-Gerät. Ein Kommentar im Code erlaubte
das explizit mit der falschen Begründung „allow Pass 2 to update with real demand".
Resultat: Zwei Befehle pro Zyklus mit unterschiedlichen Leistungswerten, der höhere
gewann — aber überschoss immer den Überschuss.

**Ursache 3 — Zustandslatenz WAKEUP → CHARGE**

Die Erkennung des Wechsels WAKEUP → CHARGE erfolgt erst wenn
`abs(packInputPower - outputPackPower) > 50 W`. Das dauert 5–10 Sekunden.
In dieser Zeit sendete der Code weitere Wakeup-Befehle an ein Gerät, das
bereits lud — was zu redundanten, teilweise überhöhten Kommandos führte.

---

## 4. Neue Wakeup-Routine

Die Änderungen adressieren alle drei Ursachen.

### 4.1 Übersicht der Änderungen

| Element       | Datei               | Beschreibung                                          |
|---------------|---------------------|-------------------------------------------------------|
| `pass1_woken: set` | `power_strategy.py` | Verhindert Doppel-Wakeup: Pass 2 überspringt Pass-1-Geräte |
| Surplus-Cap   | `power_strategy.py` | Wakeup-Befehl wird auf verfügbaren Überschuss begrenzt|
| `raw_setpoint` | `power_strategy.py` | Echter Überschuss vor dem `_cap_setpoint()`-Aufruf   |
| `wakeup_entered` | `device.py`         | Zeitstempel des WAKEUP→CHARGE-Übergangs               |
| `_ramp_factor()` | `power_strategy.py` | Berechnet Soft-Start-Faktor in drei definierten Szenarien |
| `WAKEUP_RAMP_DURATION` | `const.py`          | Dauer des Soft-Start nach Wakeup (Standard: 30 s)     |

### 4.2 Ablaufdiagramm der neuen `_wake_idle_devices()`

```
_wake_idle_devices(mgr, dev_start, is_charge, setpoint=raw_setpoint):
│
├── pass1_woken = set()
│
├── PASS 1: Bypass-Wakeup (läuft immer, unabhängig von dev_start)
│   für jedes IDLE-Gerät:
│   _need_wakeup(d)?  Nein → überspringen
│   Cooldown aktiv?   Ja   → überspringen
│   │
│   SOCEMPTY:
│     pwr = -(POWER_IDLE_OFFSET + bypass_available + offgrid_load)
│     pwr = max(pwr, setpoint)          ← Surplus-Cap
│     power_charge(pwr)
│     pass1_woken.add(d)
│   │
│   SOCFULL:
│     pwr = POWER_IDLE_OFFSET + bypass_available - offgrid_load
│     power_discharge(pwr)
│     pass1_woken.add(d)
│
├── dev_start < 0?  Nein → return (kein weiterer Wakeup nötig)
│
└── PASS 2: Grid-Demand-Wakeup (nur wenn dev_start < 0)
    für IDLE-Geräte sortiert nach SoC ↓:
    d in pass1_woken?          Ja → überspringen  ← NEU
    power_flow_state == WAKEUP? Ja → überspringen  ← vereinfacht
    │
    SOCEMPTY:
      pwr = min(dev_start, -POWER_START) - offgrid_load
      pwr = max(pwr, setpoint)           ← Surplus-Cap
      power_charge(pwr)
    SOCFULL:
      power_charge(-offgrid_load)
    ACTIVE:
      pwr = -POWER_START - offgrid_load
      power_charge(pwr)
```

### 4.3 Surplus-Cap

**Problem:** `pwr = -(POWER_IDLE_OFFSET + bypass_available + offgrid_load)` kann größer
sein als der verfügbare Solarüberschuss.

**Lösung:** In `_distribute_power()` wird der echte Überschuss vor dem Hardware-Cap gespeichert:

```python
dev_start    = _compute_wakeup_threshold(setpoint, optimal, is_charge, mgr)
raw_setpoint = setpoint  # echter Überschuss, vor Kappung auf Hardware-Limit
setpoint     = _cap_setpoint(setpoint, total_limit, is_charge, label)
...
await _wake_idle_devices(mgr, dev_start, is_charge, raw_setpoint)
```

Im Wakeup-Befehl:

```python
pwr = -(POWER_IDLE_OFFSET + bypass_available + offgrid_load)
if setpoint < 0:          # beide negativ (Laderichtung)
    pwr = max(pwr, setpoint)  # max zweier negativer Werte = kleinere Anforderung
```

| Situation           | `pwr` (vor Cap) | `setpoint` | `pwr` (nach Cap) |
|---------------------|-----------------|------------|------------------|
| Überschuss reicht   | −100 W          | −200 W     | −100 W (unverändert) |
| Überschuss zu klein | −156 W          | −100 W     | −100 W (gekappt)     |

---

## 5. Ramping: Soft-Start-Regelung

### 5.1 Ziel und Anwendungsbereiche

Ramping reduziert die Ladeleistung graduell auf 0 % und steigert sie langsam auf 100 %,
um Netz-Spikes und MATCHING-Oszillationen zu vermeiden.

Ramping wird **ausschließlich** in diesen drei Szenarien angewendet:

| Szenario | Bedingung | Wirkung |
|----------|-----------|---------|
| 1. Post-Wakeup | Direkt nach WAKEUP→CHARGE-Übergang | Soft-Start über `WAKEUP_RAMP_DURATION` Sekunden |
| 2. Entladen nahe minSoc | `soc ≤ minSoc + 2 × SOC_IDLE_BUFFER` | Faktor ∝ Abstand zu minSoc |
| 3. Laden nahe maxSoc | `soc ≥ maxSoc − 2 × SOC_IDLE_BUFFER` | Faktor ∝ Abstand zu maxSoc |

In allen anderen Situationen gilt `ramp_factor = 1.0` (volle Leistung, keine Drosselung).

### 5.2 Implementierung

**Neues Geräte-Attribut (`device.py`):**

```python
self.wakeup_entered: datetime = datetime.min
```

Wird gesetzt, wenn `update_power_flow_state()` den WAKEUP→CHARGE-Übergang erkennt:

```python
if self.power_flow_state == PowerFlowState.WAKEUP:
    if abs(self.batteryPort.power) <= SmartMode.POWER_START:
        return  # noch im WAKEUP, warte weiter
    self.wakeup_entered = datetime.now()  # Übergang erkannt → Ramp-Start
```

**Hilfsfunktion `_ramp_factor()` (`power_strategy.py`):**

```python
def _ramp_factor(d, soc, is_charge, time) -> float:
    # Szenario 1: Post-Wakeup
    if d.wakeup_entered != datetime.min:
        elapsed = (time - d.wakeup_entered).total_seconds()
        if elapsed < SmartMode.WAKEUP_RAMP_DURATION:
            return elapsed / SmartMode.WAKEUP_RAMP_DURATION  # 0.0 → 1.0 über 30 s
        d.wakeup_entered = datetime.min  # Ramp abgeschlossen

    # Szenario 2: Entladen nahe minSoc
    if not is_charge:
        min_limit = int(d.minSoc.asNumber) + SmartMode.SOC_IDLE_BUFFER
        if soc <= min_limit + SmartMode.SOC_IDLE_BUFFER:
            return max(0.0, (soc - int(d.minSoc.asNumber)) / (SOC_IDLE_BUFFER * 2))

    # Szenario 3: Laden nahe maxSoc
    else:
        max_soc = int(d.socSet.asNumber)
        if soc >= max_soc - SmartMode.SOC_IDLE_BUFFER * 2:
            return max(0.0, (max_soc - soc) / (SOC_IDLE_BUFFER * 2))

    return 1.0  # Normalbetrieb: keine Drosselung
```

**Anwendung in `_distribute_power()` (`power_strategy.py`):**

```python
# Soft-start ramp (post-wakeup, near minSoc/maxSoc boundaries)
if time is not None and pwr != 0:
    pwr = int(pwr * _ramp_factor(d, soc, is_charge, time))
```

Der Ramp-Faktor wird nach dem Clamping und Min-SoC-Block, aber vor `power_charge/discharge`
angewendet. `time` wird nur beim Charge-Pfad übergeben; der Discharge-Pfad
bekommt automatisch `time=None` solange er nicht explizit umgestellt wird.

### 5.3 Ramp-Verlauf Post-Wakeup (Beispiel: WAKEUP_RAMP_DURATION = 30 s)

```
t=0 s:  Wakeup erkannt  →  ramp_factor = 0.0  →  pwr = 0 W
t=6 s:  20 % Ramp       →  ramp_factor = 0.2  →  pwr = Setpoint × 20 %
t=15 s: 50 % Ramp       →  ramp_factor = 0.5  →  pwr = Setpoint × 50 %
t=30 s: 100 % Ramp      →  ramp_factor = 1.0  →  pwr = Setpoint (voll)
t>30 s: wakeup_entered = datetime.min (Ramp abgeschlossen, kein Overhead)
```

### 5.4 Neue Konstante

```python
SmartMode.WAKEUP_RAMP_DURATION = 30  # Sekunden Soft-Start nach WAKEUP→CHARGE
```

---

## 6. Neue Konstanten und Attribute im Überblick

| Name                    | Datei         | Typ / Wert | Bedeutung                                          |
|-------------------------|---------------|------------|----------------------------------------------------|
| `WAKEUP_RAMP_DURATION`  | `const.py`    | `30` (s)   | Dauer des Post-Wakeup-Soft-Starts                  |
| `wakeup_entered`        | `device.py`   | `datetime` | Zeitstempel WAKEUP→CHARGE-Übergang; `datetime.min` = inaktiv |
| `raw_setpoint`          | `power_strategy.py` | `int` (W) | Überschuss vor Hardware-Cap; lokale Variable       |
| `pass1_woken`           | `power_strategy.py` | `set`  | Geräte, die Pass 1 bereits geweckt hat (pro Aufruf)|

---

## 7. Regelwerk für den idealen Wakeup

| Nr. | Regel | Implementiert in |
|-----|-------|-----------------|
| 1 | Beginne Wakeup nur wenn `bypass.is_active` (Pass 1) oder Überschuss vorhanden (Pass 2) | `_need_wakeup()`, `dev_start < 0` |
| 2 | Wakeup-Befehl muss Bypass-Leistung einberechnen (`POWER_IDLE_OFFSET + bypass + offgrid`) | Pass 1 Formel |
| 3 | WAKEUP-Zustand bleibt bis `abs(batteryPort.power) > POWER_START` | `update_power_flow_state()` |
| 4 | Rampe nur in drei definierten Übergangssituationen | `_ramp_factor()` |
| 5 | Kein Entladen von SOCEMPTY-Geräten | Pass 2 Discharge-Pfad |
| 6 | Cooldown zwischen Bypass-Wake-Versuchen: `BYPASS_WAKE_COOLDOWN = 60 s` | Pass 1 Cooldown |
| 7 | Wakeup-Befehl darf verfügbaren Überschuss nicht überschreiten | Surplus-Cap (`max(pwr, setpoint)`) |
| 8 | Jedes Gerät darf pro Iteration nur einmal geweckt werden | `pass1_woken` Guard |

---

## 8. Timing-Referenz (SF2400 AC, validiert)

| Ereignis | Zeitdauer | Quelle |
|----------|-----------|--------|
| MQTT-Befehl bis `inputLimit` sichtbar | ~4 s | Log-Analyse |
| `inputLimit` gesetzt bis `packInputPower > 0` | 5–15 s | Hardware-Trägheit des Wechselrichters |
| WAKEUP → CHARGE (Code-Erkennung) | ~5–10 s nach echtem Ladestart | `abs(batPort.power) > 50 W` |
| SOCEMPTY-Sperre → aufgehoben | Sobald `electricLevel > minSoc` | Firmware |
| Gesamtdauer SOCEMPTY → stabiler Ladefluss | ~15–30 s | Log-Analyse |
| Post-Wakeup-Ramp bis voller Setpoint | 30 s (`WAKEUP_RAMP_DURATION`) | Code |