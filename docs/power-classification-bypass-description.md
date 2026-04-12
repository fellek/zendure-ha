# Bypass-Verhalten: BypassRelay und Bypass-Wakeup-Logik

## Was ist der Bypass?

Der Bypass bezeichnet den Zustand, in dem Strom durch das Gerät fließt,
**ohne vollständig über den Akku zu laufen**. Die Hardware meldet diesen Zustand
über das MQTT-Feld `pass`:

| Wert | Modus         | Bedeutung                                      |
|------|---------------|------------------------------------------------|
| 0    | off           | Kein Bypass — normaler Betrieb                 |
| 2    | reverse       | BYPASS-REVERSE — Strom fließt umgekehrt        |
| 3    | input         | BYPASS-INPUT — Eingang wird direkt durchgeleitet|

`bypass.is_active` ist `True` sobald `pass != 0` — d.h. immer dann, wenn
**per Definition Strom fließt, der nicht vollständig über den Akku geht**.

---

## BypassRelay (`bypass_relay.py`)

`BypassRelay` kapselt den `pass`-Kanal als typisiertes Objekt auf dem Gerät:

```python
device.bypass.is_active   # True wenn pass != 0
device.bypass.is_reverse  # True wenn pass == 2
device.bypass.is_input    # True wenn pass == 3
```

Zusätzlich wird ein Begleit-Sensor `pass_mode` erstellt (Enum: `off / reverse / input`),
der den Modus als lesbare HA-Entity darstellt.

`BypassRelay` ersetzt das frühere `self.byPass = ZendureBinarySensor(self, "pass")`
und fügt typisierte Properties sowie den Modus-Sensor hinzu.

---

## PowerFlowState und Bypass

`PowerFlowState.BYPASS` existiert **nicht mehr**. IDLE beschreibt ausschließlich
den **Batterie-Zustand** (Batterie lädt oder entlädt nicht) — unabhängig davon,
ob Bypass-Strom fließt.

Bypass-Zustand wird stattdessen direkt über `device.bypass.is_active` abgefragt,
wann immer die Strategie ihn benötigt.

---

## Bypass-Wakeup-Logik (`_need_wakeup` + Pass 1 in `_wake_idle_devices`)

### Wann braucht ein Bypass-Gerät einen Wakeup?

```
_need_wakeup(d):
    bypass.is_active?              Nein → kein Wakeup (kein Bypass-Strom)
    power_flow_state == IDLE?      Nein → Batterie ist bereits aktiv, kein Eingriff nötig
    state in {SOCEMPTY, SOCFULL}?  Nein → ACTIVE-Geräte regeln sich selbst
    → True
```

**Begründung:** `bypass.is_active` impliziert bereits, dass Energie fließt —
eine separate Prüfung auf `solar > 0` oder `offgrid_feed_in > 0` ist redundant.
`power_flow_state == IDLE` stellt sicher, dass die Batterie noch nicht auf den
Bypass-Strom reagiert hat.

### Warum SOCEMPTY **und** SOCFULL?

| Zustand  | Bypass aktiv + IDLE bedeutet…                          | Wakeup-Richtung   |
|----------|--------------------------------------------------------|-------------------|
| SOCEMPTY | Strom fließt durch das Gerät, Akku lädt nicht          | **Laden**         |
| SOCFULL  | Strom fließt durch das Gerät, Akku entlädt nicht       | **Entladen**      |

---

## AC-Port-Offset: Warum POWER_START nicht ausreicht

Der Setpoint (`power_charge` / `power_discharge`) wird **am AC-Eingang des Geräts**
gemessen, nicht am Akku. Der Bypass-Strom fließt ebenfalls durch diesen AC-Port und
überlagert das Startsignal.

```
AC-Port-Messung = Batterieleistung ± Bypass-Leistung
```

Damit der Akku netto `POWER_START` bekommt, muss der Setpoint die Bypass-Leistung
zusätzlich enthalten:

### SOCEMPTY — Laden

