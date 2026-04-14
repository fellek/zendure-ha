# Konstanten-Dokumentation (`const.py`)

Alle Konstanten und Enums der Zendure-HA-Integration.

---

## Allgemein

| Konstante | Wert | Beschreibung |
|---|---|---|
| `DOMAIN` | `"zendure_ha"` | Integration-Domain-Name für HA |

---

## Konfigurationskeys (`CONF_*`)

Schlüssel für den HA-Config-Flow (gespeichert in `config_entry.data`).

| Konstante | Key | Beschreibung |
|---|---|---|
| `CONF_APPTOKEN` | `"token"` | API-Token für die Zendure Cloud |
| `CONF_P1METER` | `"p1meter"` | Entity-ID des P1-Smartmeters |
| `CONF_PRICE` | `"price"` | Strompreis-Entität |
| `CONF_MQTTLOG` | `"mqttlog"` | MQTT-Logging aktivieren |
| `CONF_MQTTLOCAL` | `"mqttlocal"` | Lokale MQTT-Verbindung nutzen (statt Cloud) |
| `CONF_MQTTSERVER` | `"mqttserver"` | Adresse des lokalen MQTT-Brokers |
| `CONF_SIM` | `"simulation"` | Simulationsmodus aktivieren |
| `CONF_MQTTPORT` | `"mqttport"` | Port des MQTT-Brokers |
| `CONF_MQTTUSER` | `"mqttuser"` | MQTT-Benutzername |
| `CONF_MQTTPSW` | `"mqttpsw"` | MQTT-Passwort |
| `CONF_WIFISSID` | `"wifissid"` | WLAN-SSID für Gerätekonfiguration |
| `CONF_WIFIPSW` | `"wifipsw"` | WLAN-Passwort für Gerätekonfiguration |
| `CONF_AUTO_MQTT_USER` | `"auto_mqtt_user"` | Automatischer MQTT-User-Modus |
| `CONF_HAKEY` | `"C*dafwArEOXK"` | Interner HA-Integrationsschlüssel |

---

## Enums

### `AcMode`

Steuert den Betriebsmodus des AC-Ports.

| Wert | Bedeutung |
|---|---|
| `INPUT = 1` | AC-Port nimmt Energie auf (laden aus dem Netz) |
| `OUTPUT = 2` | AC-Port gibt Energie ab (Einspeisung ins Hausnetz) |

---

### `DeviceState`

Firmware-Zustand des Geräts. Die Werte spiegeln das Protokoll-Feld `socLimit` wider.

| Wert | Bedeutung |
|---|---|
| `OFFLINE = 0` | Kein MQTT-Kontakt seit Timeout |
| `SOCFULL = 1` | Akku voll (`socLimit=1`); Gerät darf nur entladen, nicht laden |
| `SOCEMPTY = 2` | Akku leer (`socLimit=2`); Gerät darf nur laden, nicht entladen |
| `ACTIVE = 3` | Normalbetrieb: Gerät erreichbar, Lade- und Entladebetrieb erlaubt |

> Hinweis: `INACTIVE` existiert nicht mehr — frühere Idle-/Standby-Zustände fallen heute
> unter `ACTIVE` und werden über `PowerFlowState` (siehe unten) differenziert.

---

### `ManagerMode`

Betriebsmodus des PowerStrategy-Managers, vom Benutzer einstellbar.

| Wert | Bedeutung |
|---|---|
| `OFF = 0` | Steuerung deaktiviert |
| `MANUAL = 1` | Manueller Modus (fester Setpoint) |
| `MATCHING = 2` | Automatischer Matching-Modus (P1-gesteuert, Lade+Entlade) |
| `MATCHING_DISCHARGE = 3` | Wie MATCHING, aber nur Entladen erlaubt |
| `MATCHING_CHARGE = 4` | Wie MATCHING, aber nur Laden erlaubt |
| `STORE_SOLAR = 5` | Solar-Speichermodus (überschüssige PV-Energie puffern) |

---

### `PowerFlowState`

Leistungsfluss-Zustand eines Geräts (ersetzt das frühere `ManagerState`-Enum).
Wird pro Gerät in `device.update_power_flow_state()` gesetzt und als HA-Entity
`power_flow_state` exportiert.

| Wert | Bedeutung |
|---|---|
| `OFF = 0` | Manager für dieses Gerät deaktiviert |
| `CHARGE = 1` | Gerät lädt aktiv (entspricht `AcMode.INPUT`) |
| `DISCHARGE = 2` | Gerät entlädt aktiv (entspricht `AcMode.OUTPUT`) |
| `IDLE = 3` | Kein messbarer Fluss; Bypass (Relais `pass`) wird hier ebenfalls als IDLE geführt |
| `WAKEUP = 5` | Übergang aus SOCEMPTY / aus Idle — Gerät wurde gerade reaktiviert |

