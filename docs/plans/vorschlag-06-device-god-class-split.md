# Vorschlag 06: `ZendureDevice` — God-Class aufteilen

## Status: Umgesetzt

Drei Kollaboratoren extrahiert in `custom_components/zendure_ha/device_components.py`:

- `DevicePortBundle` — kapselt Aufbau und Liste aller `PowerPort`-Instanzen.
- `DevicePowerFlowStateMachine` — enthält die ehemalige `update_power_flow_state`-Logik
  und ist isoliert testbar (siehe `tests/test_device_state_machine.py`).
- `MqttProtocolHandler` — Objektfassade über `mqtt_protocol`, mit lazy-Import,
  damit Tests keine Home-Assistant-Abhängigkeiten ziehen.

`ZendureDevice` hält die drei Kollaboratoren (`self.port_bundle`,
`self.state_machine`, `self.mqtt_handler`) und stellt die bisherigen
Port-Attribute (`batteryPort`, `connectorPort`, `solarPort`, `offgridPort`,
`inverterLossPort`, `ports`) als Properties bereit, damit aufrufende Module
(`manager.py`, `power_strategy.py`, `fusegroup.py`, `mqtt_protocol.py`,
`power_port.py`, Subklassen in `devices/`) unverändert funktionieren.

`update_power_flow_state()` ist zur reinen Delegation auf die State-Maschine
geschrumpft; `mqttPublish/Invoke/Properties/Message/entityWrite` delegieren
an den Handler.

## Priorität: Hoch

## Problem

`ZendureDevice` in `device.py` (ca. Zeile 40–376) vermischt mehrere Verantwortlichkeiten:

- MQTT-Protokoll-Handling (Topics, Payloads, Dispatcher)
- Sensor- und Entity-Lifecycle (Registrierung, Update-Callbacks)
- Power-Port-Initialisierung (`batteryPort`, `connectorPort`, `solarPort`, `offgridPort`, `inverterLossPort`)
- State-Maschine (`DeviceState`, `PowerFlowState`, `update_power_flow_state` bei `device.py:335-367`)
- Leistungssteuerung (`power_charge` / `power_discharge` bei `device.py:294-315`)
- Abgeleitete Bilanzen (`pwr_produced`, `offgrid_power`)

Konkrete Symptome:
- Eine einzige Klasse mit >300 Zeilen und ~10 öffentlichen Konzepten
- Subklassen (`ZendureLegacy`, device-spezifische Varianten) erben den kompletten Mix
- Unit-Tests zwingen zur Instanziierung des gesamten Geräts, auch wenn nur die
  State-Transitions getestet werden sollen

## Warum notwendig

- **Wartung:** Jede neue Gerätefamilie (Hub, ACE, SF) muss durch alle Concerns
  navigieren, selbst wenn sie nur einen ändern will.
- **Testbarkeit:** `update_power_flow_state()` lässt sich nicht isoliert testen,
  ohne MQTT-Sensoren zu mocken.
- **Erweiterbarkeit:** Neue Ports (z. B. `InverterLossPowerPort`, bereits hinzugefügt)
  erhöhen die God-Class weiter.

## Warum hilfreich

- Jede extrahierte Komponente kann separat getestet werden (reine State-Logik,
  reines Port-Setup).
- Subklassen können gezielt einzelne Komponenten überschreiben statt Methoden-
  Spaghetti.
- Code-Reviews werden lokaler: PRs, die nur die MQTT-Schicht anfassen, berühren
  nicht die State-Maschine.

## Lösungsskizze

Drei Kollaborator-Klassen, die `ZendureDevice` via Composition hält:

```python
class MqttProtocolHandler:
    """MQTT topic subscription, payload dispatch, write-commands."""

class DevicePowerFlowStateMachine:
    """state (DeviceState) + power_flow_state + update_power_flow_state()."""

class DevicePortBundle:
    """Alle PowerPort-Instanzen + deren Initialisierung pro Gerätetyp."""
```

`ZendureDevice` wird zur Fassade:

```python
class ZendureDevice:
    def __init__(...):
        self.ports = DevicePortBundle(self, model)
        self.state_machine = DevicePowerFlowStateMachine(self.ports)
        self.mqtt = MqttProtocolHandler(self)
```

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `device.py` | 40-376 (gesamte `ZendureDevice`-Klasse) |
| Subklassen wie `ZendureLegacy` (device.py:379) | Anpassung an Facade-API |
| `manager.py` | ggf. Zugriff auf `d.ports.*` statt `d.*Port` |

## Risiko

**Hoch.** Zentrale Klasse mit vielen Abhängigkeiten (Manager, PowerStrategy,
Entity-Layer). Refactoring ohne Test-Suite ist gefährlich.

## Abwägung

- **Pain Point heute:** Jede Änderung an Device kann Seiteneffekte in jedem Bereich haben.
- **Kosten Nicht-Handeln:** Die Klasse wächst mit jedem neuen Gerätetyp weiter.
- **Kosten des Refactorings:** Hoch — ~1–2 Tage + manueller Regressionstest an echten Geräten.
- **Netto-Nutzen:** Wird erst mit Tests voll wirksam; ohne Tests bleibt Risiko > Nutzen.
- **Alternative:** Nur `DevicePortBundle` extrahieren (kleinster Schritt), State-
  Maschine und MQTT-Layer später.

## Abhängigkeiten

- Sollte **nach** `step-04` (op_state auf Device) erfolgen, sonst Merge-Konflikte.
- Profitiert von erst hinzugefügten Unit-Tests für `update_power_flow_state`.
