# Power Control Flow

Beschreibt den vollständigen Ablauf von einem eingehenden P1-Messwert bis zur tatsächlichen Geräteansteuerung.

---

## Überblick

```
MQTT-Event (P1-Wert)
        │
        ▼
  [1] Throttle-Gate (zero_fast / zero_next)
        │
        ▼
  [2] Klassifizierung der Geräte
        │
        ▼
  [3] FuseGroup-Limits berechnen
        │
        ▼
  [4] Dispatch zum aktiven Modus
        │
        ▼
  [5] Charge / Discharge Verteilung
        │
        ▼
  [6] Wake-up Idle-Geräte
        │
        ▼
  MQTT-Befehl an Gerät (inputLimit / outputLimit)
```

```aiignore
MQTT-Event (P1-Wert eingetroffen)
       │
       ▼         
time < zero_fast?  ──Yes──▶  nur p1_history aktualisieren, RETURN
       │No
       ▼
Standardabweichung berechnen → isFast?   
       │
isFast OR time > zero_next?
       │Yes
       ▼
powerChanged() ausführen
  → zero_fast = now + TIMEFAST (2.2s)
  → zero_next = now + TIMEZERO (4s)

zero_fast ist ein Hard-Blackout: Jedes P1-Event innerhalb der ersten 2,2 Sekunden nach einer Verteilung wird komplett ignoriert — kein isFast-Check, kein Setpoint, nichts. Nur p1_history.append().
zero_next ist der reguläre Takt: Nach 4 Sekunden läuft eine neue Verteilung, unabhängig ob sich etwas geändert hat.
isFast kann zero_next überspringen (Zeile 514: if isFast or time > zero_next), aber nicht zero_fast. Das heißt: selbst bei extremen P1-Sprüngen gibt es immer mindestens 2,2 Sekunden Pause zwischen zwei Verteilungsläufen.
Fazit: TIMEFAST bremst nicht den Input neuer Werte (MQTT läuft weiter), aber er bremst die Reaktion darauf — kein
powerChanged-Aufruf öfter als alle 2,2 Sekunden. TIMEZERO ist die maximale Wartezeit ohne Reaktion.
```

---

## [1] Throttle-Gate — `manager.py`

Jedes eingehende P1-Event durchläuft zuerst zwei Zeitfilter:

```python
# Hard-Blackout: kein powerChanged innerhalb von TIMEFAST Sekunden nach letztem Lauf
if time < self.zero_fast:
    self.p1_history.append(p1)
    return                      # Event wird verworfen

# Standardabweichung → isFast?
isFast = abs(p1 - avg) > stddev or abs(p1 - p1_history[0]) > stddev

# Normaler Takt oder signifikante Änderung
if isFast or time > self.zero_next:
    await self.powerChanged(p1, isFast, time)
    self.zero_next = now + TIMEZERO   # 4 s
    self.zero_fast = now + TIMEFAST   # 2.2 s
```

| Variable | Wert | Bedeutung |
|---|---|---|
| `TIMEFAST` | 2,2 s | Mindestabstand zwischen zwei Verteildurchläufen (Hard-Block) |
| `TIMEZERO` | 4 s | Maximale Wartezeit ohne Reaktion (regulärer Takt) |
| `P1_STDDEV_FACTOR` | 3,5 | Multiplikator für Standardabweichung zur isFast-Erkennung |
| `P1_STDDEV_MIN` | 15 W | Mindest-Stddev damit ein P1-Sprung als signifikant gilt |
| `P1_MIN_UPDATE` | 400 ms | Mindestabstand zwischen zwei P1-Events (vor dem Gate) |

**Wichtig:** `zero_fast` ist ein absoluter Block — auch ein extremer P1-Sprung löst innerhalb dieser 2,2 Sekunden keinen neuen Lauf aus. `isFast=True` kann nur `zero_next` überspringen, nicht `zero_fast`.

---

## [2] Klassifizierung — `_classify_devices()` / `_classify_single_device()`

Jedes Gerät wird anhand seiner aktuellen Messwerte in eine der drei Listen eingeordnet:

| Liste | Bedingung |
|---|---|
| `mgr.charge` | `homeInput > 0` und `homeInput > offgrid_power` (Gerät zieht Netzstrom) |
| `mgr.discharge` | `homeOutput > 0` oder `offgrid_power > 0` (Gerät gibt Strom ab) |
| `mgr.idle` | weder noch — oder SOCEMPTY ohne jede Aktivität |

### SOCEMPTY-Sonderfall

Ein Gerät in `DeviceState.SOCEMPTY` landet **nur dann** in `idle`, wenn es wirklich inaktiv ist:

