# Wakeup-Matrix

Vollständige Übersicht aller Wakeup-Pfade, Bedingungen, Power-Parameter, State-Effekte und Hysterese-Interaktionen.

---

## 1. Trigger-Quellen

Es gibt **4 unabhängige Wakeup-Pfade**, alle in `power_strategy.py`:

| # | Pfad | Aufgerufen von | Richtung |
|---|------|----------------|----------|
| A | Cold-start Discharge | `distribute_discharge()` | Entladen |
| B | Cold-start Charge | `distribute_charge()` | Laden |
| C | Bypass-Wake (Pass 1) | `_wake_idle_devices()` | Laden |
| D | Grid-demand-Wake (Pass 2) | `_wake_idle_devices()` | Laden + Entladen |

---

## 2. Bedingungen pro Pfad

### Pfad A — Cold-start Discharge

| Bedingung | Prüfung | Wert |
|-----------|---------|------|
| Kein aktives Discharge-Gerät | `not mgr.discharge` | true |
| Mind. 1 Idle-Gerät | `mgr.idle` | nicht leer |
| Setpoint groß genug | `setpoint > POWER_START` | > 50 W |
| Gerät in Standby oder SOCFULL | `packState == 0 or state == SOCFULL` | — |
| SoC nicht zu niedrig | `electricLevel > minSoc + DISCHARGE_SOC_BUFFER` | > minSoc+2% |
| Gerät nicht SOCEMPTY | `state != SOCEMPTY` | — |

**Überspringen wenn:** `packState != 0 and state != SOCFULL` (Gerät bereits aktiv aber nicht dischargend)

### Pfad B — Cold-start Charge

| Bedingung | Prüfung | Wert |
|-----------|---------|------|
| Kein aktives Charge-/promoted-Gerät | `not active_devices` | true |
| Mind. 1 Idle-Gerät | `mgr.idle` | nicht leer |
| Setpoint groß genug | `setpoint < -POWER_START` | < -50 W |
| Gerät in Standby | `packState == 0` | — |
| Gerät nicht SOCFULL | `state != SOCFULL` | — |

### Pfad C — Bypass-Wake (Pass 1, nur Laden)

| Bedingung | Prüfung | Wert |
|-----------|---------|------|
| Bypass aktiv | `d.bypass.is_active` | true |
| Gerät IDLE | `power_flow_state == IDLE` | — |
| Grenzwert-SoC | `state in (SOCEMPTY, SOCFULL)` | — |
| Cooldown abgelaufen | `now > wake_started_at + BYPASS_WAKE_COOLDOWN` | > 60 s |

### Pfad D — Grid-demand-Wake (Pass 2)

| Bedingung | Prüfung | Wert |
|-----------|---------|------|
| Charge: Kapazitätsbedarf | `dev_start < 0` — `setpoint - optimal * 2 < 0` nach aktiven Geräten | — |
| Discharge: Kapazitätsbedarf | `dev_start > 0` — `setpoint - optimal * 2 - discharge_produced > 0` | — |
| Gerät nicht WAKEUP | (außer MANUAL-Modus) | — |
| Discharge: SoC nicht zu niedrig | `electricLevel > minSoc + DISCHARGE_SOC_BUFFER` | — |
| Discharge: nicht SOCEMPTY | `state != SOCEMPTY` | — |

---

## 3. Power-Parameter pro Pfad

| Pfad | Formel | Beispiel SF2400 (setpoint=64 W, limit=1800 W) |
|------|--------|-----------------------------------------------|
| **A** Cold-start Dis | `min(max(setpoint, POWER_START), discharge_limit)` | `min(max(64, 50), 1800) = 64 W` |
| **B** Cold-start Chg | `max(min(setpoint, -POWER_START), charge_limit)` | negativ, z.B. −64 W |
| **C** Bypass SOCEMPTY | `-(POWER_IDLE_OFFSET + connectorPort.power)` | −10 W − aktuelle Last |
| **C** Bypass SOCFULL | `+(POWER_IDLE_OFFSET + connectorPort.power)` | +10 W + aktuelle Last |
| **D** Dis Pass 2 | Fest: `POWER_IDLE_OFFSET` | **immer 10 W** |
| **D** Chg Pass 2 normal | `-(POWER_START + offgrid_load)` | −50 W |
| **D** Chg Pass 2 SOCEMPTY | `min(dev_start, -POWER_START) − offgrid_load` | variabel |

