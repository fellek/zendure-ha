# Vorschlag 09: `InverterLossPowerPort` — Modell-Werte konfigurierbar machen

## Priorität: Mittel

## Problem

`InverterLossPowerPort.__init__` (`power_port.py:171-176`) hat hart kodierte
Default-Werte für SF2400AC:

```python
# @todo replace magic numbers with vars
def __init__(self, device: ZendureDevice, model_active_w: int = 45, model_standby_w: int = 25):
    super().__init__(name=f"Inverter Loss ({device.name})", is_input_only=False)
    self.device = device
    self._model_active_w = model_active_w
    self._model_standby_w = model_standby_w
```

Andere Gerätefamilien (Hub 1200/2000, ACE 1500, SuperBase) haben abweichende
Verluste — aktuell werden sie mit SF2400-Werten geschätzt.

## Warum notwendig

- **Datenqualität:** Selfconsumption-Sensor liefert für Nicht-SF-Geräte falsche
  Werte → Bilanzen (step-07) und P1-Matching werden verzerrt.
- **Skalierbarkeit:** Jedes neue Gerätemodell erfordert Code-Änderung statt
  Konfigurationseintrag.
- **Dokumentation:** Der Kommentar `# @todo replace magic numbers with vars`
  ist bereits vorhanden — die Absicht existiert, nur die Umsetzung fehlt.

## Warum hilfreich

- Ein zentraler Konstanten-Block macht die Modellwerte sichtbar und kalibrierbar.
- Community-Contributions (neue Modelle) werden zu einer Dict-Zeile, nicht
  einer Code-Änderung.
- Leichtere A/B-Tests: Modellwerte per Config-Option überschreibbar.

## Lösungsskizze

In `const.py`:

```python
class InverterLossModel:
    """Selbstverbrauchs-Schätzungen pro Gerätemodell (aktiv, standby) in Watt."""
    SF2400AC = (45, 25)     # P1-kreuzvalidiert 2026-04-14
    HUB1200  = (12, 8)      # @todo kalibrieren
    HUB2000  = (20, 12)     # @todo kalibrieren
    ACE1500  = (25, 15)     # @todo kalibrieren
    DEFAULT  = (30, 20)     # Fallback

    @classmethod
    def for_model(cls, model: str) -> tuple[int, int]:
        return getattr(cls, model.upper().replace("-", ""), cls.DEFAULT)
```

`InverterLossPowerPort.__init__` liest daraus:

```python
def __init__(self, device: ZendureDevice):
    super().__init__(name=f"Inverter Loss ({device.name})", is_input_only=False)
    self.device = device
    self._model_active_w, self._model_standby_w = InverterLossModel.for_model(device.model)
```

## Betroffene Dateien

| Datei | Bereich |
|-------|---------|
| `const.py` | neue Klasse `InverterLossModel` |
| `power_port.py` | 171-176 |
| `device.py` | Aufrufstelle der Port-Initialisierung |

## Risiko

**Gering.** Nur Default-Werte werden verschoben; Verhalten für SF2400 bleibt
identisch.

## Abwägung

- **Pain Point heute:** Falsche Werte für Nicht-SF-Geräte, aber stumm.
- **Kosten Nicht-Handeln:** Bleibt verborgen, bis User Selfconsumption für Hub
  prüft und sich wundert.
- **Kosten des Refactorings:** Minimal — 20 Zeilen.
- **Netto-Nutzen:** Hoch; Voraussetzung für verlässliche Bilanz.
- **Alternative:** Erst nur den SF2400-Wert in eine Konstante extrahieren,
  Modell-Dispatch später.

## Abhängigkeiten

- Unabhängig; kann sofort umgesetzt werden.
- Ergänzt `vorschlag-04` (Selfconsumption-Sensor).
