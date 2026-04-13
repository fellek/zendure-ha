# Plan 02: Bypass-Keepalive & Wakeup-Routine

## Status: Offen (5 offene Fragen vor Implementierung)

## Problem

Während `pass=3` (BYPASS-INPUT) bestimmt der Wert von `inputLimit` ob das Gerät auf
Steuerbefehle reagiert:

| Zustand im Bypass | Wakeup-Dauer | Cmd -> Charge |
|---|---|---|
| `inputLimit > 0` (beliebig) | 15-30s | **5-20s** |
| `inputLimit = 0` | 30-115s | verzögert oder kein Start |
| `inputLimit = 0`, kein Cmd (P1 > 0) | Stunden | nie |

Die Integration setzt `inputLimit=0` und `outputLimit=0` via `power_off()` und beim
Übergang in den Discharge-Modus. Damit verliert das Gerät die Reaktionsfähigkeit für
mindestens einen vollen Relay-Umschaltzyklus (2-3 Minuten).

## Offene Fragen (Recherche vor Implementierung)

1. **Hypothese bestätigen**: Gibt es in den Logs eindeutige Paare
   `(iL=0 während Bypass -> langer Wakeup)` vs. `(iL>0 während Bypass -> kurzer Wakeup)`
   mit identischem SOC/socLimit-Kontext?

2. **Mindest-Keepalive-Wert**: Reichen 10W? Logs zeigen iL-Werte von 7W, 22W, 27-48W.

3. **Wechselwirkung mit `smartMode`**: Bei `oL=10`-Keepalive-Events ist `smartMode=1`
   (Befehle werden nicht in Flash geschrieben). Muss der Keepalive `smartMode=1` setzen?

4. **`outputLimit=10` Herkunft**: Die `oL=10`-Werte — von der Integration oder dem Gerät?
   Git-History des Upstream-Repos prüfen.

5. **Einfluss auf Energiebilanz**: 10W dauerhafter Keepalive verändert P1 und Setpoint.
   Wie wird das in `_classify_single_device()` berücksichtigt?

## Implementierungsoptionen

### Option A — Charge-Keepalive (inputLimit >= 10W während BYPASS-INPUT)

```python
if d.is_bypassing and d.acPort.power_consumption == 0:
   await d.charge(-10)  # Minimaler Keepalive: inputLimit=10W
```

Vorteil: Gerät bleibt reaktionsfähig. Nachteil: 10W Netzbezug zusätzlich.

### Option B — Discharge-Keepalive (outputLimit = 10W)

```python
if d.is_bypassing and d.acPort.power_production == 0:
   await d.discharge(10)  # outputLimit=10W
```

### Option C — Puls statt Dauerstrom

Unabhängig von P1-Vorzeichen einen `power_charge(-10)` senden wenn `is_bypassing=True`
und kein anderer Befehl im selben Roundtrip.
Vorteil: Kein dauerhafter Keepalive-Strom. Nachteil: Puls alle 5-15s.

## Betroffene Dateien

| Datei | Relevanz |
|---|---|
| `zendure_sdk.py` | `charge()`, `discharge()` — Keepalive-Befehl |
| `power_strategy.py` | `_wake_idle_devices()` — Einstiegspunkt |
| `device.py` | `is_bypassing`-Property, `power_charge()` |
| `const.py` | ggf. neue Konstante `BYPASS_KEEPALIVE_W = 10` |

## Verifikation

1. `pass=3` gefolgt von Keepalive -> `inputLimit` im nächsten MQTT-Report != 0
2. Wakeup-Zeit: Messe `power_charge()` bis `packState=1` mit und ohne Keepalive
3. P1-Stabilität: 10W-Keepalive darf Setpoint-Regler nicht destabilisieren
4. `smartMode=1` bei Keepalive-Befehl prüfen
