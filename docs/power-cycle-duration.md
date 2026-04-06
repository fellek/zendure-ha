# Zyklusdauer der Leistungssteuerung

Beschreibt die Zeitspannen vom Eingang eines neuen P1-Messwerts bis zur gesendeten Steuerreaktion und bis zum messbaren Effekt.

---

## Phase 1 — P1-Wert → Befehl gesendet (Reaktionszeit)

| Schritt | Dauer | Quelle |
|---|---|---|
| Smartmeter sendet neuen Zustand | ~1 s (DSMR-Takt) | HA state machine |
| `_p1_changed` feuert | ~0 ms | `async_track_state_change_event` |
| `zero_fast`-Block aktiv | bis zu **2,2 s** | `SmartMode.TIMEFAST` |
| Kein `isFast` + `zero_next` noch nicht abgelaufen | bis zu **4 s** | `SmartMode.TIMEZERO` |
| `classify_and_dispatch` + MQTT-/HTTP-Befehl | ~0 ms (LAN) | Event-Loop |

**Minimale Reaktionszeit: 2,2 s** — `TIMEFAST` ist ein absoluter Floor, der auch bei extremen P1-Sprüngen (`isFast=True`) nicht unterschritten werden kann.

**Typische Reaktionszeit: 4–5 s** — `TIMEZERO` + Smartmeter-Latenz bei normalem Betrieb.

> **Hinweis:** `P1_MIN_UPDATE = 400 ms` ist in `const.py` definiert, wird im aktuellen Code jedoch nicht ausgewertet — es gibt keinen entsprechenden Check in `_p1_changed`.

---

## Phase 2 — Befehl gesendet → Effekt messbar

| Schritt | Dauer | Quelle |
|---|---|---|
| HTTP-POST / MQTT-Publish an Gerät | ~1–5 ms (LAN) | `doCommand` |
| Gerät verarbeitet Befehl (Firmware) | variabel | Gerätefirmware |
| Gerät sendet `properties/report` zurück | **~5 s** (fester Zyklus) | MQTT vom Gerät |
| Nächstes P1-Event → `classify` liest neue Werte | bis zu **4 s** | `TIMEZERO` |

Aus dem Log konkret: Befehl bei `12:48:57.760`, MQTT-Report mit `inputLimit: 149` bei `12:49:01.308` → **3,5 s Geräteantwort**.

---

## Gesamtzyklus

```
P1-Änderung eingetroffen
        │
        ├── zero_fast-Block         min. 2,2 s   (TIMEFAST)
        ├── Smartmeter-Takt              ~1 s
        ├── zero_next (Normalfall)    bis 4 s   (TIMEZERO)
        │
        ▼
Befehl gesendet                ◄── Reaktionszeit: 2,2–5 s
        │
        ├── Gerät antwortet          ~3–5 s   (MQTT-Reporting-Zyklus)
        ├── Nächster classify-Lauf  bis 4 s   (TIMEZERO)
        │
        ▼
Effekt in Integration messbar  ◄── +5–9 s nach Befehl
```

| Szenario | Reaktionszeit | Zeit bis Effekt messbar | Gesamt |
|---|---|---|---|
| **Minimum** (isFast, Gerät antwortet sofort) | 2,2 s | 3,5 s | **~6 s** |
| **Typisch** (Normalzyklus) | 4–5 s | 5 s | **~9 s** |
| **Maximum** (Gerät hat gerade reportet) | 5 s | 9 s | **~14 s** |