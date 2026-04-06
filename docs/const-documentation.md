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

Klassifizierter Betriebszustand eines Geräts, berechnet in `device.setStatus()`.

| Wert | Bedeutung |
|---|---|
| `OFFLINE = 0` | Kein MQTT-Kontakt seit Timeout |
| `SOCEMPTY = 1` | Akku leer (unter `minSoc`); Gerät darf nur laden, nicht entladen |
| `INACTIVE = 2` | Gerät erreichbar, aktuell inaktiv (kein Leistungsfluss) |
| `SOCFULL = 3` | Akku voll (SoC = 100%); Gerät darf nur entladen, nicht laden |
| `ACTIVE = 4` | Gerät aktiv mit messbarem Leistungsfluss |

> `SOCEMPTY` entspricht `socLimit=2` auf Firmware-Ebene (→ `SmartMode.SOCEMPTY`).

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

### `ManagerState`

Aktueller Zustand des Managers innerhalb eines Zyklus.

| Wert | Bedeutung |
|---|---|
| `IDLE = 0` | Kein aktiver Lade- oder Entladevorgang |
| `CHARGE = 1` | Manager verteilt gerade Ladepower |
| `DISCHARGE = 2` | Manager verteilt gerade Entladepower |
| `OFF = 3` | Manager ist abgeschaltet |

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
| `TIMEFAST` | `0.7` | **Harter Blackout** nach jedem `_distribute_power`-Aufruf. Kein Lade-/Entladebefehl kann schneller als dieser Wert folgen — auch nicht bei `isFast=True`. |
| `TIMEZERO` | `1` | **Normaler Zyklustimer**: Mindestabstand zwischen zwei `_p1_changed`-Ausführungen im Normalfall. Kann durch `isFast` (hohe P1-Varianz) übersprungen werden. |

> Standardwerte in Kommentaren: `TIMEFAST` default 2.2 s, `TIMEZERO` default 4 s. Aktuelle Werte sind reduziert für schnellere Reaktion.

---

### P1-Signalerkennung

| Konstante | Wert | Beschreibung |
|---|---|---|
| `P1_STDDEV_FACTOR` | `3.5` | Multiplikator für die P1-Standardabweichung. Ein neuer P1-Wert gilt als "signifikant" wenn: `abs(delta) > factor * stddev`. |
| `P1_STDDEV_MIN` | `3 W` | Untergrenze für die berechnete P1-Stddev. Verhindert, dass bei sehr stabilem Netz schon minimale Schwankungen als "isFast" gewertet werden. |
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
| `POWER_TOLERANCE` | `5 W` | Mindestdifferenz zwischen geplantem und zuletzt gesendeten Setpoint. Liegt die Differenz darunter, wird kein neuer Befehl geschickt (Flattern vermeiden). |

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
| `HYSTERESIS_FAST_COOLDOWN` | `2 s` | Wartezeit im schnellen Cooldown-Pfad. Gerät darf 2 s nach dem letzten Stopp wieder starten. |
| `HYSTERESIS_SLOW_COOLDOWN` | `60 s` | Wartezeit im langsamen Cooldown-Pfad (1 Min). Schützt das Netz vor schnellen Lastwechseln nach einem Lade-Stopp. |

---

### Hardware-Quirks

| Konstante | Wert | Beschreibung |
|---|---|---|
| `POWER_IDLE_OFFSET` | `10 W` | Offset für Wechselrichter (z. B. SF2400), die bei einem exakten 0-W-Befehl in den Standby fallen. Statt `0 W` wird `10 W` gesendet, um den aktiven Durchleitbetrieb aufrechtzuerhalten. |