```
bypass_available = solar + offgrid_feed_in
pwr = -(POWER_IDLE_OFFSET + bypass_available + offgrid_load)
pwr = max(pwr, raw_setpoint)   ← nie mehr als verfügbarer Überschuss
→ power_charge(pwr)
→ power_flow_state = WAKEUP
→ pass1_woken.add(d)
```

| Beispiel            | Wert     | Notiz                                   |
|---------------------|----------|-----------------------------------------|
| solar               | 150 W    |                                         |
| offgrid_feed_in     | 0 W      |                                         |
| offgrid_load        | 50 W     |                                         |
| bypass_available    | 150 W    |                                         |
| POWER_IDLE_OFFSET   | 10 W     |                                         |
| Setpoint (roh)      | −250 W   | vor Surplus-Cap                         |
| raw_setpoint        | −200 W   | verfügbarer Überschuss                  |
| Setpoint (gekappt)  | −200 W   | `max(−250, −200) = −200`                |
| Batterie netto      | **≥50 W** Ladung ✓ | AC-Offset kompensiert          |

### SOCFULL — Entladen

```
pwr = POWER_IDLE_OFFSET + bypass_available - offgrid_load
→ power_discharge(pwr)
→ power_flow_state = WAKEUP
→ pass1_woken.add(d)
```

| Beispiel            | Wert  |
|---------------------|-------|
| solar               | 150 W |
| offgrid_feed_in     | 0 W   |
| offgrid_load        | 50 W  |
| bypass_available    | 150 W |
| POWER_IDLE_OFFSET   | 10 W  |
| Setpoint            | 110 W |
| Batterie netto      | **≥10 W** Entladung ✓ |

---

## Schutz gegen Doppel-Wakeup (`pass1_woken`)

Ohne Schutz können Pass 1 und Pass 2 dasselbe Gerät in einer Iteration wecken.
Das führte zu widersprüchlichen Befehlen und überhöhten Leistungsanforderungen.

**Lösung:** Pass 1 trägt jedes geweckte Gerät in `pass1_woken` ein.
Pass 2 überspringt alle Geräte in dieser Menge:

```python
pass1_woken: set = set()  # lokal pro _wake_idle_devices()-Aufruf

# Pass 1:
pass1_woken.add(d)  # nach power_charge/discharge

# Pass 2:
if d in pass1_woken:
    continue  # bereits von Pass 1 behandelt
```

---

## Einordnung in den Gesamtablauf

```
_wake_idle_devices(mgr, dev_start, is_charge, setpoint=raw_setpoint):
│
├── pass1_woken = set()
│
├── PASS 1: Bypass-Wakeup (läuft immer, unabhängig von dev_start)
│   für jedes Gerät in mgr.idle:
│   _need_wakeup(d)?  Nein → überspringen
│   Cooldown aktiv?   Ja   → überspringen
│   │
│   SOCEMPTY → pwr = -(POWER_IDLE_OFFSET + bypass + offgrid_load)
│              pwr = max(pwr, setpoint)   ← Surplus-Cap
│              power_charge(pwr) + WAKEUP + pass1_woken.add(d)
│   SOCFULL  → pwr = POWER_IDLE_OFFSET + bypass - offgrid_load
│              power_discharge(pwr) + WAKEUP + pass1_woken.add(d)
│
├── dev_start < 0?  Nein → return
│
└── PASS 2: Grid-Demand-Wakeup (nur wenn dev_start < 0)
    IDLE-Geräte nach SoC ↓ sortiert:
    d in pass1_woken?          Ja → überspringen   ← NEU
    power_flow_state == WAKEUP? Ja → überspringen
    │
    SOCEMPTY → pwr = min(dev_start, -POWER_START) - offgrid_load
               pwr = max(pwr, setpoint)           ← Surplus-Cap
               power_charge(pwr) + WAKEUP
    SOCFULL  → power_charge(-offgrid_load)
    ACTIVE   → power_charge(-POWER_START - offgrid_load)
```

Pass 1 läuft **immer** (unabhängig von dev_start), weil Bypass-Energie lokal
vorhanden ist und nicht durch den Netzanschluss begrenzt wird.
Pass 2 läuft nur wenn das Netz einen Überschuss meldet (`dev_start < 0`).
