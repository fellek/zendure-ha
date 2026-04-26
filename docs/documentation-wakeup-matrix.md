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

Konkrete Log-Sequenz die alle Probleme aus §7 illustriert:

| Zeit | Ereignis | Ursache |
|------|----------|---------|
| 19:26:02–07 | Dispatch: setpoint 32/38 W → Filter: < 50 W → 0 → kein Befehl | `_last_direction` bleibt CHARGE (veraltet) |
| **19:26:12** | Cold-start: `power_discharge(64)` — bypasses Filter ✓ | Filter sieht aber CHARGE→DISCHARGE → **Cooldown 20 s** bis 19:26:32 |
| 19:26:15 | MQTT: packState=0 → `WAKEUP → IDLE` | SF2400 AC Hardware: meldet kurz Standby während DC-Link hochfährt |
| 19:26:16 | MQTT: acMode=2, outputLimit=64, smartMode=1 — Befehl akzeptiert | — |
| 19:26:20 | MQTT: packState=2, is_discharging → `IDLE → DISCHARGE` | `wakeup_committed` bleibt false (prev=IDLE, nicht WAKEUP) |
| **19:26:24** | Filter: Cooldown noch aktiv (12 s < 20 s) → 0 → **`power_discharge(0)`** | Problem 1+2: kein Reset, Filter stoppt laufendes Gerät |
| 19:26:27 | `DISCHARGE → WAKEUP` — AC-Ausgang aus, DC-Link aktiv | — |
| 19:26:30–40 | **28 W Inverter-Eigenverbrauch** aus Batterie, kein Netzausgang | Inverter fährt herunter (outputPackPower=28, acStatus=0) |
| 19:26:42 | Zweiter Cold-start: `power_discharge(69)` — Cooldown abgelaufen ✓ | 30 s nach Cooldown-Ende → Filter passiert normal |
| 19:26:54 | Stabiler Betrieb 75–86 W | — |