> **Breaking Change:** Die numerischen Werte von `OFF` (früher 3) und `IDLE`
> (früher 0) wurden getauscht, damit `CHARGE=1` / `DISCHARGE=2` mit `AcMode`
> übereinstimmen. HA-Automationen oder Dashboards, die hartcodierte Integer-
> Vergleiche auf `power_flow_state` anwenden, müssen angepasst werden.

> **Kein `BYPASS` mehr:** Der frühere `PowerFlowState.BYPASS` wurde entfernt.
> Der Bypass-/Pass-Through-Zustand wird heute über die Klasse `BypassRelay`
> (`bypass_relay.py`) beschrieben — siehe
> [power-classification-bypass-description.md](power-classification-bypass-description.md).

---

### `FuseGroupType`

Beschreibt Sicherungsgruppen (Leitungsschutz) mit zugehörigen Lade-/Entlade-Limits.
Jeder Eintrag trägt einen numerischen Index, ein Label und die zulässigen
`maxpower` / `minpower` (in Watt, Entlade-Richtung positiv, Lade-Richtung negativ).

| Wert | Label | maxpower | minpower |
|---|---|---|---|
| `UNUSED` (0) | `unused` | 0 | 0 |
| `OWNCIRCUIT` (1) | `owncircuit` | 3600 | -3600 |
| `GROUP800` (2) | `group800` | 800 | -1200 |
| `GROUP800_2400` (3) | `group800_2400` | 800 | -2400 |
| `GROUP1200` (4) | `group1200` | 1200 | -1200 |
| `GROUP2000` (5) | `group2000` | 2000 | -2000 |
| `GROUP2400` (6) | `group2400` | 2400 | -2400 |
| `GROUP3600` (7) | `group3600` | 3600 | -3600 |

Hilfsmethoden:
- `FuseGroupType.as_select_dict()` — Dict `{index: label}` für HA-Select-Entities
- `FuseGroupType.from_label(label)` — Rückwärtslookup aus dem Label-String

---

## `SmartMode` — Zeitsteuerung & Schwellwerte

### Firmware-States

| Konstante | Wert | Bedeutung |
|---|---|---|
| `SOCFULL` | `1` | Firmware-seitiger SOCFULL-State (`socLimit=1`) |
| `SOCEMPTY` | `2` | Firmware-seitiger SOCEMPTY-State (`socLimit=2`) |
| `ZENSDK` | `2` | Alias für SOCEMPTY; SDK-interner Modus |
| `CONNECTED` | `10` | Gerät ist verbunden und bereit |

---

### Zykluszeiten (Throttle-Gate)

| Konstante | Wert (s) | Beschreibung |
|---|---|---|
| `TIMEFAST` | `1.5` | **Harter Blackout** nach jedem `_distribute_power`-Aufruf. Kein Lade-/Entladebefehl kann schneller als dieser Wert folgen — auch nicht bei `isFast=True`. |
| `TIMEZERO` | `4` | **Normaler Zyklustimer**: Mindestabstand zwischen zwei `_p1_changed`-Ausführungen im Normalfall. Kann durch `isFast` (hohe P1-Varianz) übersprungen werden. |

> Kommentar in `const.py` nennt als Default `TIMEFAST=2.2 s`; aktueller Code nutzt `1.5 s` für schnellere Reaktion.

---

### P1-Signalerkennung

| Konstante | Wert | Beschreibung |
|---|---|---|
| `P1_STDDEV_FACTOR` | `3.5` | Multiplikator für die P1-Standardabweichung. Ein neuer P1-Wert gilt als "signifikant" wenn: `abs(delta) > factor * stddev`. |
| `P1_STDDEV_MIN` | `15 W` | Untergrenze für die berechnete P1-Stddev. Verhindert, dass bei sehr stabilem Netz schon minimale Schwankungen als "isFast" gewertet werden. |
| `P1_MIN_UPDATE` | `400 ms` | Minimaler Abstand zwischen zwei P1-Updates (definiert, aber im aktuellen Code nicht ausgewertet). |

---

### Setpoint-Stabilitätserkennung

