Hier ist ein Diagramm (als Mermaid-Code), das die Architektur und die Abhängigkeiten der bereitgestellten Dateien visualisiert.

Es zeigt, wie der `manager` die zentrale Rolle einnimmt, der `api` die Kommunikation übernimmt und die `device`-Klassen die physischen Geräte repräsentieren.

<img src="class-dependencies.svg">

```mermaid
graph TD
    %% Stile für verschiedene Dateitypen
    classDef coordinator fill:#ffcccc,stroke:#333,stroke-width:2px;
    classDef deviceCore fill:#cce5ff,stroke:#333,stroke-width:2px;
    classDef logic fill:#ccffcc,stroke:#333,stroke-width:2px;
    classDef communication fill:#e5ccff,stroke:#333,stroke-width:2px;
    classDef entity fill:#f0f0f0,stroke:#999,stroke-width:1px,stroke-dasharray: 5 5;

    %% Knoten (Dateien)
    Manager["manager.py (Koordinator)"]:::coordinator
    API["api.py (MQTT/Cloud Manager)"]:::communication
    MQTT_Prot["mqtt_protocol.py (Handler)"]:::communication
    
    Device["device.py (Basis-Klasse)"]:::deviceCore
    SDK["zendure_sdk.py (HTTP Geräte)"]:::deviceCore
    Battery["battery.py (Datenmodell)"]:::deviceCore
    
    Strategy["power_strategy.py (Verteil-Logik)"]:::logic
    FuseGroup["fusegroup.py (Gruppen-Logik)"]:::logic
    PowerPort["power_port.py (Abstraktion)"]:::logic
    
    BinSensor["binary_sensor.py (HA Entität)"]:::entity
    Entities["andere Entitäten<br/>(sensor, number, select...)"]:::entity

    %% Beziehungen (Abhängigkeiten)

    %% Manager Ebene
    Manager -- "instanziiert" --> API
    Manager -- "verwaltet Liste von" --> Device
    Manager -- "delegiert Logik an" --> Strategy
    Manager -- "verwaltet Gruppen" --> FuseGroup
    Manager -- "nutzt für P1/Grid" --> PowerPort

    %% API Ebene
    API -- "importiert Callbacks" --> MQTT_Prot
    API -- "Factory für Geräte" --> Device
    API -- "stellt MQTT bereit" --> Device

    %% Kommunikation
    MQTT_Prot -- "leitet Payload weiter an" --> Device
    Device -- "sendet über" --> MQTT_Prot
    
    %% Geräte Ebene
    SDK -- "erbt von" --> Device
    Device -- "instanziiert" --> Battery
    Device -- "nutzt (Solar/Offgrid)" --> PowerPort
    Device -- "gehört zu" --> FuseGroup
    
    %% Entitäten
    Device -- "erstellt" --> BinSensor
    Device -- "erstellt" --> Entities

    %% Strategie Ebene
    Strategy -- "berechnet Leistung für" --> Device
    Strategy -- "liest Werte von" --> PowerPort
```

### Erklärung der Schichten

Das Diagramm ist in vier logische Bereiche unterteilt:

1.  **Koordinator (Rot):**
    *   **`manager.py`**: Das Herzstück. Es erbt vom Home Assistant `DataUpdateCoordinator`. Es lädt beim Start die Geräte, überwacht den P1-Stromzähler und entscheidet, wann geladen oder entladen werden soll.

2.  **Kommunikation (Lila):**
    *   **`api.py`**: Hält die MQTT-Verbindungen (Cloud und Local). Es verwaltet das Gerät-Dictionary und leitet eingehende MQTT-Nachrichten weiter.
    *   **`mqtt_protocol.py`**: Enthält die reine Logik zum Parsen von JSON-Nachrichten und das Routing an die richtigen Geräte-Methoden (z.B. `on_msg_cloud`).

3.  **Geräte-Kern (Blau):**
    *   **`device.py`**: Die Hauptklasse `ZendureDevice`. Sie verwaltet den Zustand (State), Entitäten (Sensoren) und die Verbindungsinformationen.
    *   **`zendure_sdk.py`**: Spezialisierte Klasse für Geräte, die das lokale "ZenSDK" (HTTP) unterstützen. Erbt von `device.py`.
    *   **`battery.py`**: Hilfsklasse zur Darstellung von Batterie-Packs, die an ein Gerät angeschlossen sind.

4.  **Logik & Strategie (Grün):**
    *   **`power_strategy.py`**: Enthält die komplexe Mathematik, um决定 (zu entscheiden), wie viel Leistung auf welche Geräte verteilt wird (basierend auf SOC, Limits und PV-Leistung). Wird vom Manager aufgerufen.
    *   **`fusegroup.py`**: Verwaltung von Gerätegruppen, um sicherzustellen, dass die Summe der Leistung einer Gruppe nicht bestimmte Grenzen überschreitet.
    *   **`power_port.py`**: Abstraktionsschicht. Sie definiert, was ein "Eingang" oder "Ausgang" ist (z.B. Grid-Port, Solar-Port). Dies hilft dem Manager und der Strategie, Geräte einheitlich zu behandeln.

### Wichtige Abhängigkeiten

*   **Kreisbezug API <-> Device:** Der `api` erstellt die Geräte (`Device`), aber das `Device` benötigt den `api` wiederum, um MQTT-Nachrichten zu senden.
*   **Trennung von Logic und State:** Der `Manager` weiß *was* zu tun ist (z.B. "alle Geräte entladen"), die `power_strategy` weiß *wie* viel, und das `device` weiß *wie* der Befehl technisch gesendet wird (via `mqtt_protocol`).
*   **Entitäten:** Dateien wie `binary_sensor.py` (und andere nicht gezeigte wie `sensor.py`, `number.py`) hängen stark von `device.py` ab, da sie dort instanziiert werden.