---

## 4. State-Effekte im Gerät

| Aktion | `power_flow_state` | `wake_started_at` | `wakeup_entered` | `wakeup_committed` |
|--------|--------------------|-------------------|------------------|--------------------|
| Cold-start sendet Befehl | IDLE → **WAKEUP** (gesetzt in Strategy) | = `time` | = `time` | false |
| packState=0 nach Wakeup | → **IDLE** (classify: packState=0 immer IDLE) | unverändert | unverändert | **false** ← Lücke! |
| packState=2, `is_discharging` | → **DISCHARGE** | = `datetime.min` (gelöscht in classify) | unverändert | **false** ← Sunset-Bug |
| packState=2, WAKEUP-Timeout | → **IDLE** (nach 15 s) | unverändert | unverändert | false |
| **Direkter** WAKEUP→DISCHARGE | → **DISCHARGE** | gelöscht | unverändert | **true** ✓ |
| WAKEUP→CHARGE direkt | → **CHARGE** | gelöscht | unverändert | **true** ✓ |

---

## 5. Hysterese-Rolle im Wakeup

Die Hysterese interagiert an **zwei Stellen** mit dem Wakeup:

```
         Cold-start          _distribute_power
         (außerhalb Filter)  (innerhalb Filter)
              │                     │
      power_discharge(N)     filter(setpoint) ──► 0  wenn Cooldown aktiv
              │                     │
         Device wacht auf     ◄────────────────────  power_discharge(0)
                                                      ← BUG bei Cooldown!
```

| Aspekt | Verhalten | Problem |
|--------|-----------|---------|
| **Cold-start bypasses Filter** | `power_discharge(wake_power)` wird direkt ohne Hysterese-Check gesendet | — korrekt so |
| **Filter läuft nach Cold-start** | Sobald Gerät in `mgr.discharge` erscheint, läuft `_distribute_power` mit Filter | Filter kann 0 zurückgeben → stoppt laufendes Gerät |
| **wakeup_committed Reset** | Wenn WAKEUP→DISCHARGE direkt: `hysteresis.reset()` → Cooldown gelöscht | funktioniert nur beim Direktpfad |
| **WAKEUP→IDLE→DISCHARGE** (SF2400) | `wakeup_committed` nie gesetzt → kein Reset → Filter gibt 0 → `power_discharge(0)` | **Sunset-Bug** |
| **POWER_START-Deadband** | `abs(setpoint) < 50 W` → Filter gibt 0 zurück, ohne `_last_direction` zu setzen | Beim Wiederanlauf nach kleinem Setpoint: Direction-State veraltet |
| **Cooldown-Länge** | SLOW (20 s) oder FAST (5 s) je nach Gap zur letzten Non-Idle-Zeit | Wenn letzter Charge-Dispatch < 5 Min her → 20 s Cooldown während Device entlädt |

---

## 6. Relevante Konstanten

| Konstante | Wert | Bedeutung |
|-----------|------|-----------|
| `POWER_START` | 50 W | Mindest-Setpoint für Cold-Start-Trigger und Filter-Deadband |
| `POWER_IDLE_OFFSET` | 10 W | Keepalive-Wert bei Pass-2-Discharge-Wake |
| `WAKE_TIMEOUT` | 15 s | Max. Zeit in WAKEUP bevor Rückfall zu IDLE |
| `BYPASS_WAKE_COOLDOWN` | 60 s | Mindest-Abstand zwischen Bypass-Wake-Befehlen |
| `WAKEUP_CAPACITY_FACTOR` | 2 | Faktor für `dev_start` (ab wann idle Gerät geweckt wird) |
| `DISCHARGE_SOC_BUFFER` | 2 % | SoC-Puffer über minSoc — Entladung gesperrt darunter |
| `HYSTERESIS_SLOW_COOLDOWN` | 20 s | Cooldown nach Richtungswechsel (< 5 Min seit letztem) |
| `HYSTERESIS_FAST_COOLDOWN` | 5 s | Cooldown nach Richtungswechsel (> 5 Min seit letztem) |
| `HYSTERESIS_LONG_COOLDOWN` | 300 s | Schwelle zwischen fast und slow |

