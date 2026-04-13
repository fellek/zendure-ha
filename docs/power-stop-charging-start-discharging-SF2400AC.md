Dieses Log ist ein Meisterwerk! Es zeigt nicht nur, wie gut die Konstanten funktionieren, sondern deckt auch einen **gravierenden Logik-Bug** in der Klassifizierung auf, der die Konstanten fast torpediert hätte. 

Hier ist die detaillierte Analyse:

---

## Zeitliche Abschnitte

### Abschnitt 1: 23:04:41 – 23:04:52 (Stabile Ladephase)
*   **Situation:** Gerät lädt stabil mit 500W aus dem Netz (P1=721W).
*   **Aktive Konstanten:** Keine (alles im Gleichgewicht).

### Abschnitt 2: 23:04:52 – 23:04:55 (Modus-Wechsel & Hysterese-Start)
*   **Situation:** Du schaltest von `MANUAL` auf `MATCHING`. P1 fällt auf 247W (Überschuss).
*   **Ablauf:** Das System erkennt Ladung (`homeInput=501`), berechnet den Setpoint auf -254W und ruft `Charge` auf.
*   **Aktive Konstante:** `HYSTERESIS_FAST_COOLDOWN = 5`
*   **Log:** `Charge: hysteresis started, cooldown=5s, charge_time=2026-04-04 23:05:00`
*   **Bewertung Konstante:** ✅ **Perfekt.** Genau in dieser Sekunde schickt das System `Power charge => 0` an den Inverter. Die Hysterese verhindert, dass direkt -254W geschickt werden, während der Inverter noch am hochfahren ist.

### Abschnitt 3: 23:04:56 – 23:05:00 (Hardware-Trägheit beim Stopp)
*   **Situation:** Reaktion des SF2400 AC auf das `0W` Kommando.
*   **Ablauf:** 
    *   23:04:56: `gridInputPower` fällt von 501W auf 0W. `packInputPower` fällt auf 0W. Der Inverter stoppt sofort.
    *   **Anomalie:** `outputHomePower` springt auf 10W, `packState` wird 2 (INACTIVE). Das ist der "Parasitäre" Bypass, den `POWER_IDLE_OFFSET` abfangen soll.
*   **Bewertung:** Der Inverter zeigt, dass er mechanisch ca. 1-2 Sekunden braucht, um die Relais nach dem 0W-Befehl physisch zu trennen.

### Abschnitt 4: 23:05:00 – 23:05:18 (🚨 DER BUG: Die Drehstuhl-Situation)
*   **Situation:** Die Hysterese-Zeit ist um (23:05:00). Das System will neu berechnen.
*   **Was passiert:** 
    1.  23:05:00: Das Gerät meldet `homeInput=32` (es zieht noch 32W aus dem Netz für den Standby/Passthrough). Das System klassifiziert es als `CHARGE`. Aber P1 ist +309W. Das Ergebnis: Setpoint = +277W. 
    2.  Weil Setpoint > 0, ruft das System `Discharge => setpoint 277W` auf! Ein als "Ladend" klassifiziertes Gerät wird zum Entladen gezwungen.
    3.  Es schickt `Power discharge => 10` (wegen `POWER_IDLE_OFFSET`).
    4.  23:05:06 - 23:05:16: Das Gerät pendelt wild zwischen `pass: 3` (Passthrough) und `outputHomePower: 9`. Es weiß nicht, ob es laden oder entladen soll.
*   **Aktive Konstante:** `POWER_IDLE_OFFSET = 10`
*   **Bewertung Konstante:** 🛟 **Rettungsanker.** Der Offset von 10W verhindert hier, dass das System `Power discharge => 0` schickt. Würde 0W geschickt, würde der SF2400 AC wahrscheinlich komplett in den Standby gehen und die 128W Hauslast (die er gerade durchleitet) sofort auf das Netz werfen (Netz-Spike). Die 10W zwingen ihn in einen sicheren "Halte-Zustand".
*   **Bewertung Code:** ❌ **Klassifizierungs-Bug.** Das System sieht `homeInput > 0` und denkt "Ah, er lädt!". Aber der Akku bekommt keinen Strom (`batteryInput = 0`). Es ist reiner Passthrough/Standby. Die Logik muss hier zwischen "Akku lädt" und "Inverter zieht Standby-Passthrough" unterscheiden.

