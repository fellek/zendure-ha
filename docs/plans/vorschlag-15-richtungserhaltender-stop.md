# Vorschlag 15: Richtungserhaltender Stop (stop_in_place) + Stop-Trigger-Gate

> **Hinweis**: Dieser Vorschlag ist zum späteren Umsetzen gedacht. Vor Ausführung wird der Nutzer eine andere Änderung testen. Datei kann nach Plan-Mode-Ende nach `docs/plans/vorschlag-15-richtungserhaltender-stop.md` übernommen werden.

## Context

Im aktuellen Fork setzen `ZendureSDK.charge(0)` und `ZendureSDK.discharge(0)` beim Zendure-Gerät ein hartes `acMode` (1 bzw. 2) in der MQTT-Payload:

```python
# zendure_sdk.py:77–86
async def charge(self, power, _off=False):
    await self.doCommand({"properties": {"smartMode": 0 if power==0 else 1,
                                          "acMode": 1,
                                          "outputLimit": 0,
                                          "inputLimit": -power}})

async def discharge(self, power):
    await self.doCommand({"properties": {"smartMode": 0 if power==0 else 1,
                                          "acMode": 2,
                                          "outputLimit": power,
                                          "inputLimit": 0}})
```

`apply_assignment` in `power_strategy.py:136–163` leitet heute **jeden** STOP (sowohl `STOP_CHARGE` als auch `STOP_DISCHARGE`) auf `power_discharge(0 oder POWER_IDLE_OFFSET)` um. Damit trägt jeder Stop implizit `acMode=2` — auf ein gerade **ladendes** Gerät wirkt das als Betriebsart-Flip `acMode: 1 → 2`, den die Firmware durchführt, auch wenn Limits 0 sind.

**Beobachteter Effekt** (Log 2026-04-14, 16:04:19–27):
```
16:04:19.548  Stopping charge SolarFlow 2400 AC with 0
16:04:19.548  Power discharge ... => no action [power 0]     # vom Toleranzfilter gedeckelt
16:04:22      MQTT report: packState=2, smartMode=1
16:04:24.545  Discharge: wake idle => power_discharge(10)    # gefiltert (alt: TOLERANCE=10)
16:04:27      MQTT report: gridInputPower=84 W, packState=1  # Gerät lädt plötzlich aus Netz
16:04:27.466  PowerFlow: IDLE → CHARGE
```

Die Kombination „Stop in falsche acMode-Richtung senden" + „Toleranz-Filter lässt Stop nicht durch" + „Firmware übernimmt Property-Ziel" erzeugt eine reproduzierbare Oszillation. Die bestehende Quirk-Dokumentation (`memory:quirk_sf2400_stop_charge`) beschreibt das Symptom, nicht die Wurzel: **jeder Stop, der `acMode` neu setzt, ist potenziell schädlich**, unabhängig davon, ob über `power_charge(0)` oder `power_discharge(0)`.

Ziel: Stops so absetzen, dass `acMode` **unverändert** bleibt und nur dann überhaupt abgesetzt werden, wenn real Leistung zu stoppen ist.

## Vorschlag

### A. Wann soll ein Stop gesendet werden?

`apply_assignment` erhält ein zusätzliches Gate vor dem STOP-Pfad:

```python
if cmd in (Command.STOP_CHARGE, Command.STOP_DISCHARGE):
    # Nichts zu stoppen, wenn Leistung bereits abgeklungen ist
    if abs(d.connectorPort.power) <= SmartMode.POWER_IDLE_OFFSET \
       and abs(d.batteryPort.power) <= SmartMode.POWER_IDLE_OFFSET:
        return 0
    ...
```

Begründung:
- Der Toleranzfilter in `power_discharge`/`power_charge` würde den Befehl ohnehin zu `no action` degradieren — das explizite Gate erspart Log-Spam und verhindert, dass ein Firmware-Property-Write stattfindet, falls der Stop-Pfad irgendwann partial-properties nutzt (siehe B).
- Das Gate ist orthogonal zum `power_flow_state == IDLE`-Guard auf Zeile 151: dieser prüft **klassifizierte** Richtung, das neue Gate prüft **reale** Leistung.

### B. Wie richtungserhaltend stoppen?

Neue SDK-Methode in `zendure_sdk.py`:

```python
async def stop_in_place(self) -> int:
    """Stop all power flow without flipping acMode.

    Sends only smartMode/limits. The firmware keeps the current acMode,
    which avoids the SF 2400 AC oscillation caused by acMode: 1 → 2 flips
    on an actively charging device.
    """
    _LOGGER.info("Stop in place %s", self.name)
    await self.doCommand({"properties": {"smartMode": 0, "outputLimit": 0, "inputLimit": 0}})
    return 0
```

`doCommand` reicht das Dict unverändert weiter (HTTP-POST oder MQTT-Publish). Zendure-Properties werden von der Firmware als Delta-Update interpretiert — nicht übergebene Felder bleiben.

Änderung in `apply_assignment` (`power_strategy.py:150–163`):

```python
if cmd in (Command.STOP_CHARGE, Command.STOP_DISCHARGE):
    if abs(d.connectorPort.power) <= SmartMode.POWER_IDLE_OFFSET \
       and abs(d.batteryPort.power) <= SmartMode.POWER_IDLE_OFFSET:
        return 0

    offgrid_consumption = d.offgridPort.power_consumption if d.offgridPort else 0
    if offgrid_consumption > 0:
        # Offgrid-Last darf nicht aus dem Netz gedeckt werden → aktiv Discharge halten
        _LOGGER.info("Stop %s (offgrid hold) with %s", d.name, SmartMode.POWER_IDLE_OFFSET)
        return await d.power_discharge(SmartMode.POWER_IDLE_OFFSET)
    return await d.stop_in_place()
```