---

## 7. Bekannte Probleme

| # | Problem | Datei | Auswirkung |
|---|---------|-------|------------|
| 1 | WAKEUP→IDLE→DISCHARGE setzt `wakeup_committed` nicht | `device_components.py:update()` | Hysterese-Cooldown stoppt laufendes Gerät (Sunset-Bug) |
| 2 | `_distribute_power` sendet `power_discharge(0)` wenn Filter=0 und Discharge-Liste nicht leer | `power_strategy.py:_distribute_power` | Laufendes Gerät wird abgewürgt |
| 3 | Pass-2-Discharge-Wake sendet immer nur 10 W | `power_strategy.py:_wake_idle_devices` | Gerät überschreitet ggf. nicht die `is_discharging`-Schwelle |
| 4 | Cold-start setzt WAKEUP nur wenn `power_flow_state == IDLE` | `power_strategy.py:distribute_discharge:590` | Kein Re-Wake wenn Gerät bereits in WAKEUP steckt |
| 5 | `wake_started_at` wird in `classify()` gelöscht bevor `update()` die Transition auswertet | `device_components.py:classify()` | Fix für Problem 1 erfordert `had_wake`-Sicherung vor `classify()`-Aufruf |
| 6 | Hysterese-Filter und Cold-start-Entscheidung sind entkoppelt | `power_strategy.py` | Cold-start feuert, aber direkt danach läuft der Filter gegen den Wakeup |

---

## 8. Sunset-Bug — Detailablauf (SF2400 AC, 2026-04-26 19:26)

### Analyse 

Konkrete Log-Sequenz die alle Probleme aus §7 illustriert:
Erste Zeile: alle Werte, danach nur Änderungen.

Relevante Felder: `packState` · `acMode` · `acStatus` · `outputLimit` · `outputHomePower` · `outputPackPower` · `smartMode` · `pack.state` · `pack.batcur`

---

