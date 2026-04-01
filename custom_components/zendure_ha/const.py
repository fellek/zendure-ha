"""Constants for Zendure."""

from datetime import timedelta
from enum import Enum

DOMAIN = "zendure_ha"

CONF_APPTOKEN = "token"
CONF_P1METER = "p1meter"
CONF_PRICE = "price"
CONF_MQTTLOG = "mqttlog"
CONF_MQTTLOCAL = "mqttlocal"
CONF_MQTTSERVER = "mqttserver"
CONF_SIM = "simulation"
CONF_MQTTPORT = "mqttport"
CONF_MQTTUSER = "mqttuser"
CONF_MQTTPSW = "mqttpsw"
CONF_WIFISSID = "wifissid"
CONF_WIFIPSW = "wifipsw"
CONF_AUTO_MQTT_USER = "auto_mqtt_user"

CONF_HAKEY = "C*dafwArEOXK"


class AcMode:
    INPUT = 1
    OUTPUT = 2


class DeviceState(Enum):
    OFFLINE = 0
    SOCEMPTY = 1
    INACTIVE = 2
    SOCFULL = 3
    ACTIVE = 4


class ManagerMode(Enum):
    OFF = 0
    MANUAL = 1
    MATCHING = 2
    MATCHING_DISCHARGE = 3
    MATCHING_CHARGE = 4
    STORE_SOLAR = 5


class ManagerState(Enum):
    IDLE = 0
    CHARGE = 1
    DISCHARGE = 2
    OFF = 3


class SmartMode:
    SOCFULL = 1
    SOCEMPTY = 2
    ZENSDK = 2
    CONNECTED = 10

    TIMEFAST = 2.2  # Fast update interval after significant change
    TIMEZERO = 4  # Normal update interval

    # Standard deviation thresholds for detecting significant changes
    P1_STDDEV_FACTOR = 3.5  # Multiplier for P1 meter stddev calculation
    P1_STDDEV_MIN = 15  # Minimum stddev value for P1 changes (watts)
    P1_MIN_UPDATE = timedelta(milliseconds=400)
    SETPOINT_STDDEV_FACTOR = 5.0  # Multiplier for power average stddev calculation
    SETPOINT_STDDEV_MIN = 50  # Minimum stddev value for power average (watts)

    HEMSOFF_TIMEOUT = 60  # Seconds before HEMS state is set to OFF if no updates are received

    POWER_START = 50  # Minimum Power (W) for starting a device
    POWER_TOLERANCE = 5  # Device-level power tolerance (W) before updating

    # ====================================================================
    # NEU: Power Distribution & Hysteresis Konstanten
    # ====================================================================

    # --- Hysteresis (Verhindert Flattern beim Starten/Stoppen) ---
    HYSTERESIS_START_FACTOR = 1.5
    # Erklärung: Multiplikator für die Start-Leistung eines Geräts (charge_start / discharge_start).
    # Wenn das Gerät weniger als 150% seiner Start-Leistung bekommt, baut sich "pwr_low" auf.
    # Wert erhöhen = Geräte starten schwerer/schneller aus. Wert verringern = toleranter.

    WAKEUP_CAPACITY_FACTOR = 2
    # Erklärung: Multiplikator für die optimale Leistung (charge_optimal / discharge_optimal).
    # Berechnet, ob ein "idle"- Gerät reaktiviert werden muss, weil die anderen überlastet sind.
    # Wert erhöhen = Mehr Puffer, bevor ein weiteres Gerät dazukommt.

    SOC_IDLE_BUFFER = 3
    # Erklärung: SoC-Puffer in % bei der Entscheidung, ob ein Gerät als "ausgleichend" gilt.
    # (z.B. idle_lvlmax - 3%). Verhindert, dass Geräte wegen kleiner SoC-Messfehler
    # ständig zwischen Idle und Active hin und her springen.

    # --- Timing: Charge Cooldown (Hysterese-Zeitfenster) ---
    HYSTERESIS_LONG_COOLDOWN = 300
    # Erklärung: Zeit in Sekunden (5 Min), die vergangen sein muss, seit dem letzten Lade-Stop,
    # damit das System beim nächsten Überschuss "schnell" (FAST_COOLDOWN) wieder anfängt zu laden.
    # Wenn weniger Zeit vergangen ist, wird der lange (SLOW_COOLDOWN) Weg genommen.

    HYSTERESIS_FAST_COOLDOWN = 2
    # Erklärung: Zeit in Sekunden, die der Manager bei "schnellem" Cooldown wartet,
    # bevor er den Lade-Setpoint wieder freigibt.

    HYSTERESIS_SLOW_COOLDOWN = 60
    # Erklärung: Zeit in Sekunden (1 Min), die der Manager bei "langsamem" Cooldown wartet.
    # Schützt das Netz vor schnellen Lastwechseln, wenn der Akku gerade erst gestoppt hat.

    # --- Hardware Quirks (Inverter Eigenheiten) ---
    POWER_IDLE_OFFSET = 10
    # Erklärung: Manche Wechselrichter (z.B. SF2400) zeigen beim reinen Durchleiten
    # (Bypass/Offgrid) parasitäre Leistung oder schalten sich ab, wenn exakt 0W gefordert werden.
    # Dieses Offset (10W) wird gesetzt, wenn ein Gerät eigentlich 0W machen soll,
    # aber das MQTT-Kommando "10W" benötigt, um nicht in den Standby zu fallen.