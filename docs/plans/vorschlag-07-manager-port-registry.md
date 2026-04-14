# Vorschlag 07: `DevicePortRegistry` — Manager ↔ Device Port-Init entkoppeln

## Priorität: Hoch

## Problem

`ZendureManager` (`manager.py`, ca. 474 Zeilen) hält gleichzeitig:

- `devices` (Liste)
- `fuse_groups`
- `grid_port`
- implizit Geräte-spezifische Ports (über `d.batteryPort`, `d.connectorPort`, …)

Die Port-Initialisierung ist zwischen Manager und Device zerstreut:
- Der Manager legt den `grid_port` an.
- Das Device legt seine lokalen Ports im Konstruktor / `_init_power_ports` an.
- `power_strategy.py` greift quer durch die Hierarchie hindurch auf beides zu
  (`mgr.grid_port.power` in `power_strategy.py:346`, `d.connectorPort.power_consumption`
  u. ä.).

Konsequenz: keine zentrale Stelle, an der man alle Ports eines Setups inspizieren,
loggen oder mocken kann.

## Warum notwendig

- **Kopplung:** Neue Port-Typen müssen an zwei Stellen (Device + Manager)
  eingepflegt werden.
- **Diagnose:** Es gibt kein einheitliches Log/Debug-Bild „alle Ports im System".
- **Tests:** Integrationstests brauchen einen vollständigen Manager mit echten
  Devices, nur um einen Port zu inspizieren.

## Warum hilfreich

- Ein `DevicePortRegistry` kapselt die Port-Topologie als eigenständiges Objekt;
  Tests können es direkt mit Fake-Ports instanziieren.
- Wird zur natürlichen Stelle für offgrid-Bilanz (step-07), Selfconsumption
  (vorschlag-04) und zukünftige Cross-Device-Views (z. B. Gesamt-Solarertrag).
- Reduziert die `ZendureManager`-Klasse auf ihre eigentliche Rolle: Orchestrierung.

## Lösungsskizze

```python
@dataclass
class DevicePortRegistry:
    grid_port: GridPort
    device_ports: dict[str, DevicePortBundle]  # deviceId → bundle

    def all_battery_ports(self) -> list[BatteryPowerPort]: ...
    def all_offgrid_ports(self) -> list[OffGridPowerPort]: ...
    def total_solar_power(self) -> int: ...
```

`ZendureManager` hält eine einzige `self.ports: DevicePortRegistry` und delegiert.

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `manager.py` | 52-400 (Registry-Integration, `_load_single_device`) |
| `device.py` | 40-100 (Port-Init umziehen lassen) |
| `power_strategy.py` | Zugriffe auf `mgr.grid_port` → `mgr.ports.grid_port` |

## Risiko

**Mittel bis hoch.** Breite Code-Oberfläche; aber mechanisch (nur Zugriffe
umleiten). Gut per grep nachvollziehbar.

## Abwägung

- **Pain Point heute:** Jede Port-Frage landet bei Manager **und** Device.
- **Kosten Nicht-Handeln:** Wächst mit jedem neuen Port-Typ linear.
- **Kosten des Refactorings:** Mittel — viele Call-Sites, aber jede ist trivial.
- **Netto-Nutzen:** Freischaltet saubere Tests für Bilanz-/Selfconsumption-Logik.
- **Alternative:** Nur eine Read-Only-View-Klasse (z. B. `PortView`) einführen,
  ohne die Ownership zu ändern — reduziert Risiko auf gering.

## Abhängigkeiten

- Passt gut **nach** `vorschlag-06` (DevicePortBundle existiert dann bereits).
- Kein Konflikt mit `step-07` (offgrid-Bilanz) — profitiert sogar davon.
