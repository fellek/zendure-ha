# Refactoring-Pläne: PowerFlowStrategy

Gesammelte Pläne, Bearbeitungsschritte und Verbesserungsvorschläge für das Refactoring
der Power-Strategy-Logik in `custom_components/zendure_ha/power_strategy.py` und
angrenzenden Dateien.

## Struktur

### Pläne (eigenständige, abgeschlossene Vorhaben)

| Datei | Titel | Status |
|-------|-------|--------|
| [plan-01-bypass-refactor.md](plan-01-bypass-refactor.md) | BYPASS aus PowerFlowState entkoppeln | Offen |
| [plan-02-bypass-keepalive.md](plan-02-bypass-keepalive.md) | Bypass-Keepalive & Wakeup-Routine | Offen (Recherche nötig) |
| [plan-03-manual-mode-sign.md](plan-03-manual-mode-sign.md) | MANUAL-Modus Vorzeichen-Problem (Bug #7) | Offen |

### Bearbeitungsschritte (aufeinander aufbauend: Device-State-Ownership)

| Datei | Titel | Abhängigkeit |
|-------|-------|--------------|
| [step-01-manager-state-enum.md](step-01-manager-state-enum.md) | ManagerState erweitern | - |
| [step-02-offgrid-port-negative.md](step-02-offgrid-port-negative.md) | OffGridPowerPort negative Werte zulassen | - |
| [step-03-offgrid-device-property.md](step-03-offgrid-device-property.md) | offgrid_power als Device-Property | Step 2 |
| [step-04-device-operational-state.md](step-04-device-operational-state.md) | update_operational_state() im Device | Step 1, 2, 3 |
| [step-05-consolidate-idle-list.md](step-05-consolidate-idle-list.md) | socempty/woken_socempty entfernen | Step 4 |
| [step-06-classify-on-opstate.md](step-06-classify-on-opstate.md) | _classify_single_device auf op_state | Step 4, 5 |
| [step-07-offgrid-bilanz.md](step-07-offgrid-bilanz.md) | pwr_produced + Offgrid-Einspeisung bilanzieren | Step 2, 3, 6 |

### Verbesserungsvorschläge (unabhängig umsetzbar)

| Datei | Titel | Priorität |
|-------|-------|-----------|
| [vorschlag-01-matching-charge-early-return.md](vorschlag-01-matching-charge-early-return.md) | MATCHING_CHARGE: Early-return statt distribute_discharge(0) | Mittel |
| [vorschlag-02-stop-state-check.md](vorschlag-02-stop-state-check.md) | Stop-Kommando nur wenn Gerät nicht IDLE | **Umgesetzt** |
| [vorschlag-03-polling-minsoc.md](vorschlag-03-polling-minsoc.md) | Polling-Intervall bei minSoC-Block reduzieren | **Umgesetzt** |
| [vorschlag-04-selfconsumption-sensor.md](vorschlag-04-selfconsumption-sensor.md) | Selfconsumption virtueller Sensor | Niedrig |
| [vorschlag-05-rename-variables.md](vorschlag-05-rename-variables.md) | Variablen umbenennen (home, kWh, ConnectorPort) | Niedrig |
| [vorschlag-06-device-god-class-split.md](vorschlag-06-device-god-class-split.md) | `ZendureDevice` in MQTT/State/Ports aufteilen | **Umgesetzt** |
| [vorschlag-07-manager-port-registry.md](vorschlag-07-manager-port-registry.md) | `DevicePortRegistry` — Manager ↔ Device Port-Init entkoppeln | Hoch |
| [vorschlag-08-distribute-power-context.md](vorschlag-08-distribute-power-context.md) | `_distribute_power()` Context-Dataclass + Clock-Abstraktion | Mittel |
| [vorschlag-09-inverter-loss-per-model.md](vorschlag-09-inverter-loss-per-model.md) | `InverterLossPowerPort` — Modellwerte pro Gerät konfigurierbar | Mittel |
| [vorschlag-10-power-charge-discharge-symmetry.md](vorschlag-10-power-charge-discharge-symmetry.md) | `power_charge` / `power_discharge` Spiegel-Duplikate zusammenführen | Mittel |
| [vorschlag-11-hysteresis-suppression-dry.md](vorschlag-11-hysteresis-suppression-dry.md) | `apply_device_suppression()` Charge/Discharge-Zweige vereinheitlichen | Niedrig |
| [vorschlag-12-mode-dispatch-branch-helper.md](vorschlag-12-mode-dispatch-branch-helper.md) | `_branch_by_sign()` Helper für Mode-Dispatch | Niedrig |
| [vorschlag-13-classification-power-semantik.md](vorschlag-13-classification-power-semantik.md) | `_classify_single_device()` Semantik von `connector_power` / `net_battery` klären | Niedrig |
| [vorschlag-14-stop-discharge-test-alignment.md](vorschlag-14-stop-discharge-test-alignment.md) | STOP_DISCHARGE — Kommentar & Tests an Bug-#9-Fix angleichen | Niedrig |
