# Inverter Loss / Selbstverbrauch (SF2400AC)

## Motivation

Der Zendure SolarFlow 2400 AC hat einen nicht vernachlässigbaren Eigenverbrauch, der in
keinem einzelnen MQTT-Feld direkt sichtbar ist. Die Firmware stellt die interne
Energiebilanz durch "kreative" Zuordnung wieder her:

- Beim **Laden**: `gridInputPower == outputPackPower` exakt → Selbstverbrauch unsichtbar
- Beim **Entladen**: `packInputPower - outputHomePower ≈ 4W` → fast alles unsichtbar
- Im **Idle** (mit Offgrid AN): erscheint teilweise in `gridOffPower`, Rest in `gridInputPower`

### Folge für die Ladelogik

Der Ladebefehl der Integration legt die Leistung am `connectorPort` fest — nicht die am
Akku ankommende Leistung. Die am connectorPort verfügbare Energie wird intern wie folgt
aufgeteilt:

```
connectorPort_power = akku_ladung + inverter_selbstverbrauch + offgrid_last
```

Wird z.B. **300W Ladung** befohlen, gehen vom Akku-Anteil ab:
- Selbstverbrauch Inverter (~45W aktiv)
- Verbrauch am Offgrid-Socket (wenn Last angesteckt)

**Kritisches Szenario 1 — Offgrid-Last > (connectorPort - Selbstverbrauch):**
Befehl: "Lade mit 300W". Offgrid-Last: 280W. Selbstverbrauch: 60W.
Akku-Bilanz: `300 - 60 - 280 = -40W` → Akku **entlädt** trotz Ladebefehl!
Das läuft weiter, bis SOCEMPTY erreicht ist und die Firmware eigenständig auf Bypass
umschaltet.

**Kritisches Szenario 2 — Ladeleistung < Selbstverbrauch:**
Befehl: "Lade mit 30W". Selbstverbrauch: 45W.
Akku-Bilanz: `30 - 45 = -15W` → Akku entlädt ebenfalls bis SOCEMPTY.

Der `InverterLossPowerPort` macht dieses Problem als HA-Sensor sichtbar. Eine echte
Korrektur der Setpoints liegt außerhalb des v1-Scopes.

## Messdatenbasis

### Datenquelle 1: Shelly-CSV (2026-04-10)

Messaufbau: Shelly zwischen Hausnetz und connectorPort, Offgrid AN mit Verbraucher,
Entladung bis SOCEMPTY. Limitiert (nur wenige Shelly-Samples während Entladung), aber
liefert Anhaltspunkte für den Bypass-Zustand.

Ergebnisse:
- Bypass (Batterie=0): `gridOffPower - Shelly ≈ 13W` → Battery-Trickle + Bypass-Verbrauch
- Discharge (213W Batterie): `bat_in_out = outputHomePower + gridOffPower` (FW-konsistent)

### Datenquelle 2: MQTT-Log (2026-04-14, entscheidend)

**Offgrid AN, KEIN Verbraucher angeschlossen** → `gridOffPower` = reiner Selbstverbrauch
der Offgrid-Schaltung. P1-Smartmeter liefert die wahre Grid-Leistung. Haus-Grundlast =
214W (aus IDLE-Phase abgeleitet).

| Zustand | P1 | FW gridIn | FW bat | FW homeOut | FW gridOff | FW sichtbar | **REAL (P1)** |
|---------|-----|-----------|--------|------------|------------|-------------|---------------|
| IDLE (Standby) | 245 | 31 | 0/0 | 0 | 13 | 31W total | **31W** |
| CHARGE 1500W | 1778 | 1500 | 1501/0 | 0 | 0 | 0W (!) | **63W** |
| DISCHARGE 329W | -59 | 0 | 0/329 | 325 | 0 | 4W | **56W** |
| DISCHARGE 803W | -566 | 0 | 0/803 | 799 | 1 | 4W | **23W** |

**Methode:** Haus-Grundlast aus IDLE: `214W = P1(245) - gridInputPower(31)`. In aktiven
Phasen: `real_device_power = P1 - 214`. Selbstverbrauch = `real_device_power -
FW_reported_transfer`.

### Schlussfolgerungen

1. **Standby: ~31W** — signifikant höher als initial angenommen (10W)
   - 13W erscheinen in `gridOffPower` (Offgrid-Schaltkreis)
   - 18W unsichtbar in `gridInputPower`
2. **Aktiver Verlust: 23–63W** je nach Betriebspunkt
   - 1500W Ladung: 63W (~4.2%)
   - 803W Entladung: 23W (~2.9%)
   - 329W Entladung: 56W (~17%) — dominanter Grundlastanteil
3. **Firmware versteckt Verluste**
   - Laden: `gridInputPower == outputPackPower` exakt
   - Entladen: nur ~4W Differenz sichtbar
4. **`gridOffPower = 0` bei aktivem Laden/Entladen** (obwohl Offgrid AN) — Firmware
   meldet Offgrid-Last nur im IDLE

### Kalibrierte Modellwerte (SF2400AC)

| Zustand | Selbstverbrauch | Quelle |
|---------|----------------|--------|
| Offline | 0W | — |
| Standby | **25W** | Mittel aus gemessenen 31W (Offgrid AN) und ~15-20W (Offgrid AUS geschätzt) |
| Aktiv | **45W** | Konservatives Mittel aus gemessenen 23-63W |

## Klasse: `InverterLossPowerPort`