| Zeit | Ereignis | MQTT-Werte (erste Zeile: alle; dann: Δ Änderungen) |
|------|----------|----------------------------------------------------|
| 19:26:00 | Erste MQTT-Meldung (msg 64683) | `packState=0` `acMode=1` `acStatus=0` `outputLimit=0` `outputHomePower=0` `outputPackPower=0` `smartMode=0` · `pack.state=0` `pack.batcur=0` |
| 19:26:02 | Dispatch: classify=IDLE, setpoint=32W → Filter: 32<50W → 0 → kein Befehl | — |
| 19:26:05 | MQTT (msg 64685) | Keine Änderung |
| 19:26:07 | Dispatch: classify=IDLE, setpoint=38W → Filter: 38<50W → 0 → kein Befehl | — |
| 19:26:10 | MQTT (msg 64687) | Keine Änderung |
| **19:26:12** | **Cold-Start: `power_discharge(64)` gesendet. Hysterese: CHARGE→DISCHARGE → Cooldown 20s (bis 19:26:32). `power_flow_state=WAKEUP`** | — |
| 19:26:15 | MQTT (msg 64688, packNum only) + **State: `WAKEUP → IDLE`** (pack=0, ac=1) | — |
| 19:26:16 | MQTT (msg 64689) — Gerät akzeptiert Befehl | Δ `acMode=2` `outputLimit=64` `smartMode=1` |
| 19:26:18 | Dispatch: classify=IDLE (packState noch 0!), setpoint=11W → Filter: 11<50W → 0 → kein Befehl | — |
| **19:26:20** | MQTT (msg 64691) + **State: `IDLE → DISCHARGE`** (wakeup_committed bleibt False!) | Δ `packState=2` `acStatus=1` `outputHomePower=63` `outputPackPower=0` `smartMode=1` · `pack.state=2` `pack.batcur=65525` |
| **19:26:24** | **Dispatch: classify=DISCHARGE(feedIn=64), setpoint=59W → Filter: Cooldown aktiv (noch 8s) → 0 → `power_discharge(0)` gesendet ← BUG** | — |
| 19:26:25 | MQTT (msg 64693) — kurz höhere Leistung vor Stopp | Δ `pack.batcur=65515` `pack.power=104` |
| **19:26:27** | **State: `DISCHARGE → WAKEUP`** — Gerät stoppt AC-Ausgang | — |
| **19:26:30** | **MQTT (msg 64695) — 28W Inverter-Eigenverbrauch** | Δ `outputHomePower=0` `outputPackPower=28` `acStatus=0` `outputLimit=0` `smartMode=0` · `pack.batcur=65534` `pack.power=9` |
| 19:26:32–37 | Dispatches: classify=WAKEUP (idle=1), cold-start: skip weil packState=2 → kein Befehl | — |
| 19:26:35 | MQTT (msg 64697) | Δ `pack.power=4` `pack.batcur=65535` |
| 19:26:40 | MQTT (msg 64699) — DC-Link schaltet ab | Δ `outputPackPower=0` `dcStatus=0` |
| **19:26:42** | **State: `WAKEUP → IDLE`** (packState→0). Dispatch: classify=IDLE, setpoint=69W → **Cold-Start**, `power_discharge(69)` | — |
| 19:26:45 | MQTT (msg 64701) — zweiter Anlauf, Befehl akzeptiert | Δ `packState=0` `outputLimit=69` `smartMode=1` · `pack.state=0` `pack.batcur=0` |
| **19:26:47** | **State: `WAKEUP → IDLE`** (packState=0 nochmals). Dispatch: cold-start, `power_discharge(76)` | — |
| **19:26:50** | MQTT (msg 64703) + **State: `IDLE → DISCHARGE`** | Δ `packState=2` `acStatus=1` `outputLimit=76` `outputHomePower=76` · `pack.state=2` `pack.batcur=65517` |
| **19:26:54** | **Dispatch: classify=DISCHARGE(feedIn=75), setpoint=76W → Filter: Cooldown abgelaufen (42s nach 19:26:12) → passiert → no action (Gerät liefert bereits 75W)** | — |
| 19:27:00+ | Stabiler Betrieb, Feinregelung 75–86W | — |

---

**Auffälligkeiten in den Rohdaten:**

- `pack.batcur=65525` ≈ −11 (16-bit-Komplement, Entladestrom in ~0.1A-Einheit), steigt mit der Last
- Das Gerät meldet nach 19:26:16 schon `acMode=2, outputLimit=64` (Befehl angekommen), aber `packState` bleibt noch bis 19:26:20 auf 0 — das ist die SF2400 AC Hardware-Eigenheit die den WAKEUP→IDLE→DISCHARGE-Pfad erzwingt
- `outputPackPower=28` bei `outputHomePower=0` und `acStatus=0` (19:26:30–40) ist der reine Wechselrichter-Eigenverbrauch — keine Nutzleistung
---
### Fazit
Mqtt-Werte `acMode=2` und `outputLimit=64` sind die Bestätigung der Firmware, dass die gewünschten Werte angenommen sind und verarbeitet werden. 
Diese Information soll in der Wakeup Routine berücksichtigt werden. 
Wenn Lade-/Entladerichtung bestätigt sind, ist der `packState` und die Leistung am Akku mit verzögerung zu erwarten.
Ein STOP darf in dieser Situation nicht gesendet werden.

## Prüfen
1. Welche Wakeup und Cold-Wakeup-Pfade doppeln oder überlagern sich?
2. Welche Bedingungen sind doppelt?
3. Wo können Aufgaben zusammengefasst werden?

## Ergänzen
1. POWER_START bekommt in https://github.com/Zendure/Zendure-HA/pull/1288/changes/f787a6a38c3bd23e07bcad7c7e64cd3859b9579e zusätzlich eine randomisierte Zahl zwischen 0-10 addiert.   