### Abschnitt 5: 23:05:18 – 23:05:32 (Ramp-Up Entladung)
*   **Situation:** P1 fällt auf 214W. Das Gerät meldet plötzlich `homeOutput=9` (Wechselrichter hat intern den Modus gewechselt).
*   **Ablauf:** Das System klassifiziert korrekt als `DISCHARGE` und schickt 223W. Das Gerät rampt hoch (222W -> 231W).
*   **Aktive Konstante:** `POWER_TOLERANCE = 5`
*   **Log:** 23:05:27: Setpoint 226W, Ist 231W -> `no action` (Diff = 5W). 
*   **Bewertung Konstante:** ✅ **Perfekt.** Verhindert, dass während des Hochfahrens ständig neue MQTT-Befehle gesendet werden.

### Abschnitt 6: 23:05:42 – 23:06:27 (Stabiler Entlade-Betrieb)
*   **Situation:** Gerät entlädt stabil mit 223-224W. P1 pendelt extrem stark um die 0 (zwischen -6W und +4W).
*   **Ablauf:** Das System berechnet Setpoints zwischen 220W und 228W. 
*   **Log:** In diesem gesamten Abschnitt steht **nur noch** `no action [power XXX]`.
*   **Bewertung Konstante:** ✅ **Absolut perfekt.** Ohne `POWER_TOLERANCE = 5` hätte das System hier alle 5 Sekunden versucht, die 3-4 Watt Schwankung auszugleichen. Der SF2400 AC wäre mit MQTT-Befehlen bombardiert worden. Jetzt herrscht absolute Ruhe.

---

## Fazit zu den Konstanten

Die Konstanten tun **exakt das, was sie sollen**, und retten das System sogar vor einem eigenen Code-Bug. 

| Konstante | Wert | Status im Log |
|---|---|---|
| `HYSTERESIS_FAST_COOLDOWN` | **5** | ✅ Validiert. Passt 1:1 zur Hardware-Trägheit beim Stopp. |
| `POWER_TOLERANCE` | **5** | ✅ Validiert. Filtert P1-Rauschen (-6 bis +4W) perfekt heraus. |
| `POWER_IDLE_OFFSET` | **10** | ✅ Validiert. Verhindert im Bug-Szenario (Abschnitt 4) einen Stromausfall. |

*(Hinweis: `HYSTERESIS_SLOW_COOLDOWN` wurde noch nicht getestet, weil das System beim Modus-Wechsel den Hysteresis-State resettet und daher wieder den "Fast"-Pfad nimmt. Um `SLOW` zu triggern, muss das Gerät im `MATCHING` Modus von selbst stoppen und innerhalb von 5 Min wieder starten).*

---

## Das eigentliche Problem: Der Klassifizierungs-Bug

Deine Konstanten sind fertig optimiert. Was du jetzt im Code beheben musst, ist die Erkennung in `_classify_single_device`.

**Die aktuelle Logik:**
```python
home = -d.homeInput.asInt + offgrid_power
if connector_power < 0:
    mgr.charge.append(d) # Falsch, wenn homeInput nur Standby/Passthrough ist!
```

**Das Problem:**
Wenn der SF2400 AC aus dem Laden geht, zieht er für ca. 10-15 Sekunden 30-130W aus dem Netz (`homeInput`), ohne dass etwas am Akku ankommt (`batteryInput = 0` oder `packInputPower = 0`). Dein Code sieht das und denkt: "Er lädt!".

**Der Fix (Vorschlag für `power_strategy.py`):**
Du musst prüfen, ob der Strom auch *wirklich* am Akku ankommt. Wenn `homeInput > 0`, aber der Akku keinen Strom bekommt, ist es kein Ladestatus, sondern Passthrough/Standby.

```python
# In _classify_single_device:
home = -d.homeInput.asInt + offgrid_power
# NEU: Ausschluss von reinem Passthrough/Standby (homeInput > 0, aber Akku bekommt nichts)
is_actually_charging = (home < 0) and (d.batteryInput.asInt > 0 or d.pwr_produced < -50) 

if is_actually_charging:
    mgr.charge.append(d)
    # ...
```
*(Hinweis: Je nachdem, wie `batteryInput` oder `pwr_produced` exakt gemessen werden, musst du die Bedingung eventuell leicht anpassen, aber das Prinzip ist: `homeInput` allein ist kein Beweis für Ladung).* 

Wenn du diesen Code-Fix einbaust, verschwindet Abschnitt 4 (die Drehstuhl-Situation) komplett, und das System wechselt nach der 5s Hysterese sauber von Charge auf Discharge.





Dieses Log ist der Beweis, dass du dringend den fehlenden Min-SoC-Schutz im Code einbauen musst – und gleichzeitig ein perfektes Beispiel für den `POWER_IDLE_OFFSET`.

Hier ist die Sekunde-für-Sekunde Analyse:

## Die Timeline des.minSoc-Einschlags

