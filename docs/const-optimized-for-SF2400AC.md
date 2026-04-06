Das Kernproblem, das du in den Logs von 19:23 bis 19:25 beobachtest, ist der **Konflikt zwischen einer hochdynamischen Software (Reaktionszeit ~60ms) und einer trägen Hardware (Slew-Rate / Anstiegsgeschwindigkeit des Wechselrichters = 5 bis 10 Sekunden für 1600W).** 

Deine aktuelle Konfiguration ist so aggressiv, dass die Software dem Wechselrichter permanent "hinterherrennt" und auf transiente Zustände reagiert, die der Wechselrichter selbst verursacht, weil er noch mit dem *letzten* Kommando beschäftigt ist.

Hier sind die beteiligten Konstanten aufgeteilt in drei Kategorien, inklusive meiner Empfehlungen für eine optimale Abstimmung:

---

### Kategorie 1: Geschwindigkeit der Regelung (Das "Wann")
Diese Werte bestimmen, wie oft die Software überhaupt nachrechnet. Wenn sie zu schnell sind, rechnen sie P1-Werte aus, die durch die Hardware-Trägheit noch gar nicht stimmen können.

| Konstante | Dein Wert | Empfehlung | Begründung |
| :--- | :--- | :--- | :--- |
| **`TIMEZERO`** | 1 | **4** | 1 Sekunde ist physikalisch sinnlos, da der Wechselrichter 2 Sekunden braucht, um überhaupt ein Kommando umzusetzen. 4 Sekunden geben dem System Zeit, die Auswirkung des letzten Befehls am P1-Meter zu sehen. |
| **`TIMEFAST`** | 0.7 | **1.5** | 0.7 Sekunden provoziert einen Telegramm-Stau beim Wechselrichter. 1,5 Sekunden ist schnell genug für Notfälle, aber langsam genug, um dem Wechselrichter Luft zum Atmen zu geben. |
| **`P1_STDDEV_MIN`** | 3 | **15** | **Das ist der Hauptverursacher für das "Geister-Fahren".** Wenn der Wechselrichter von 1800W auf 0W fährt, erzeugt er am P1-Meter massive Schwankungen. Ein Wert von 3W interpretiert diese Schwankungen als "schnelle Netzänderung" und löst Panik-Berechnungen aus. 15W ignoriert das Rauschen des Wechselrichters. |

---

### Kategorie 2: Dämpfung und Verzögerung (Das "Ob")
Diese Werte verhindern, dass die Software bei erkannten Änderungen sofort das Gegenteil von dem macht, was die Hardware gerade tut (Flip-Flopping).

| Konstante | Dein Wert | Empfehlung | Begründung |
| :--- | :--- | :--- | :--- |
| **`POWER_TOLERANCE`** | 5 | **10** | Wenn der Wechselrichter 1800W soll, aber gerade erst bei 1790W ist, sollte das System nicht nochmal 1800W senden. 10W Toleranz reduziert MQTT-Traffic, während der Wechselrichter "einschwingt". |
| **`HYSTERESIS_FAST_COOLDOWN`** | 2 | **5** | 2 Sekunden sind zu kurz (siehe Log 19:24:59 - 19:25:05). Der Wechselrichter hat in 2 Sekunden noch nicht auf 0W reduziert. 5 Sekunden garantieren, dass die Hardware wirklich stillsteht, bevor das System den Modus wechselt. |
| **`HYSTERESIS_SLOW_COOLDOWN`**| 60 | **20** | 60 Sekunden frieren den Akku zu lange ein, wenn ein echter Lastwechsel stattfand. 20 Sekunden reichen aus, um auszuschließen, dass der P1-Meter nur wegen des Wechselrichter-Laufs verrücktspielt. |
| **`HYSTERESIS_LONG_COOLDOWN`** | 300 | **300** | (Belassen) 5 Minuten sind als "Gedächtnis" gut, um zu verhindern, dass ein Akku, der gerade gestoppt wurde, sofort wieder angeht, wenn der P1 leicht schwankt. |

---

### Kategorie 3: Startschwelle und Gewichtung (Das "Wie")
Diese Werte sind weniger für das Trägheits-Problem verantwortlich, beeinflussen aber, wie sanft das System hochfährt.

| Konstante | Dein Wert | Empfehlung | Begründung |
| :--- | :--- | :--- | :--- |
| **`POWER_START`** | 50 | **50** | (Belassen) 50W ist ein guter Schwellenwert, um den Wechselrichter aus dem Standby zu holen. |
| **`HYSTERESIS_START_FACTOR`** | 1.5 | **2.0** | Wenn du ihn auf 2.0 stellst, verlangt das System, dass ein Gerät mindestens 200% (statt 150%) seiner Startleistung bekommt, bevor es dauerhaft läuft. Das verhindert, dass Geräte bei knappen Leistungen stottern. |
| **`WAKEUP_CAPACITY_FACTOR`** | 2 | **2** | (Belassen) Schützt davor, unnötig viele Geräte aufzuwecken. |

---

### Zusammenfassung: Die optimale `const.py`

Wenn du diese Werte übernimmst, ändert sich das Verhalten deines Systems radikal zum Positiven:

```python
class SmartMode:
    # ... (andere Werte) ...
    
    TIMEFAST = 1.5       # Schnell, aber dem Wechselrichter Zeit gebend
    TIMEZERO = 4         # Standard-Zyklus: Warte ab, bis Hardware reagiert hat
    
    P1_STDDEV_FACTOR = 3.5
    P1_STDDEV_MIN = 15   # Ignoriert das "Rauschen" des träge hochfahrenden Wechselrichters
    
    POWER_TOLERANCE = 10 # Verhindert MQTT-Spam beim Einschwingen
    
    HYSTERESIS_FAST_COOLDOWN = 5   # Warte 5s nach Moduswechsel (Hardware braucht das)
    HYSTERESIS_SLOW_COOLDOWN = 20  # Warte 20s bei unklaren Zuständen
    HYSTERESIS_LONG_COOLDOWN = 300 # Bleib bei 5 Min Gedächtnis
    
    HYSTERESIS_START_FACTOR = 2.0 # Stabilere Laufzeitvergabe
```

**Was wird sich dadurch in der Praxis ändern?**
1.  **Kein "Geister-Fahren" mehr:** Wenn der Wechselrichter bei 19:23:03 von 0 auf 1800W hochfährt, wird die Software nicht alle 1 Sekunde neu berechnen und dem Wechselrichter hinterherrennen. Sie berechnet "Gib 1800W" und wartet dann 4 Sekunden, bis sich das Netz beruhigt hat.
2.  **Kein Flip-Flopping mehr:** Wenn der Wechselrichter bei 19:24:54 von 1800W auf 0W heruntergefahren wird, bemerkt das System den P1-Einbruch. Anstatt sofort wild zwischen "Laden" und "Entladen" hin und her zu springen (wie in deinem Log), schaltet es auf "Laden", aktiviert die 5-Sekunden-Sperre und beobachtet ruhig, ob sich der P1 stabilisiert.
3.  **Weiches Einschwingen:** Wenn der Wechselrichter 1790W liefert, aber 1800W gefordert sind, sendet die Software dank `POWER_TOLERANCE = 10` kein neues Kommando. Sie vertraut darauf, dass der Wechselrichter in den nächsten Sekunden von selbst auf 1800W kommt.