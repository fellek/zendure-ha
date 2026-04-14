# MQTT-Feld: `batcur` (Batteriestrom)

## Deutsch

### Beschreibung

`batcur` ist das Batteriestrommessfeld im `packData`-Objekt der MQTT-Property-Reports des Zendure SF2400 AC.

**Quelle:** `packData[].batcur` im Topic `.../properties/report`

### Einheit und Kodierung

- **Einheit:** 100 mA (0,1 A) pro Einheit
- **Datentyp:** uint16 (vorzeichenloser 16-Bit-Integer im MQTT-Protokoll)
- **Vorzeichenkodierung:** Vorzeichenbehafteter Wert als uint16 (Zweierkomplement)

**Dekodierung:**
```
batcur_raw > 32767  →  batcur_A = (batcur_raw - 65536) × 0.1 A  (Entladen)
batcur_raw ≤ 32767  →  batcur_A = batcur_raw × 0.1 A            (Laden)
```

### Vorzeichen

| Vorzeichen | Richtung | Beispiel |
|------------|----------|---------|
| Positiv    | Laden    | `270` → +27,0 A |
| Negativ    | Entladen | `65490` → −4,6 A |
| ~0         | Ruhestrom / Selbstentladung | `65534` → −0,2 A |

### Plausibilitätsprüfung

`batcur` lässt sich mit `totalVol` (Gesamtspannung, Einheit: 10 mV) gegen `packData.power` (Watt) verifizieren:

```
P [W] = |batcur × 0,1 A| × totalVol × 0,01 V
```

**Beispiel aus den Logs (Entladebetrieb):**
```
batcur  = 65490  →  -46  →  -4,6 A
totalVol = 4890  →  48,9 V
P = 4,6 × 48,9 ≈ 225 W  ≈  packData.power = 229 W  ✓
```

**Beispiel aus den Logs (Ladebetrieb 1500 W):**
```
batcur  = 270  →  +27,0 A
totalVol ≈ 4920  →  49,2 V
P = 27,0 × 49,2 ≈ 1328 W  ≈  packData.power = 1333 W  ✓
```

### Wertetabelle (Beispiele aus dem Betrieb)

| `batcur` (raw) | Dekodiert | Strom   | Betriebszustand          |
|----------------|-----------|---------|--------------------------|
| 270–293        | +27–29,3  | +2,7–2,93 A | Laden mit ~1500 W    |
| 65490–65486    | −46 bis −48 | −4,6–4,8 A | Entladen mit ~190 W |
| 65534          | −2        | −0,2 A  | Idle / Selbstentladung   |
| 6              | +6        | +0,6 A  | Laden mit ~63 W (Anlauf) |

---

## English

### Description

`batcur` is the battery current measurement field in the `packData` object of Zendure SF2400 AC MQTT property reports.

**Source:** `packData[].batcur` in the topic `.../properties/report`

### Unit and Encoding

- **Unit:** 100 mA (0.1 A) per unit
- **Data type:** uint16 (unsigned 16-bit integer in the MQTT protocol)
- **Sign encoding:** Signed value stored as uint16 (two's complement)

**Decoding:**
```
batcur_raw > 32767  →  batcur_A = (batcur_raw - 65536) × 0.1 A  (discharging)
batcur_raw ≤ 32767  →  batcur_A = batcur_raw × 0.1 A            (charging)
```

### Sign Convention

| Sign     | Direction  | Example |
|----------|------------|---------|
| Positive | Charging   | `270` → +27.0 A |
| Negative | Discharging | `65490` → −4.6 A |
| ~0       | Standby / self-discharge | `65534` → −0.2 A |

### Plausibility Check

`batcur` can be cross-validated against `totalVol` (total pack voltage, unit: 10 mV) and `packData.power` (Watts):

```
P [W] = |batcur × 0.1 A| × totalVol × 0.01 V
```

**Example from logs (discharge mode):**
```
batcur   = 65490  →  -46  →  -4.6 A
totalVol = 4890   →  48.9 V
P = 4.6 × 48.9 ≈ 225 W  ≈  packData.power = 229 W  ✓
```

**Example from logs (charging at 1500 W):**
```
batcur   = 270    →  +27.0 A
totalVol ≈ 4920   →  49.2 V
P = 27.0 × 49.2 ≈ 1328 W  ≈  packData.power = 1333 W  ✓
```

### Value Table (observed examples)

| `batcur` (raw) | Decoded   | Current     | Operating state               |
|----------------|-----------|-------------|-------------------------------|
| 270–293        | +27–29.3  | +27–29.3 A  | Charging at ~1500 W           |
| 65490–65486    | −46 to −48 | −4.6–4.8 A | Discharging at ~190 W         |
| 65534          | −2        | −0.2 A      | Idle / self-discharge         |
| 6              | +6        | +0.6 A      | Charging at ~63 W (startup)   |

### Related Fields

| Field        | Unit    | Description                              |
|--------------|---------|------------------------------------------|
| `totalVol`   | 10 mV   | Total battery pack voltage               |
| `power`      | W       | Battery pack power (BMS measurement)     |
| `BatVolt`    | mV      | Battery voltage (device-level)           |
| `packState`  | —       | Pack state: 1 = charging, 2 = discharging |
