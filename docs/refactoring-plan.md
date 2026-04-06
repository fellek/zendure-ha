# Refactoring-Analyse: Aufgaben und Dateien

## Kontext

Analyse des IST-Zustands (welche Datei macht was) und ein SOLL-Vorschlag (wie ein Refactoring aussehen könnte). Basiert auf der bestehenden Codebase und den Notizen in `z.ai/optimierung/`.

---

## Tabelle 1: IST-Zustand — Welche Aufgabe wird von welcher Datei übernommen?

| Aufgabe / Verantwortlichkeit | Datei(en) | Zeilen | Anmerkung |
|---|---|---|---|
| **HA Integration Setup/Teardown** | `__init__.py` | 79 | Sauber, keine Mixed Concerns |
| **Config Flow (UI)** | `config_flow.py` | 237 | Sauber |
| **Konstanten & Enums** | `const.py` | 113 | Sauber |
| **Entity-Basisklassen** | `entity.py` | 309 | Sauber |
| **Migration/Registry** | `migration.py` | 178 | Sauber |
| | | | |
| **MQTT Cloud-Verbindung** | `api.py` | 369 | Vermischt mit Device-Factory und Auth |
| **MQTT Local-Verbindung** | `api.py` | — | Teil von api.py |
| **Cloud-Authentifizierung (Token, SHA1)** | `api.py` | — | Gehört nicht zum laufenden MQTT-Betrieb |
| **Device-Factory (createdevice Dict)** | `api.py` | — | Vermischt mit Transport |
| | | | |
| **Power-Orchestrierung (P1-Loop)** | `manager.py` | 818 | Vermischt mit Power-Mathematik |
| **Power-Klassifizierung (Charge/Discharge/Idle)** | `manager.py` | — | Teil von powerChanged() |
| **Power-Verteilung (Gewichtung, Limits)** | `manager.py` | — | power_charge(), power_discharge() |
| **Fusegroup-Limits** | `fusegroup.py` | 73 | Sauber |
| **Modus-Steuerung (MATCHING/MANUAL/OFF)** | `manager.py` | — | update_operation() + match-Block |
| **Simulation/CSV** | `manager.py` | — | Nebenaufgabe, könnte raus |
| | | | |
| **Device-Datenmodell (State, Limits, SoC)** | `device.py` | 787 | Stark vermischt |
| **MQTT-Parsing (mqttMessage, mqttProperties)** | `device.py` | — | Gehört in eigene Schicht |
| **MQTT-Publishing (mqttPublish, mqttInvoke)** | `device.py` | — | Gehört in eigene Schicht |
| **Entity-Erstellung (create_entities)** | `device.py` | — | OK hier, aber groß |
| **Bluetooth/BLE-Transport** | `device.py` | — | Komplett eigenständige Logik |
| **HTTP/SDK-Transport** | `device.py` (ZendureZenSdk) | — | Eigene Subklasse, aber erbt alles |
| **Batterie-Datenmodell** | `device.py` (ZendureBattery) | — | Eigenständiges Objekt |
| **Energie-Aggregation (kWh)** | `device.py` | — | In entityUpdate() vermischt |
| | | | |
| **Power-Port-Abstraktion** | `power_port.py` | 84 | Neu, sauber, teilweise integriert |
| | | | |
| **Sensor-Entities** | `sensor.py` | 190 | Sauber |
| **Number-Entities** | `number.py` | 132 | Sauber |
| **Select-Entities** | `select.py` | 116 | Sauber |
| **Switch-Entities** | `switch.py` | 77 | Sauber |
| **Button-Entities** | `button.py` | 36 | Sauber |
| **Binary-Sensor-Entities** | `binary_sensor.py` | 52 | Sauber |
| | | | |
| **Gerätespezifische Configs** | `devices/*.py` | je 20-50 | Sauber (setLimits, maxSolar, etc.) |

**Hauptprobleme:** `device.py` (787 Zeilen, 6+ Aufgaben), `manager.py` (818 Zeilen, Orchestrierung + Mathematik), `api.py` (369 Zeilen, Transport + Factory + Auth)

---

## Tabelle 2: SOLL-Zustand — Refactoring-Vorschlag