| Konstante | Wert | Beschreibung |
|---|---|---|
| `SETPOINT_STDDEV_FACTOR` | `5.0` | Multiplikator für die Standardabweichung des gleitenden Setpoint-Durchschnitts. Bestimmt, ob ein neuer Setpoint als "instabil" gilt und Hysterese ausgelöst wird. |
| `SETPOINT_STDDEV_MIN` | `50 W` | Untergrenze für die Setpoint-Stddev. Kleine Schwankungen unter 50 W lösen keine Hysterese aus. |

---

### HEMS-Timeout

| Konstante | Wert | Beschreibung |
|---|---|---|
| `HEMSOFF_TIMEOUT` | `60 s` | Wartezeit ohne MQTT-Update, bevor der HEMS-State auf `OFF` gesetzt wird. |

---

### Startverhalten

| Konstante | Wert | Beschreibung |
|---|---|---|
| `POWER_START` | `50 W` | Minimale Leistung zum Aufwecken eines Geräts aus dem Idle-Zustand. Wird als Puls gesendet: `charge(-POWER_START - pwr_offgrid)`. |
| `POWER_TOLERANCE` | `10 W` | Mindestdifferenz zwischen geplantem und zuletzt gesendeten Setpoint. Liegt die Differenz darunter, wird kein neuer Befehl geschickt (Flattern vermeiden). |
| `BYPASS_WAKE_COOLDOWN` | `60 s` | Minimaler Abstand zwischen zwei Bypass-Wake-Pass-1-Kommandos pro Gerät. Verhindert, dass ein passives Gerät in Bypass-Stellung mehrfach pro Minute angestupst wird. |
| `WAKEUP_RAMP_DURATION` | `30 s` | Dauer des Soft-Start-Rampens nach einem `WAKEUP → CHARGE/DISCHARGE`-Übergang. Der Setpoint wird während dieser Zeit schrittweise auf den Zielwert angehoben. |
| `WAKE_TIMEOUT` | `15 s` | Maximale Verweildauer im `PowerFlowState.WAKEUP`. Läuft diese ab, ohne dass Leistung fließt, wird das Gerät wieder als `IDLE` klassifiziert. |

---

### Hysterese (Flattern-Schutz)

| Konstante | Wert | Beschreibung |
|---|---|---|
| `HYSTERESIS_START_FACTOR` | `1.5` | Multiplikator auf `charge_start` / `discharge_start`. Ein Gerät baut intern `pwr_low` auf, wenn der Setpoint unter `1.5 × start_power` liegt. Verhindert, dass Geräte kurz gestartet und sofort wieder gestoppt werden. |
| `WAKEUP_CAPACITY_FACTOR` | `2` | Multiplikator auf `charge_optimal` / `discharge_optimal`. Ein Idle-Gerät wird reaktiviert, wenn die laufenden Geräte mehr als `2 × optimal` leisten müssten. |
| `SOC_IDLE_BUFFER` | `3 %` | SoC-Puffer bei der Idle-Entscheidung (`idle_lvlmax - 3%`). Verhindert Hin-und-her-Springen durch kleine SoC-Messfehler. |

---

### Charge-Cooldown-Zeiten

| Konstante | Wert | Beschreibung |
|---|---|---|
| `HYSTERESIS_LONG_COOLDOWN` | `300 s` | Referenzzeitraum (5 Min): War der letzte Lade-Stop länger als 300 s her, gilt der "schnelle" Wiedereinstieg. Liegt er darunter, wird der "langsame" Pfad gewählt. |
| `HYSTERESIS_FAST_COOLDOWN` | `5 s` | Wartezeit im schnellen Cooldown-Pfad. Gerät darf wenige Sekunden nach dem letzten Stopp wieder starten. |
| `HYSTERESIS_SLOW_COOLDOWN` | `20 s` | Wartezeit im langsamen Cooldown-Pfad. Schützt das Netz vor schnellen Lastwechseln nach einem Lade-Stopp. |

---

### Hardware-Quirks

| Konstante | Wert | Beschreibung |
|---|---|---|
| `POWER_IDLE_OFFSET` | `10 W` | Offset für Wechselrichter (z. B. SF2400), die bei einem exakten 0-W-Befehl in den Standby fallen. Statt `0 W` wird `10 W` gesendet, um den aktiven Durchleitbetrieb aufrechtzuerhalten. |
| `DISCHARGE_SOC_BUFFER` | `2 %` | SoC-Puffer über `minSoc`, ab dem die HA-Steuerung das Entladen einstellt. Unterhalb `minSoc + 2 %` wird kein Discharge-Wakeup mehr ausgelöst; der Wechselrichter (z. B. SF2400 AC) darf die restlichen 2 % selbst abbauen. |