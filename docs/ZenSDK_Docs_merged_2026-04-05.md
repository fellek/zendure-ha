Hier ist das umfassende Masterdokument in deutscher Sprache. Es wurden alle Informationen aus den提供的 Dokumenten (Chinesisch, Englisch, Französisch, Deutsch sowie dem PDF zur Home Assistant Integration) konsolidiert, Dubletten entfernt und eine einheitliche, strukturierte Formatierung angewandt.

---

# Zendure Lokales Steuersystem & Home Assistant Integration (Masterdokument)

<p align="center">
  <img src="https://zendure.com/cdn/shop/files/zendure-logo-infinity-charge_240x.png?v=1717728038" alt="Zendure Logo" width="240">
</p>

## 🌟 1. Überblick
Aus den Erfahrungen des vorherigen [Device Data Report Projekts](https://github.com/Zendure/developer-device-data-report) entstand der Bedarf für eine optimierte lokale Steuerung. Das Team entwickelte daraufhin das IoT-Framework **ZenSDK** und öffnet nun seine **lokale API**. Entwickler können damit folgende Fähigkeiten umsetzen:
*   Abruf von Gerätestatus und -eigenschaften in Echtzeit
*   Abonnieren von Geräte-Datenströmen (Event Stream Subscription)
*   Fernsteuerung von Gerätefunktionen
*   Anbindung beliebiger MQTT-Clients (u.a. [Home Assistant](https://www.home-assistant.io/integrations/mqtt/) und openHAB)
*   Entwicklung individueller Features über offene APIs zur Steigerung des Benutzererlebnisses

---

## 🆕 2. Aktualisierungen & Hinweise
*   **EN 18031 Standard:** Die ZenSDK-MQTT-Client-Verbindung wurde aktualisiert und nutzt TLS für die Verbindung zum Zendure-MQTT-Server.
*   **Lokaler MQTT-Client:** Es wird ein benutzerkonfigurierbarer MQTT-Client bereitgestellt. **Wichtig:** Dieser unterstützt *ausschließlich* Non-TLS. Port 8883 und `mqtts://` werden nicht unterstützt.
*   **API Limits:** Die Empfangslänge der lokalen API ist auf 512 Bytes festgelegt.
*   **EN 18031 Einschränkung:** Unter dem EN 18031-Modus werden HTTP-Anfragen standardmäßig nicht unterstützt.
*   **Aktivierung:** Um die lokale API zu aktivieren, muss zunächst HEMS hinzugefügt und anschließend beendet werden, damit die Änderung wirksam wird.
*   **HEMS Priorität:** Wenn ein Gerät zur Zendure HEMS hinzugefügt wurde, kann es nicht mehr über die MQTT-Integration genutzt werden. Priorität: HEMS > MQTT.

---

## 📌 3. Unterstützte Produkte
Die folgende Liste fasst alle Geräte zusammen, die über die lokale API oder Home Assistant integriert werden können (es wird immer die jeweils neueste Firmware-Version vorausgesetzt).

**Lokale API (ZenSDK):**
*   SolarFlow 800 / 800 Plus / 800 Pro
*   SolarFlow 1600 AC+
*   SolarFlow 2400 AC / 2400 AC+ / 2400 Pro
*   SmartMeter 3CT

**Home Assistant Integration (Cloud & MQTT):**
*   *Zusätzlich zu den obigen Geräten unterstützt HA auch:*
*   Hub 1200 / Hub 2000
*   Hyper 2000
*   AIO 2400
*   SuperBase V6400
*   Smart Meter TIC / D0 / P1
*   CT (Stromwandler)

---

## 🏠 4. Home Assistant Integration
### 4.1 Voraussetzungen
*   Laufender Home Assistant Server (z.B. Raspberry Pi, Mini PC, VM, Docker).
*   Stabile Netzwerkanbindung.

### 4.2 Integrationsmethoden
Es gibt zwei Methoden: **Cloud Token** (integriert alle Geräte eines Kontos auf einmal) und **MQTT** (Geräte müssen einzeln konfiguriert werden).

#### Methode A: Cloud Token Autorisierung
1.  Zendure App öffnen: Profil -> Autorisierung -> Cloud Key.
2.  Cloud Key kopieren.
3.  In Home Assistant: "Zendure" suchen -> Integration hinzufügen.
4.  Copierten Cloud Key einfügen und absenden.
5.  Geräteinformationen prüfen und Integration abschließen.

#### Methode B: MQTT
1.  In der Zendure App: Gerät auswählen -> Einstellungen -> MQTT (Nur Non-TLS unterstütz).
2.  MQTT Broker Informationen eingeben.
3.  In Home Assistant die Geräteliste prüfen.

### 4.3 Unterstützte Operationen (Speichergeräte)
1. Licht An/Aus
2. On-Grid Modus / Off-Grid Modus
3. Export von überschüssiger Energie (An/Aus)
4. On-Grid Eingangsleistung (Limit)
5. On-Grid Ausgangsleistung (Limit)
6. Regulatorische Ausgangsgrenze (Limit)
7. Entladestop SoC (min SoC)
8. Ladestop SoC
9. Import aus dem Netz / Export ins Netz (Aus / Eco-Modus / Normal-Modus)

### 4.4 Verfügbare HA-Datenpunkte
*   **SolarFlow Serie:** Battery maxTemp_SN, maxVol_SN, minVol_SN, Power_SN, SOC_SN, softVersion_SN, State_SN, TotalVol_SN, Average Battery SOC, On/Off-grid Power, Heat State, Device Tmp, Battery Input/Output Power, Battery Num, Battery Status, Bypass Status, RemainOut Time, Reverse Status, SOC Calibration Status, Total Solar Input, Solar Input 1-4.
*   **Smart Meter:** L1/L2/L3 Strom, Leistung, Spannung, Gesamtaktive Energie, Gesamtleistung, Gesamtrückwärtsenergie.
*   **CT:** L1/L2/L3 Frequenz, Strom, Aktive Leistung, Blindleistung, Leistungsfaktor (PF), Spannung (inkl. L1', L2', L3' Abgriffe).

---

## 🚀 5. Kernarchitektur (Lokale API)
Die lokale Steuerung basiert auf **mDNS Service Discovery** und **HTTP RESTful API**.

### 5.1 Geräteerkennung (mDNS)
Nach dem Netzstart sendet das Gerät via mDNS (Multicast DNS):
*   **Service-Name:** `Zendure-<Modell>-<letzte12MAC>` (z.B. `Zendure-SolarFlow800-WOB1NHMAMXXXXX3`)
*   IP-Adresse
*   HTTP-Port

### 5.2 HTTP RESTful API
Jedes Gerät betreibt einen internen HTTP-Server.

| Methode | Zweck | Beispiel |
| -------- | ----------------------------------- | ------------------------------------------------- |
| `GET` | Gerätestatus / Eigenschaften lesen | `GET /properties/report` |
| `POST` | Steuer- oder Konfig-Befehle senden | `POST /properties/write` |

**Datenformate:**
*   **GET:** Kein Request-Body, Antwort erfolgt als JSON.
*   **POST:** JSON-Body, das Feld `sn` (Seriennummer) ist zwingend erforderlich.

#### API Beispiele
**Eigenschaften abfragen:**
```http
GET /properties/report
```
**Eigenschaft schreiben (z.B. acMode ändern):**
```http
POST /properties/write
Content-Type: application/json

{
  "sn": "WOB1NHMAMXXXXX3",
  "properties": {
    "acMode": 2
  }
}
```
**MQTT Status prüfen:**
```http
GET /rpc?method=HA.Mqtt.GetStatus
```
**MQTT Konfiguration abrufen:**
```http
GET /rpc?method=HA.Mqtt.GetConfig
```
**MQTT Konfiguration setzen:**
```http
POST /rpc
Content-Type: application/json

{
  "sn": "WOB1NHMAMXXXXX3",
  "method": "HA.Mqtt.SetConfig",
  "params": {
    "config": {
      "enable": true,
      "server": "192.168.50.48",
      "port": 1883,
      "protocol": "mqtt",
      "username": "zendure",
      "password": "zendure"
    }
  }
}
```

---

## 🛠️ 6. Entwicklungswerkzeuge
### 6.1 mDNS Erkennung (OS Ebene)
| Betriebssystem | Befehl | Beschreibung |
| -------------- | ------------------------------------------------------------ | ------------------------------------ |
| Windows | `Get-Service \| Where-Object { $_.Name -like "*Bonjour*" }` | Bonjour-Dienst prüfen |
| macOS | `dns-sd -B _zendure._tcp` | Zendure-Geräte durchsuchen |
| Linux | `avahi-browse -r _zendure._tcp` | Services `_zendure._tcp` entdecken |

### 6.2 Code-Beispiele & CLI
In den offiziellen Repositories finden sich Beispiele für: **C, C#, Java, JavaScript, openHAB, PHP, Python**.

**CLI Schnelltest (cURL):**
```bash
# Alle Eigenschaften abrufen
curl -X GET "http://<gerät-ip>/properties/report"

# MQTT-Status prüfen
curl -X GET "http://<gerät-ip>/rpc?method=HA.Mqtt.GetStatus"

# acMode-Eigenschaft setzen
curl -X POST "http://<gerät-ip>/properties/write" \
  -H "Content-Type: application/json" \
  -d '{"sn": "your_device_sn", "properties": { "acMode": 2 }}'
```

---

## 📚 7. Produktdaten & Eigenschaften (Property Reference)
*Gilt für die SolarFlow Serie.*

### 7.1 Dokumentkonventionen
*   Leistungseinheit: W (Watt)
*   Spannungseinheit: V (Volt)
*   Temperatureinheit: 0.1 Kelvin (sofern nicht anders angegeben)
*   Zugriff: `RO` (Read Only / Nur Lesen), `RW` (Read / Write / Lesen & Schreiben)
*   Alle `int` Werte sind nicht-negativ, sofern nicht anders spezifiziert.

### 7.2 Wichtige Datenkonvertierungen
**Temperatur (maxTemp):**
Gespeichert als 0.1 Kelvin. Umrechnung in Celsius:
`float maxTemp_C = (maxTemp - 2731) / 10.0;`

**Batteriestrom (batcur):**
Das Rohformat ist ein **16-Bit Zweierkomplement**, gespeichert in `uint8_t[2]`. Es muss in einen vorzeichenbehafteten 16-Bit Integer umgewandelt und dann durch 10 geteilt werden.
`current_A = ((int16_t)batcur) / 10.0;`

### 7.3 Battery Pack Daten (RO)
| Attribut | Typ | Einheit | Beschreibung |
|-----------|------|--------|------|
| sn | string | — | Seriennummer des Batteriepacks |
| packType | int | — | Reserviert |
| socLevel | int | % (0-100) | Ladezustand (State of Charge) |
| state | int | — | 0: Standby, 1: Laden, 2: Entladen |
| power | int | W | Leistung des Batteriepacks |
| maxTemp | int | 0.1K | Höchste Temperatur (siehe Konvertierung) |
| totalVol | int | V | Gesamtspannung |
| batcur | int | A | Gesamtstrom (siehe Konvertierung) |
| maxVol | int | 0.01V | Höchste Zellspannung |
| minVol | int | 0.01V | Niedrigste Zellspannung |
| softVersion | int | — | Firmware-Version |
| heatState | int | — | 0: Nicht heizen, 1: Heizen |

### 7.4 Geräte Daten - Nur Lesen (RO)
| Attribut | Typ | Einheit | Beschreibung |
|-----------|------|--------|------|
| heatState | int | — | Heizzustand (0: Aus, 1: An) |
| packInputPower | int | W | Batterie Eingangsleistung (Entladen) |
| outputPackPower | int | W | Ausgangsleistung zur Batterie (Laden) |
| outputHomePower | int | W | Ausgangsleistung an das Haus |
| remainOutTime | int | min | Verbleibende Entladezeit |
| packState | int | — | 0: Standby, 1: Laden, 2: Entladen |
| packNum | int | — | Anzahl der Batteriepacks |
| electricLevel | int | % (0-100) | Durchschnittlicher Ladezustand (SOC) |
| gridInputPower | int | W | Netzeingangsleistung |
| solarInputPower | int | W | Gesamt PV-Eingangsleistung |
| solarPower1~6 | int | W | PV-Kanal Leistung (Kanal 1 bis 6) |
| pass | int | 0–3 | Bypass-Modus (siehe Detailtabelle unten) |
| reverseState | int | — | 0: Nein, 1: Rückspeisung (Einspeisung) |
| socStatus | int | — | 0: Nein, 1: SOC-Kalibrierung läuft |
| hyperTmp | int | — | Gehäusetemperatur |
| dcStatus | int | — | 0: Stopp, 1: Batterieeingang, 2: Batterieausgang |
| pvStatus | int | — | 0: Stopp, 1: Laufend |
| acStatus | int | — | 0: Stopp, 1: On/Off-Grid Betrieb, 2: Ladebetrieb |
| dataReady | int | — | 0: Nicht bereit, 1: Bereit |
| gridState | int | — | 0: Nicht verbunden, 1: Verbunden |
| BatVolt | int | 0.01V | Batteriespannung |
| FMVolt | int | V | Spannungsaktivierungswert |
| socLimit | int | — | 0: Normal, 1: Ladestopp, 2: Entladestopp |
| rssi | int | dBm | Signalstärke |
| gridOffPower | int | W | Off-Grid Leistung |
| lampSwitch | int | — | 0: Aus, 1: An (Licht) |
| gridOffMode | int | — | Off-Grid Modus |
| IOTState | int | — | IoT Verbindungszustand |
| fanSwitch | int | — | 0: Aus, 1: An (Lüfter) |
| fanSpeed | int | — | Lüfterstufe |
| faultLevel | int | — | Fehler Schweregrad |
| bindstate | int | — | Bindungsstatus |
| VoltWakeup | int | — | Spannungs-Wake-up |
| OldMode | int | — | Legacy-Modus |
| OTAState | int | — | OTA Status |
| LCNState | int | — | LCN Status |
| factoryModeState | int | — | Werkseinstellungs-Modus |
| timestamp | int | — | Systemzeitstempel |
| ts | int | — | Unix-Zeitstempel |
| timeZone | int | — | Zeitzone |
| tsZone | int | — | Zeitzonen-Offset |
| chargeMaxLimit | int | W | Maximale Ladeleistung |
| phaseSwitch | int | — | Phasenumschaltung |
| is_error | int | — | 0: Ok, 1: Fehler |
| acCouplingState | int | — | AC-Kopplungs Status (Siehe Bit-Feld Definition) |
| dryNodeState | int | — | Trockenkontakt Status (1: Verbunden, 0: Verbunden - kann je nach Verkabelung umgekehrt sein) |

#### `pass` — Bypass-Modus (Detailwerte)

| Wert | Name | Bedeutung |
|------|------|-----------|
| 0 | BYPASS-OFF | Normalbetrieb ohne BYPASS |
| 1 | Nicht verwendet | Nicht verwendet |
| 2 | BYPASS-REVERSE | Überschüssiger Strom von Offgrid-Socket oder PV-Leistung wird direkt ins Netz eingespeist |
| 3 | BYPASS-INPUT | Benötigter Strom an Offgrid-Socket wird direkt aus dem Netz gezogen |

**BYPASS-OFF (0):** Gerät arbeitet im regulären Betrieb. Die Batterie kann geladen oder entladen
werden. `inputLimit` und `outputLimit` werden vom Steuerungssystem gesetzt und akzeptiert.
`smartMode` ist typischerweise 1.

**Nicht verwendet (1):** Dieser Wert ist in der ursprünglichen SDK-Dokumentation erwähnt, wird
vom Gerät jedoch nicht gesendet. Alle ausgewerteten Logs zeigen ausschließlich die Werte 0, 2 und 3.

**BYPASS-REVERSE (2):** Das Gerät schaltet in einen Durchleitungs-Modus, bei dem überschüssige
Energie (z. B. von einem angeschlossenen Off-Grid-Socket oder PV-Eingang) direkt ins Hausnetz
eingespeist wird, ohne die Batterie zu nutzen. Erkennbar an: `acMode: 2` (Output),
`gridOffPower < 0` (Einspeisung ins Off-Grid-Netz), `batcur ≈ 0 A` (keine Batterieaktivität).
Tritt auf bei verschiedenen SOC-Leveln, häufig in Verbindung mit `socLimit: 2` (Entladestopp).
Befehle über `inputLimit`/`outputLimit` werden ignoriert (`= 0`). `smartMode: 0`.

**BYPASS-INPUT (3):** Das Gerät schaltet in einen Durchleitungs-Modus, bei dem der am
Off-Grid-Socket benötigte Strom direkt aus dem Hausnetz (Grid) gezogen wird, ohne die Batterie zu
nutzen. Typisch bei leerem Akku (SOC 8–25 %, `socLimit: 2`). Erkennbar an: `acMode: 1` (Input),
`gridInputPower > 0`, `gridOffPower > 0`, `batcur ≈ −0,1` bis `−0,4 A` (uint16: 65532–65535).
Befehle über `inputLimit`/`outputLimit` werden ignoriert (`= 0`). `smartMode: 0`.
Das Gerät verlässt diesen Modus selbstständig nach dem Hardware-Relais-Rückübergang
(beobachtete Wartezeit: 2–3 Minuten); Lade-/Entladebefehle werden in dieser Zeit nicht ausgeführt.

> **Hinweis**: In beiden Bypass-Modi (2 und 3) gilt: `inputLimit == 0`, `outputLimit == 0`,
> `smartMode == 0`. Das Gerät reagiert nicht auf Steuerungsbefehle. `batcur` zeigt in beiden
> Fällen nahezu keinen Batteriestrom (0 bis −0,4 A; Rohwert uint16 → int16 / 10).

### 7.5 Geräte Daten - Lesen & Schreiben (RW)
| Attribut | Typ | Einheit / Bereich | Beschreibung |
|-----------|------|-------------------|------|
| writeRsp | N/A | — | Schreibantwort |
| acMode | int | 1-2 | 1: Eingang (Laden), 2: Ausgang (Entladen) |
| inputLimit | int | W | AC Ladeleistungs-Limit |
| outputLimit | int | W | Ausgangsleistungs-Limit |
| socSet | int | 70-100 (%) | Ziel-SOC |
| minSoc | int | 0-50 (%) | Minimaler SOC |
| gridReverse | int | 0-2 | 0: Deaktiviert, 1: Rückspeisung erlauben, 2: Rückspeisung verbieten |
| inverseMaxPower | int | W | Maximaler Wechselrichter-Ausgang |
| gridStandard | int | 0-9 | 0:DE, 1:FR, 2:AT, 3:CH, 4:NL, 5:ES, 6:BE, 7:GR, 8:DK, 9:IT |
| smartMode | int | 0-1 | Flash-Schreibverhalten (1: Parameter werden *nicht* in Flash geschrieben, Gerät stellt nach Neustart alte Werte her. Empfohlen für häufige Änderungen) |
| batCalTime | int | Minuten | Batterie-Kalibrierungszeit (Nicht empfohlen zu ändern) |
| Fanmode | int | 0-1 | 0: Lüfter aus, 1: Lüfter an (Nicht empfohlen zu ändern) |
| Fanspeed | int | 0-2 | 0: Auto, 1: Stufe 1, 2: Stufe 2 (Nicht empfohlen zu ändern) |
| gridOffMode | int | 0-2 | 0: Standard Modus, 1: Ökonomie Modus, 2: Verschluss (Closure) |

### 7.6 Bit-Feld Definitionen
**AC Coupling State (`acCouplingState`):**
| Bit | Bedeutung |
|-----|--------|
| Bit 0 | AC-gekoppelter Eingang vorhanden, wird automatisch durch DSP gelöscht |
| Bit 1 | AC-Eingang vorhanden (Flag) |
| Bit 2 | AC-gekoppelte Überlast |
| Bit 3 | Überschüssige AC-Eingangsleistung |