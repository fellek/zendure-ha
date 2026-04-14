# Vorschlag 14: STOP_DISCHARGE — veralteten Kommentar und Tests nachziehen

## Priorität: Niedrig (Dokumentations-/Testkorrektur, kein Produktionsbug)

## Vorfall

Bei der Umsetzung von Vorschlag 02 (IDLE-Skip in `apply_assignment`) stellte sich
heraus, dass zwei Regression-Tests bereits vor dem Vorschlag-02-Change rot waren:

- `tests/test_power_strategy_regressions.py::test_stop_discharge_without_offgrid_uses_power_charge_zero`
- `tests/test_power_strategy_regressions.py::test_stop_discharge_with_offgrid_uses_power_charge_negative_idle_offset`

Die Tests erwarten für `STOP_DISCHARGE` einen Aufruf von `power_charge(0)` bzw.
`power_charge(-POWER_IDLE_OFFSET)`. Produziert wird jedoch `power_discharge(0)`
bzw. `power_discharge(+POWER_IDLE_OFFSET)`.

## Wurzelursache

Der **Produktionscode ist korrekt** — die Änderung war beabsichtigt. Commit
`5b62926 "Add Todos in Comments"` (Autor: fellek, 2026-04-12) hat im Rahmen der
Behebung von **Bug #9 "BYPASS-Oszillation"** den `STOP_DISCHARGE`-Zweig umgestellt:

```diff
   # STOP_DISCHARGE: symmetric — stop a discharging device via power_charge.
-  stop_pwr = -SmartMode.POWER_IDLE_OFFSET if offgrid_consumption > 0 else 0
-  return await d.power_charge(stop_pwr)
+  stop_pwr = SmartMode.POWER_IDLE_OFFSET if offgrid_consumption > 0 else 0
+  _LOGGER.info("Stopping charge %s with %s", d.name, stop_pwr)
+  return await d.power_discharge(stop_pwr)
```

Begründung (Memory `bug_list.md` #9): Ein `power_charge(0)` an ein entladendes
Gerät triggerte bei der SF 2400 AC eine CHARGE↔DISCHARGE-Oszillation (alle
2–15 s, dokumentiert im Log vom 2026-04-11 15:00–17:15). Die Symmetrie
`STOP_CHARGE → power_discharge(0 oder 10)` / `STOP_DISCHARGE → power_charge(0
oder −10)` war mathematisch sauber, führte aber in der Praxis zur Oszillation.

Nach dem Fix benutzen **beide** Stop-Richtungen `power_discharge(stop_pwr)` mit
positivem Offset. Das bricht die Aufruf-Symmetrie bewusst, hält das Gerät aber
stabil im Leerlauf.

## Stale-Zustand

Nach Bug-#9-Fix wurden zwei Stellen **nicht nachgezogen**:

1. **Kommentar** in `power_strategy.py:160`:
   ```python
   # STOP_DISCHARGE: symmetric — stop a discharging device via power_charge.
   ```
   widerspricht dem Code darunter und erzeugt bei neuen Contributors die falsche
   Erwartung, der Code sei defekt.

2. **Tests** `test_stop_discharge_without_offgrid_uses_power_charge_zero` und
   `test_stop_discharge_with_offgrid_uses_power_charge_negative_idle_offset`:
   Kodieren die **alte** oszillierende Logik als "korrektes" Verhalten. Sie sind
   seit Commit `5b62926` rot, wurden aber nie als Regression-Fence für Bug #9
   umgeschrieben.

## Lösung

### A) Kommentar korrigieren

`power_strategy.py:160`:

```python
# STOP_DISCHARGE: also via power_discharge with positive offset.
# NOT symmetric to STOP_CHARGE — a power_charge(0) on a discharging SF 2400
# triggers a CHARGE↔DISCHARGE oscillation (Bug #9, fixed in 5b62926).
```

### B) Tests als Bug-#9-Regression-Fence umschreiben

```python
@pytest.mark.asyncio
async def test_stop_discharge_uses_power_discharge_not_power_charge() -> None:
    """Bug #9 regression fence: STOP_DISCHARGE must NOT emit power_charge().
    A power_charge(0 or -10) on a discharging SF 2400 triggered a
    CHARGE↔DISCHARGE oscillation every 2–15 s (see memory bug_list.md#9
    and commit 5b62926). Both stop directions now route through
    power_discharge with a positive offset.
    """
    d = _RecordingDevice(offgrid_consumption=0, power_flow_state=PowerFlowState.DISCHARGE)
    await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    assert d.calls == [("discharge", 0)]
    for verb, _ in d.calls:
        assert verb != "charge", "STOP_DISCHARGE must never emit power_charge — Bug #9"


@pytest.mark.asyncio
async def test_stop_discharge_with_offgrid_uses_positive_idle_offset() -> None:
    d = _RecordingDevice(offgrid_consumption=50, power_flow_state=PowerFlowState.DISCHARGE)
    await apply_assignment(d, DeviceAssignment(Command.STOP_DISCHARGE))
    assert d.calls == [("discharge", SmartMode.POWER_IDLE_OFFSET)]
```

### C) Klassenkommentar von `Command` anpassen

`power_strategy.py` (Klasse `Command`): Der Doc-Kommentar
beschreibt noch die Symmetrie (`"the symmetric case applies to stopping a
discharging device"`) und sollte auf "both stop directions route through
power_discharge after Bug #9" aktualisiert werden.

## Betroffene Dateien

| Datei | Stelle |
|---|---|
| `custom_components/zendure_ha/power_strategy.py` | Zeile 160 (inline comment), Klasse `Command` Docstring |
| `tests/test_power_strategy_regressions.py` | zwei `test_stop_discharge_*`-Tests umschreiben |

## Risiko

Keins — rein dokumentatorisch / Test-Umformulierung. Produktionsverhalten
unverändert.

## Nebennutzen

Die umgeschriebenen Tests dienen als Regression-Fence für Bug #9: sollte
jemand in Zukunft versuchen, die "saubere" Symmetrie wiederherzustellen,
schlagen die Tests sofort an.
