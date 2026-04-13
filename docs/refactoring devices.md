Das ist ein hervorragender Ansatz für eine Refaktorisierung. Aktuell vermischen sich in der `powerChanged`-Methode des Managers die Sensor-Werte (`pwr_offgrid`, `solarInput`, `homeInput/Output`) direkt mit der komplexen Mathematik zur Nulleinspeisung. Das macht den Code schwer erweiterbar und schwer testbar.

Die ideale Lösung hierfür ist das **Strategy- bzw. Port-Pattern**. Wir definieren eine abstrakte Schnittstelle (Base Class) für einen "Strom-Anschlusspunkt" (Port). Jede Strom-Art wird ein eigener Port.

### Das Konzept: Der "Power Port"
1. **Grid Port (AC Netz):** Liest den P1-Zähler. Ist bidirektional (Import/Export).
2. **Offgrid Port (Integrierte Steckdose):** Liest `pwr_offgrid`. Ist bidirektional, aber verbrauchsdominant.
3. **DC Solar Port (PV-Eingang):** Liest `solarInput`. Ist **nur Input**. 

**Wichtige mathematische Konvention für alle Ports:**
*   **Positiver Wert (+):** Verbrauch (Zieht Strom aus dem System/hinaus)
*   **Negativer Wert (-):** Erzeugung (Speist Strom in das System ein)

Hier ist die Umsetzung, wie du das Projekt sauber strukturieren kannst.

---

### 1. Neue Datei: `power_port.py`
Erstelle diese Datei im Hauptverzeichnis deiner Integration. Sie enthält die abstrakte Definition und die konkreten Implementierungen.

```python
"""Abstrakte und konkrete Strom-Anschlusspunkte (Power Ports) für das Zendure-Management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .device import ZendureDevice


class PowerPort(ABC):
    """Abstrakte Basisklasse für jeden Strom-Input/Output."""
    
    def __init__(self, name: str, is_input_only: bool = False):
        self.name = name
        self.is_input_only = is_input_only

    @property
    @abstractmethod
    def power(self) -> int:
        """
        Gibt die aktuelle Leistung in Watt zurück.
        Konvention: + = Verbrauch, - = Erzeugung.
        """
        pass


class GridSmartmeter(PowerPort):
    """Representiert den P1 Zähler (AC Strom vom Netz)."""
    
    def __init__(self):
        super().__init__(name="AC Grid (P1)", is_input_only=False)
        self._power = 0

    def update_state(self, power: int):
        """Wird vom Manager aufgerufen, wenn sich der P1-Zähler ändert."""
        self._power = power

    @property
    def power(self) -> int:
        # P1 Positiv = Netzbezug (Verbrauch), P1 Negativ = Einspeisung (Erzeugung)
        return self._power


class OffGridPowerPort(PowerPort):
    """Representiert die integrierte Offgrid-Steckdose (Input/Output)."""
    
    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"Offgrid ({device.name})", is_input_only=False)
        self.device = device

    @property
    def power(self) -> int:
        """
        Im Zendure-Ökosystem kann pwr_offgrid negativ sein, wenn intern 
        Solarstrom an der Steckdose anliegt. Das zählt aber eigentlich zum 
        DC Solar Port. Für die Nulleinspeisung ist hier nur der reine 
        Verbrauch (Last an der Steckdose) relevant.
        """
        return max(0, self.device.pwr_offgrid)


class DcSolarPowerPort(PowerPort):
    """Representiert das integrierte PV-Gerät (Nur Input)."""
    
    def __init__(self, device: ZendureDevice):
        super().__init__(name=f"DC Solar ({device.name})", is_input_only=True)
        self.device = device

    @property
    def power(self) -> int:
        """
        Für die Basis-Setpoint-Berechnung ist DC Solar "unsichtbar", 
        da der Wechselrichter den Solarstrom automatisch durchreicht 
        (P1 reagiert darauf ohnehin).
        Daher gibt diese Methode 0 zurück.
        """
        return 0

    @property
    def raw_solar_input(self) -> int:
        """Gibt den reinen Solar-Input für spezielle Logiken (z.B. SOCFULL Bypass) zurück."""
        return self.device.solarInput.asInt
```

---

### 2. Anpassung in `manager.py`

Jetzt müssen wir den Manager von den hartkodierten Sensor-Namen entkoppeln und stattdessen mit den Ports arbeiten.

#### A) Import hinzufügen
Ganz oben in `manager.py`:
```python
from .power_port import DcSolarPowerPort, GridSmartmeter, OffGridPowerPort
```

#### B) Initialisierung in `__init__` und `loadDevices`
In der `__init__` Methode des `ZendureManager` fügst du den Grid-Port hinzu:
```python
# Statt nur self.p1_factor = 1
self.grid_port = GridSmartmeter()
self.p1_factor = 1
```

Ganz unten in `loadDevices()`, nachdem `self.devices = list(self.api.devices.values())` steht, initialisieren wir die Geräte-Ports modular:
```python
self.devices = list(self.api.devices.values())
_LOGGER.info("Loaded %s devices", len(self.devices))

# --- NEU: Modulare Port-Initialisierung ---
self.device_ports: dict[str, list[PowerPort]] = {}
for device in self.devices:
    ports = []
    
    # Nur Ports hinzufügen, wenn das Gerät diese Features auch physisch hat
    # (Annahme: pwr_offgrid > 0 oder maxSolar > 0 sind Indikatoren)
    if hasattr(device, 'pwr_offgrid') and device.maxSolar > 0:
        ports.append(OffGridPowerPort(device))
    if hasattr(device, 'solarInput') and device.maxSolar > 0:
        ports.append(DcSolarPowerPort(device))
        
    if ports:
        self.device_ports[device.deviceId] = ports
# ----------------------------------------
```