| Datei | Aufgaben VORHER | Aufgaben NACHHER | Änderung |
|---|---|---|---|
| **`device.py`** | Datenmodell + MQTT-Parsing + MQTT-Publishing + BLE + Entity-Erstellung + Aggregation + Battery | Datenmodell + Entity-Erstellung + Power-Steuerung (power_get/charge/discharge) | **Stark verschlankt**: ~300 Zeilen statt 787 |
| **`mqtt_protocol.py`** *(NEU)* | — | MQTT-Parsing (mqttMessage, mqttProperties), MQTT-Publishing (mqttPublish, mqttInvoke), Topic-Routing | **Aus device.py extrahiert** |
| **`ble.py`** *(NEU)* | — | BLE-Transport (bleMqtt, bleCommand, bleAdapterSelect, bleMac) | **Aus device.py extrahiert** |
| **`zendure_sdk.py`** *(NEU)* | — | HTTP-Transport für ZenSDK-Geräte (httpGet, httpPost, doCommand, charge/discharge-Overrides) | **Aus device.py extrahiert** (ZendureZenSdk Klasse) |
| **`battery.py`** *(NEU)* | — | ZendureBattery Klasse, packData-Parsing, totalKwh-Berechnung | **Aus device.py extrahiert** |
| **`api_auth.py`** *(NEU)* | — | Cloud-Auth (api_ha, SHA1-Signierung, Token-Parsing) | **Aus api.py extrahiert** |
| **`api.py`** | MQTT-Broker + Factory + Auth + Message-Routing | MQTT-Broker-Manager (Client-Setup, shutdown) + Device-Factory | **Vereinfacht**: Auth und Routing raus |
| **`manager.py`** | Orchestrierung + Power-Mathematik + Simulation + Klassifizierung | Orchestrierung + Modus-Steuerung + P1-Event-Loop | **Verschlankt**: Power-Mathe raus |
| **`power_strategy.py`** *(NEU)* | — | Power-Klassifizierung + Verteilungs-Mathematik (aus powerChanged, power_charge, power_discharge) | **Aus manager.py extrahiert** |
| **`power_port.py`** | Grid/Offgrid/Solar Ports | Unverändert | Bleibt |
| **`fusegroup.py`** | Fusegroup-Limits | Unverändert | Bleibt |
| Alle Entity-Dateien | Entity-Plattformen | Unverändert | Bleiben |
| `devices/*.py` | Gerätespezifische Configs | Unverändert | Bleiben |

---

## Tabelle 3: Zusammenfassung der Verschiebungen

| Quell-Datei | Was wird verschoben | Ziel-Datei |
|---|---|---|
| `device.py` | mqttPublish(), mqttInvoke(), mqttMessage(), mqttProperties(), entityUpdate() match-Block | `mqtt_protocol.py` |
| `device.py` | bleMqtt(), bleCommand(), bleAdapterSelect(), bleMac, ble_sources() | `ble.py` |
| `device.py` | ZendureZenSdk Klasse komplett (httpGet, httpPost, doCommand) | `zendure_sdk.py` |
| `device.py` | ZendureBattery Klasse, packData-Logik | `battery.py` |
| `api.py` | api_ha(), SHA1-Signierung, Base64-Token-Parsing | `api_auth.py` |
| `api.py` | mqtt_msg_cloud(), mqtt_msg_local(), mqtt_connect() | `mqtt_protocol.py` |
| `manager.py` | Klassifizierung (Charge/Discharge/Idle), power_charge(), power_discharge(), Gewichtungslogik | `power_strategy.py` |
| `manager.py` | writeSimulation(), _sync_write_sim() | Optional: `simulation.py` oder einfach löschen |

---

## Ergebnis nach Refactoring (geschätzte Zeilen)

| Datei | Vorher | Nachher |
|---|---|---|
| `device.py` | 787 | ~300 |
| `manager.py` | 818 | ~400 |
| `api.py` | 369 | ~150 |
| `mqtt_protocol.py` | — | ~250 |
| `ble.py` | — | ~100 |
| `zendure_sdk.py` | — | ~150 |
| `battery.py` | — | ~60 |
| `api_auth.py` | — | ~80 |
| `power_strategy.py` | — | ~300 |
| **Gesamt** | **~1974** | **~1790** |

Weniger Gesamtcode durch Wegfall von Duplikaten, aber vor allem: keine Datei über 400 Zeilen, klare Einzelverantwortung pro Datei.

---

## Empfohlene Reihenfolge (Strangler-Fig-Pattern)

| Schritt | Neue Datei | Risiko | Begründung |
|---|---|---|---|
| 1 | `battery.py` | Sehr gering | ZendureBattery ist bereits isoliert, kleinste Änderung |
| 2 | `api_auth.py` | Gering | Statische Methode, keine Abhängigkeiten zum Laufzeitbetrieb |
| 3 | `ble.py` | Gering | Eigenständige Logik, wenig Querverbindungen |
| 4 | `mqtt_protocol.py` | Mittel | Größter Eingriff in device.py, aber klare Schnittlinie |
| 5 | `zendure_sdk.py` | Mittel | Abhängig von mqtt_protocol.py |
| 6 | `power_strategy.py` | Mittel | Abhängig von stabilem device.py und power_port.py |

---

## Verifikation

Nach jedem Schritt:
- HA Integration laden und prüfen ob Geräte erkannt werden
- MQTT-Nachrichten in den Logs prüfen
- Power-Verteilung testen (MATCHING, MANUAL, OFF Modus)
- Für BLE: Bluetooth-Erkennung prüfen (falls Hardware vorhanden)