**Datei:** `custom_components/zendure_ha/power_port.py`

### Zweck

Schätzt den aktuellen Selbstverbrauch des Inverters in Watt. Immer `>= 0`.
Wird als PowerPort in jedes Gerät integriert und exponiert einen HA-Sensor
(`inverter_loss`).

### Strategien (in Reihenfolge der Bevorzugung)

#### 1. `energy_balance` (Messung, wenn verfügbar)

Aktiv, wenn:
- Gerät hat Offgrid-Anschluss (`_has_offgrid == True`)
- `offgrid_power > 0` (Offgrid-Schaltung AN und zieht Strom)

Berechnung:
```
all_in  = connectorPort.power_consumption + solarPort.total_solar_power
        + batteryPort.discharge_power
all_out = batteryPort.charge_power + connectorPort.power_production
loss    = max(0, all_in - all_out)
```

Per Firmware-Bilanz gilt `all_in - all_out == gridOffPower` (exakt). Der Wert ist eine
**Obergrenze** des Selbstverbrauchs, da er die tatsächliche Offgrid-Last nicht von ihm
separieren kann.

**Limitation:** Da die Firmware `gridOffPower = 0` meldet, sobald das Gerät aktiv lädt
oder entlädt, greift diese Strategie in der Praxis fast nur im IDLE.

#### 2. `model` (Fallback)

Fester Modellwert, basierend auf dem Betriebszustand:
- `DeviceState.OFFLINE` → 0W
- Aktiv (`batteryPort.is_charging or batteryPort.is_discharging`) → `model_active_w` (45W)
- Sonst (Idle/Bypass) → `model_standby_w` (25W)

Die Defaultwerte sind für den SF2400AC kalibriert. Andere Gerätemodelle können die
Konstruktorparameter überschreiben.

### API

```python
class InverterLossPowerPort(PowerPort):
    def __init__(self, device: ZendureDevice, model_active_w: int = 45, model_standby_w: int = 25)

    @property
    def power(self) -> int:
        """Geschätzter Selbstverbrauch in Watt (immer >= 0)."""

    @property
    def strategy(self) -> str:
        """Aktuelle Strategie: "energy_balance" oder "model"."""
```

### Integration

1. **Port-Instanziierung** in `ZendureDevice._init_power_ports()` — jedes Gerät bekommt
   den Port (nicht nur Offgrid-Geräte), da auch Geräte ohne Offgrid Selbstverbrauch haben.

2. **HA-Sensor** `inverter_loss` (W/power/measurement) in `create_entities()`.

3. **Update-Zyklus** in `update_power_flow_state()`:
   ```python
   self.inverterLoss.update_value(self.inverterLossPort.power)
   ```

### Erwartetes Verhalten

| Situation | `power` | `strategy` |
|-----------|---------|-----------|
| Gerät offline | 0 | model |
| Offgrid AUS, aktiv | 45 | model |
| Offgrid AUS, idle | 25 | model |
| Offgrid AN, idle | ~13 (gleich `gridOffPower`) | energy_balance |
| Offgrid AN, aktiv | 45 (FW meldet gridOffPower=0) | model |

## Scope v1: Nur informativ

Der Sensor ist **informativ** und wird in v1 **nicht** in die Setpoint-Berechnung
zurückgeführt. Eine echte Korrektur muss berücksichtigen, dass der Akku-Anteil am
connectorPort durch Selbstverbrauch und Offgrid-Last reduziert wird:

```
connectorPort_command = gewünschte_akku_leistung + inverter_loss + offgrid_load
```

Beispiel Laden mit 300W netto in den Akku bei 60W Selbstverbrauch und 280W Offgrid-Last:
Befehl an connectorPort muss `300 + 60 + 280 = 640W` lauten, damit tatsächlich 300W im
Akku ankommen.

Voraussetzungen für einen Korrektur-Loop:
1. Validierung des Sensors gegen reale Messungen über mehrere Tage
2. Modellwerte pro Gerätemodell (derzeit nur SF2400AC kalibriert)
3. Schutz gegen das Entlade-Szenario: Wenn `offgrid_load + inverter_loss >
   connectorPort_limit`, muss die Strategy den Ladebefehl entweder aufstocken (falls
   das Gerät es kann) oder frühzeitig stoppen, statt den Akku unbemerkt leerzulaufen.

Siehe `.claude/plans/nested-pondering-puzzle.md` für die nächsten geplanten Schritte.

## Offene Punkte

1. **Lastabhängiges Modell:** Die gemessenen Verluste (23W bei 800W Entladung vs. 56W
   bei 329W Entladung) deuten auf einen hohen Grundlastanteil. Ein Modell
   `loss = base + factor * |power|` wäre genauer, aber erfordert mehr Messdaten.

2. **Andere Gerätemodelle:** Hyper 2000, AIO 2400, Hub-Serie — Messungen fehlen.
   Defaults (45W/25W) sind vorerst vernünftig, bis gerätespezifische Daten vorliegen.

3. **Strategie 1 (Energiebilanz mit bekannter Offgrid-Last):** Nicht implementiert.
   Erfordert User-Konfiguration einer externen Last-Messung (z.B. Shelly an der
   Offgrid-Steckdose).

4. **Strategie 3 (P1-Kreuzvalidierung):** Nicht implementiert. Würde lernend arbeiten,
   indem Sprünge in P1 mit Sprüngen in `outputPackPower` verglichen werden — funktioniert
   aber nur bei stabiler Grundlast und einzelnem aktiven Gerät.
