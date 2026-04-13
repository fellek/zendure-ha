# Vorschlag 04: Selfconsumption virtueller Sensor

## Priorität: Niedrig

## Problem

Der SF2400AC hat einen Eigenverbrauch von ca. 40W beim Laden. Dieser wird nirgends
bilanziert und führt zu systematischen Fehlern in der Setpoint-Berechnung.

## Details (aus @todo in power_strategy.py:39)

```
# @todo calculate Selfconsumption with virtual Sensor?
#  for SF2400AC selfconsumption on charging is about 40W.
#  If Input Power is below 40W (charge(<40), with pwr_offgrid=0)
#  then Inverter consumes Batterypower to stay alive.
#  On charging SF2400AC with pwr_offgrid > 0, selfconsumption is added to pwr_offgrid on top.
```

### Szenarien

| Zustand | Eigenverbrauch-Quelle | Sichtbar am P1? |
|---------|----------------------|-----------------|
| Laden < 40W, kein Offgrid | Batterie (!) | Nein |
| Laden > 40W, kein Offgrid | Teil des Ladestroms | Ja (im gridConsumption) |
| Laden mit Offgrid > 0 | Offgrid + Grid | Teilweise |

## Lösungsansatz

Virtuellen Sensor `selfconsumption_power` im Device erstellen, der den geschätzten
Eigenverbrauch basierend auf dem Betriebszustand berechnet:

```python
@property
def selfconsumption(self) -> int:
    """Geschätzter Eigenverbrauch des Inverters in Watt."""
    if self.state == DeviceState.OFFLINE:
        return 0
    if self.power_flow_state in (PowerFlowState.CHARGE, PowerFlowState.DISCHARGE):
        return 40  # SF2400AC spezifisch
    return 10  # Standby
```

Dieser Wert könnte in die Setpoint-Berechnung einfließen, um die 40W-Abweichung
zwischen berechnetem und tatsächlichem Ladestrom zu kompensieren.

## Offene Fragen

1. Ist der Eigenverbrauch gerätemodell-abhängig? (Vermutlich ja)
2. Variiert er mit der Temperatur? (`hyperTmp` im MQTT)
3. Lohnt sich die Komplexität oder reicht ein fester Offset?

## Betroffene Dateien

| Datei | Relevanz |
|-------|----------|
| `device.py` | Neues Property oder Sensor |
| `power_strategy.py` | Setpoint-Korrektur |
| `devices/*.py` | Modellspezifische Werte |