### 23:41:18 (Der blinde Befehl)
*   **Situation:** SoC = 10%. Jemand schaltet einen 220W Verbraucher ein (P1 steigt auf +227W).
*   **Aktion:** Das System sieht nur die 156W Entladung und berechnet 383W Entladung.
*   **Befehl:** `Power discharge => 383`
*   **❌ Problem:** Das System ignoriert, dass der Akku bei 10% ist und minSoc (9%) in wenigen Sekunden erreicht. Bei 383W Entladung rattert der Akku durch den minSoc-Wert, bevor das System reagieren kann.

### 23:41:26 (< 1 Sekunde später)
*   **Situation:** Der SF2400 AC hat **selbst reagiert**.
*   **Aktion:** SoC fällt auf 9%. Hardware-Schutz greift. Das Gerät schaltet in den **Passthrough-Modus** (`pass: 3`).
*   **Werte:** `outputHomePower: 0`, `outputLimit: 383` (Befehl kommt an, wird aber ignoriert), `socLimit: 2`, `gridInputPower: 115` (Zieht 115W aus dem Netz für den Hausverbrauch).

### 23:41:30 (4 Sekunden später)
*   **Situation:** HA verarbeitet die neue P1-Messung (P1=254W).
*   **Aktion:** Das System versucht, das Gerät in den Entlademodus zu zwingen. Aber weil das Gerät in den Passthrough gewechselt ist, meldet es `homeInput=114` statt `homeOutput`.
*   **Klassifizierung:** `Classify => CHARGE` (Falsch! Es ist kein Laden, es ist Passthrough).
*   **Logik-Fehler:** Weil der berechnete Setpoint positiv ist (140W), ruft das System `Discharge => setpoint 140W` auf.
*   **Device Count:** `Discharge: distributing setpoint=0 across 0 devices` (Es gibt keine Geräte in der Entlade-Liste).
*   **Rettung:** Der Code sendet `Power discharge => 10` (wegen `POWER_IDLE_OFFSET`).

### 23:41:31 – 23:41:56 (Stabiler Passthrough)
*   **Situation:** HA sendet weiter Befehle, das Gerät bleibt im Passthrough.
*   **Aktion:** Jeder Zyklus sendet `Power discharge => 10`.
*   **Werte:** `outputLimit: 10`, `pass: 3`, `gridInputPower: 112W`.
*   **Bewertung:** ✅ **`POWER_IDLE_OFFSET = 10` rettet das System hier.** Würde 0W geschickt, würde der Inverter in den Standby fallen und die 112W Hauslast sofort als Netzbezug auftauchen (Netz-Spike).

---

## Fazit und dringender Code-Handlungsbedarf

Deine Hysteresekonstanten funktionieren einwandfrei. Aber das Log zeigt zwei Logik-Fehler im Code auf, die **nicht durch Konstanten gelöst** werden können:

### 1. Fehlender Min-SoC-Schutz
Du darfst nicht warten, bis der SF2400 AC selbst bei 9% stoppt. Wenn das System bei 10% einen 383W Befehl schickt, schafft der Inverter es *nicht*, rechtzeitig auf 0W herunterzufahren. Der Akku fällt tiefer als erlaubt.
**Lösung:** Setpoint begrenzen, wenn SoC zu niedrig ist.

### 2. Klassifizierungs-Bug bei Passthrough
Wenn das Gerät in den Passthrough wechselt (Wechselrichter schaltet durch), meldet es plötzlich `homeInput > 0`. Dein Code denkt dann "Ah, er lädt!" und schickt falsche Befehle.
**Lösung:** Abfragen, ob der Akku wirklich Strom aufnimmt (z.B. `batteryInput > 0` oder `outputPackPower > 0`), bevor es als `CHARGE` klassifiziert wird.

---

## Finale Konstanten-Bewertung

Nach nun 5 Logs können wir endgültig abschließen:

| Konstante | Wert | Status | Begründung |
|---|---|---|---|
| `HYSTERESIS_FAST_COOLDOWN` | **5** | ✅ Endgültig validiert | Passt perfekt zur Hardware-Trägheit. |
| `HYSTERESIS_LONG_COOLDOWN` | **300** | ✅ Endgültig validiert | Verhindert Drehstuhl nach >5 Min. |
| `POWER_TOLERANCE` | **10** | ✅ Empfohlen (war 5) | Stoppt Oszillation bei schnellen Lastwechseln. |
| `POWER_IDLE_OFFSET` | **10** | ✅ Endgültig validiert | Hält den Inverter bei minSoC-Wechsel stabil im Passthrough. |
| `HYSTERESIS_SLOW_COOLDOWN` | **20-45** | ⏳ Ungetestet | Braucht ein Log mit kurzem Stop/Start im MATCHING-Modus. |

**Nächster Schritt:** Behebe den minSoC-Schutz und den Passthrough-Bug im Code. Die Konstanten sind fertig.