Die bisherige STOP_CHARGE/STOP_DISCHARGE-Unterscheidung entfällt strukturell — in beiden Fällen bleibt `acMode` stehen. Der Command-Enum-Unterschied bleibt erhalten für Tests, Logging und potentielle zukünftige Sonderbehandlung.

## Abwägung

### Positiv
- Keine `acMode`-Flips mehr beim Stoppen → eliminiert den beobachteten 84 W Grid-Input-Spike und den dadurch ausgelösten Mode-Flip IDLE→CHARGE.
- Der SF-2400-Quirk („stop-charge muss über power_discharge laufen") wird **strukturell obsolet**: egal welche Richtung das Gerät fährt, `stop_in_place()` stört sie nicht.
- Explizites Stop-Gate (A) reduziert unnötige MQTT-Writes und Log-Einträge.
- Kompatibel mit allen Device-Subklassen (`hub1200`, `hub2000`, `ace1500`, `hyper2000`, `aio2400`, `superbasev4600/6400`), weil `stop_in_place` auf SDK-Ebene definiert ist und nicht das Unterklassen-spezifische `charge()`/`discharge()` verwendet.

### Risiken / Offene Fragen
1. **Firmware-Verhalten bei partiellem Property-Write.** Nicht garantiert, dass Zendure-Firmware bei fehlendem `acMode` den aktuellen Modus beibehält. Muss vor Rollout live verifiziert werden (einmaliger Test via MQTT-Explorer). Falls Firmware einen Default setzt, **kein Regression** gegenüber heute, aber kein Gewinn.
   - Fallback: `stop_in_place()` liest den letzten bekannten `acMode` (aus `self.acMode.asInt`, falls als Sensor existiert) und schreibt ihn explizit zurück.
2. **Gerät bleibt im letzten `acMode`.** Wenn das Gerät zuletzt geladen hat und jetzt `stop_in_place` erhält, bleibt `acMode=1`. Beim nächsten Discharge-Befehl über `discharge(power)` wird `acMode=2` gesetzt → ein einziger sauberer Flip, statt zwei in kurzer Folge. Vorteil.
3. **Offgrid-Hold-Pfad** (inneres `if offgrid_consumption > 0`) setzt weiterhin `acMode=2`. Bei einem ladenden Gerät mit offgrid-Last ist das eine echte Betriebsart-Änderung und akzeptabel, weil Offgrid aus Akku gespeist werden soll — nicht aus Netz.
4. **Toter Code:** Die kommentierten Stellen in `power_strategy.py:108–114` und `memory:quirk_sf2400_stop_charge` sollten nach Rollout angepasst/gelöscht werden. Der Quirk ist dann Geschichte.
5. **Reihenfolge:** Wenn `POWER_TOLERANCE = POWER_IDLE_OFFSET − 2` (bereits gesetzt) aktiv ist, werden manche Stops die bisherigen Filter nicht mehr blockieren und somit wirklich durchlaufen — sowohl nützlich (Wake-Pfad funktioniert) als auch ein Grund, diesen Vorschlag zeitnah zu realisieren, damit keine erhöhte Rate von `acMode`-Flips live geht.

## Touched Files

- `custom_components/zendure_ha/zendure_sdk.py` — neue Methode `stop_in_place()`
- `custom_components/zendure_ha/power_strategy.py:150–163` — Stop-Gate + neuer Stop-Call; alte SF-2400-Quirk-Doc-Strings anpassen
- `tests/test_power_strategy_regressions.py` — Tests:
  - `test_stop_gate_skips_when_quiet`: `connector_power=5, battery_power=3` → STOP führt zu keinem Device-Call.
  - `test_stop_in_place_preserves_acmode`: Mock-Gerät, STOP_CHARGE → erwarte `doCommand` ohne `acMode`-Key.
  - `test_stop_offgrid_keeps_discharge_hold`: `offgrid_consumption=50` → `power_discharge(POWER_IDLE_OFFSET)`.
- Ggf. `memory:quirk_sf2400_stop_charge.md` und `memory:bug_bypass_oscillation.md` aktualisieren nach Live-Verifikation.

## Verifikation

1. **Unit-Tests** wie oben.
2. **Firmware-Live-Test** vor Merge: MQTT-Payload `{"properties":{"smartMode":0,"outputLimit":0,"inputLimit":0}}` an das SF 2400 AC senden, während es lädt. Erwartung: nächster Properties-Report zeigt `acMode` unverändert und `outputPackPower / gridInputPower ≤ 10 W`. Wenn nicht — Fallback-Variante implementieren (`acMode` explizit zurückschreiben).
3. **24-h-Beobachtung**: `grep "PowerFlow .* → .*"` auf dem Home-Assistant-Log. Erwartung: die Cluster um 16:04, 09:34 usw. verschwinden.
4. **Regression auf Offgrid-Setups**: Gerät mit aktiver Offgrid-Last → nach STOP_DISCHARGE muss `power_discharge(10)` sichtbar sein, Offgrid-Verbrauch darf nicht aus Grid gedeckt werden.

## Nachfolge (nicht Teil dieses Vorschlags)

Separat zu adressierende Folge-Themen aus der Oszillations-Analyse:

- **Symmetrische Battery-Direction-Heuristik** in `power_port.py:105–112` (`is_charging` Schwelle auf `POWER_IDLE_OFFSET` anheben).
- **Dwell-Time in `update_power_flow_state`** (`device.py:335–366`) gegen Sub-Sekunden-Flips.

Diese bleiben als eigene Vorschläge offen, weil sie unabhängig von der Stop-Semantik wirken.