```python
if d.state == DeviceState.SOCEMPTY \
        and d.homeInput.asInt == 0 \
        and d.batteryInput.asInt == 0 \
        and d.homeOutput.asInt == 0:
    mgr.idle.append(d)
    return 0, 0
```

Zieht das Gerät bereits Netzstrom (`homeInput > 0`), fällt es durch zur normalen CHARGE-Klassifikation. Dies ist entscheidend, damit `charge_limit` gesetzt wird und der Setpoint nicht auf 0 gecappt wird.

---

## [3] FuseGroup-Limits — `_update_group_limits()`

Nach der Klassifizierung werden die Hardware-Limits pro Gerät über die FuseGroup berechnet:

```python
mgr.charge_limit   = sum(d.pwr_max for d in mgr.charge)
mgr.charge_optimal = sum(d.charge_optimal for d in mgr.charge)
```

Wenn `mgr.charge` leer ist, bleibt `charge_limit = 0` — das hat direkte Auswirkung auf den Cap in Schritt 5.

---

## [4] Dispatch — `_dispatch_to_mode()`

Der berechnete Setpoint wird je nach aktivem `ManagerMode` weitergeleitet:

| Modus | Verhalten |
|---|---|
| `MATCHING` | Setpoint < 0 → Laden; Setpoint > 0 → Entladen |
| `MATCHING_CHARGE` / `STORE_SOLAR` | Immer laden, außer bei Solarüberschuss |
| `MATCHING_DISCHARGE` | Immer entladen |
| `MANUAL` | Setpoint aus `manualpower`-Entity; < 0 = Laden, > 0 = Entladen |
| `OFF` | Nichts |

---

## [5] Leistungsverteilung — `_distribute_power()`

```
Eingang: devices[], setpoint, direction

1. _compute_weights()         → total_limit, total_weight, optimal
2. apply_charge_cooldown()    → Hysterese-Cooldown (HYSTERESIS_FAST/SLOW_COOLDOWN)
3. _compute_wakeup_threshold()→ dev_start (Bedarf für Wake-up)
4. _cap_setpoint()            → Setpoint auf charge_limit/discharge_limit begrenzen
5. Verteilungsschleife        → gewichteter Anteil pro Gerät
6. _wake_idle_devices()       → Idle-Geräte aktivieren falls dev_start < 0
```

### Setpoint-Cap (Schritt 4)

```python
# Laden: setpoint darf nicht positiver sein als charge_limit (negativ)
capped = max(setpoint, total_limit)
```

Ist `charge_limit = 0` (kein Gerät in charge-Liste), wird jeder negative Setpoint auf 0 gecappt. Deshalb ist die korrekte Klassifizierung in Schritt 2 kritisch.

### Gewichtete Verteilung

Geräte mit niedrigerem SOC bekommen beim Laden einen größeren Anteil:

```
device_weight = pwr_max * (100 - SOC%)
pwr = setpoint * device_weight / total_weight
```

---

## [6] Wake-up Idle-Geräte — `_wake_idle_devices()`

Wenn `dev_start < 0` (Laden) oder `dev_start > 0` (Entladen) nach der Verteilung noch übrig bleibt, werden Idle-Geräte aktiviert:

```python
# Laden: Gerät mit niedrigstem SOC zuerst
await d.power_charge(-POWER_START - max(0, d.pwr_offgrid))
# POWER_START = 50 W + pwr_offgrid (Eigenverbrauch des Geräts)
```

Für Entladen werden SOCEMPTY-Geräte übersprungen.

---

## Hysterese-Cooldown

Verhindert schnelles An/Aus-Schalten beim Laden:

```
Letzter Lade-Stop < 5 Min her?
    Ja  → HYSTERESIS_SLOW_COOLDOWN = 60 s Wartezeit
    Nein → HYSTERESIS_FAST_COOLDOWN = 2 s Wartezeit
```

Während des Cooldowns wird `setpoint = 0` gesetzt — das Gerät empfängt keine Ladebefehle. Nach Ablauf gibt `apply_charge_cooldown()` den Setpoint wieder frei.

---

## Typischer Log-Ablauf

```
P1 ======> p1:-812 isFast:False, setpoint:0W stored:0W
Classify SolarFlow 2400 AC => CHARGE: homeInput=126 ...
Charge => setpoint -300W, devices=1
Charge: distributing setpoint=-300 across 1 devices, dev_start=0, weight=216000
Charge: [0/1] SolarFlow 2400 AC soc=10% pwr_max=-2400 ... final=-300
Power charge SolarFlow 2400 AC => -300
Set Manual power charging: isFast:False, setpoint:-300W stored:0W
```