#### C) Die `powerChanged`-Methode aufräumen
Hier wird der Code durch die Ports endlich lesbar. Ersetze die relevante Schleife in `powerChanged` durch diese modularisierte Version:

```python
    async def powerChanged(self, p1: int, isFast: bool, time: datetime) -> None:


    """Return the distribution setpoint."""
availableKwh = 0
# 1. Basis-Setpoint ist immer der aktuelle Netz-Bezug/-Export
setpoint = self.grid_port.power
power = 0

for d in self.devices:
    # 2. Hole die modularen Ports für dieses spezifische Gerät
    ports = self.device_ports.get(d.deviceId, [])
    offgrid_port = next((p for p in ports if isinstance(p, OffGridPowerPort)), None)
    solar_port = next((p for p in ports if isinstance(p, DcSolarPowerPort)), None)

    if await d.power_get():
        # 3. Entkoppelte Berechnung der unkontrollierten Einflüsse
        offgrid_power = offgrid_port.power if offgrid_port else 0
        solar_power = solar_port.raw_solar_input if solar_port else 0

        # pwr_produced Logik für SOCFULL-Bypass (braucht den reinen Solarwert)
        d.pwr_produced = min(0,
                             d.batteryOutput.asInt + d.homeInput.asInt - d.batteryInput.asInt - d.homeOutput.asInt - solar_power)
        self.produced -= d.pwr_produced

        # --- Klassifizierung (Charge / Discharge / Idle) ---
        if d.state == DeviceState.SOCEMPTY and d.batteryInput.asInt == 0 and d.homeOutput.asInt == 0:
            home = 0
            self.idle.append(d)
            self.idle_lvlmax = max(self.idle_lvlmax, d.electricLevel.asInt)
            self.idle_lvlmin = min(self.idle_lvlmin, d.electricLevel.asInt)

        elif (home := -d.homeInput.asInt + offgrid_power) < 0:
            self.charge.append(d)
            self.update_charge_limit += d.fuseGrp.update_charge_limit()
            self.charge_optimal += d.charge_optimal
            self.charge_weight += d.pwr_max * (100 - d.electricLevel.asInt)
            setpoint += -d.homeInput.asInt

        elif (home := d.homeOutput.asInt) > 0 or offgrid_power > 0:
            self.discharge.append(d)
            self.discharge_bypass -= d.pwr_produced if d.state == DeviceState.SOCFULL else 0
            self.discharge_limit += d.fuseGrp.discharge_limit(d)
            self.discharge_optimal += d.discharge_optimal
            self.discharge_produced -= d.pwr_produced
            self.discharge_weight += d.pwr_max * d.electricLevel.asInt

            # NEUE MATHEMATIK FÜR AC-WECHSELRICHTER (SolarFlow 2400 AC)
            net_battery = home - offgrid_power

            if home == 0 and net_battery <= 0:
                pass  # WAKEUP Logik...
            else:
                setpoint += home
        else:
            self.idle.append(d)
            self.idle_lvlmax = max(self.idle_lvlmax, d.electricLevel.asInt)
            self.idle_lvlmin = min(self.idle_lvlmin,
                                   d.electricLevel.asInt if d.state != DeviceState.SOCFULL else 100)

        availableKwh += d.actualKwh
        power += offgrid_power + home + d.pwr_produced

# ... [Rest der powerChanged Methode bleibt exakt gleich!] ...
self.power.update_value(power)
self.availableKwh.update_value(availableKwh)

if self.discharge_bypass > 0:
    setpoint = max(0 if p1 >= 0 else setpoint - self.discharge_bypass, setpoint - self.discharge_bypass)

_LOGGER.info("P1 ======> p1:%s isFast:%s, setpoint:%sW stored:%sW", p1, isFast, setpoint, self.produced)

# ... [Match/Case Block für ManagerMode bleibt gleich] ...
```

#### D) P1 Update anpassen
In der Methode `_p1_changed` aktualisierst du nun den Port statt nur einer Variablen:

```python
    async def _p1_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self.hass.is_running or (new_state := event.data["new_state"]) is None:
            return

        try:
            p1 = int(self.p1_factor * float(new_state.state))
        except ValueError:
            return

        # --- NEU: Zustand an den Port delegieren ---
        self.grid_port.update_state(p1)
        # -------------------------------------------

        time = datetime.now()
        # ... [Rest bleibt gleich] ...
```

---

### Warum ist diese Struktur jetzt viel besser?

1. **Erweiterbarkeit:** Wenn Zendure nächstes Jahr ein Gerät mit einem *zweiten*, unabhängigen AC-Ausgang (z.B. für eine Wärmepumpe) herausbringt, musst du nur eine neue Klasse `SecondaryAcPort(PowerPort)` erstellen und in `loadDevices` appenden. Die Mathematik in `manager.py` bleibt komplett unberührt.
2. **Testbarkeit:** Du kannst jetzt in Unit-Tests Mock-Ports erstellen: `port = GridSmartmeter(); port.update_state(-500)`. Du musst nicht mehr ein ganzes `ZendureDevice` mit 20 Sensoren mocken, nur um den Manager zu testen.
3. **Saubereres Interface:** Das `manager.py` weiß nicht mehr, *wie* ein Gerät seinen Offgrid-Strom misst (ob über `pwr_offgrid` oder einen anderen Sensor). Es fragt den Port einfach: `"Wie viel verbrauchst du gerade?"` und bekommt eine saubere Integer zurück.
4. **DC-Solar-Isolierung:** Durch `is_input_only = True` im `DcSolarPowerPort` ist im Code sofort ersichtlich, dass dieser Anschlusspunkt den Setpoint nicht beeinflusst, sondern reine "Info" für den SOCFULL-Bypass ist.