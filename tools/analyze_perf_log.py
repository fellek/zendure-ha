#!/usr/bin/env python3
"""Analyse PERF-Markierungen aus dem HA-Log.

Verwendung:
    python tools/analyze_perf_log.py home-assistant_before.log [home-assistant_after.log]

Extrahiert alle PERF-Zeilen, berechnet Metriken pro Zyklus und gibt eine
vergleichende Zusammenfassung aus.

PERF-Format im Log:
    <timestamp> DEBUG ... PERF <TAG> t=<monotonic> [key=value ...]

Tags:
    P1_IN          P1-Event empfangen (manager.py)
    DISPATCH_START classify_and_dispatch aufgerufen (power_strategy.py)
    ASSESS_DONE    _assess() abgeschlossen (power_strategy.py)
    CMD_ASSIGN     Befehl pro Gerät zugewiesen (power_strategy.py)
    CMD_SENT       Befehl abgeschickt via HTTP oder MQTT
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median, quantiles

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_PERF_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[\d.]*)"
    r".*PERF (?P<tag>\w+) t=(?P<mono>[\d.]+)"
    r"(?P<rest>.*)"
)
_KV_RE = re.compile(r"(\w+)=([\S]+)")


@dataclass
class PerfEvent:
    wall_ts: str
    tag: str
    mono: float
    attrs: dict[str, str] = field(default_factory=dict)


def parse_log(path: str) -> list[PerfEvent]:
    events: list[PerfEvent] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _PERF_RE.search(line)
            if not m:
                continue
            attrs = dict(_KV_RE.findall(m.group("rest")))
            events.append(PerfEvent(
                wall_ts=m.group("ts"),
                tag=m.group("tag"),
                mono=float(m.group("mono")),
                attrs=attrs,
            ))
    return events


# ---------------------------------------------------------------------------
# Cycle assembly
# ---------------------------------------------------------------------------

@dataclass
class Cycle:
    p1_in: float | None = None
    dispatch_start: float | None = None
    assess_done: float | None = None
    cmd_assigns: list[float] = field(default_factory=list)
    cmd_sent: list[tuple[float, str]] = field(default_factory=list)  # (mono, transport)
    p1_value: int | None = None
    isFast: bool | None = None

    def dispatch_latency_ms(self) -> float | None:
        if self.p1_in and self.dispatch_start:
            return (self.dispatch_start - self.p1_in) * 1000
        return None

    def assess_duration_ms(self) -> float | None:
        if self.dispatch_start and self.assess_done:
            return (self.assess_done - self.dispatch_start) * 1000
        return None

    def cycle_latency_ms(self) -> float | None:
        if self.p1_in and self.cmd_sent:
            return (self.cmd_sent[-1][0] - self.p1_in) * 1000
        return None

    def strategy_duration_ms(self) -> float | None:
        if self.dispatch_start and self.cmd_sent:
            return (self.cmd_sent[-1][0] - self.dispatch_start) * 1000
        return None

    def has_command(self) -> bool:
        return bool(self.cmd_sent)


def assemble_cycles(events: list[PerfEvent]) -> list[Cycle]:
    cycles: list[Cycle] = []
    current: Cycle | None = None

    for ev in events:
        if ev.tag == "P1_IN":
            if current and current.p1_in is not None:
                cycles.append(current)
            current = Cycle(
                p1_in=ev.mono,
                p1_value=int(ev.attrs.get("p1", 0)),
            )
        elif ev.tag == "DISPATCH_START" and current:
            current.dispatch_start = ev.mono
            current.isFast = ev.attrs.get("isFast", "False") == "True"
        elif ev.tag == "ASSESS_DONE" and current:
            current.assess_done = ev.mono
        elif ev.tag == "CMD_ASSIGN" and current:
            current.cmd_assigns.append(ev.mono)
        elif ev.tag == "CMD_SENT" and current:
            current.cmd_sent.append((ev.mono, ev.attrs.get("transport", "?")))

    if current and current.p1_in is not None:
        cycles.append(current)

    return cycles


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def percentile(values: list[float], p: int) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return values[0]
    qs = quantiles(values, n=100)
    return qs[min(p - 1, len(qs) - 1)]


def _fmt(v: float | None, unit: str = "ms") -> str:
    if v is None or v != v:  # nan check
        return "  n/a"
    return f"{v:6.1f}{unit}"


@dataclass
class Metrics:
    label: str
    total_cycles: int = 0
    dispatched_cycles: int = 0
    cmd_cycles: int = 0

    cycle_latency: list[float] = field(default_factory=list)
    dispatch_latency: list[float] = field(default_factory=list)
    assess_duration: list[float] = field(default_factory=list)
    strategy_duration: list[float] = field(default_factory=list)

    # Regelgüte
    cmd_per_minute: float | None = None
    direction_flips: int = 0  # Oszillationsindikator

    def compute(self, cycles: list[Cycle]) -> None:
        self.total_cycles = len(cycles)
        dispatched = [c for c in cycles if c.dispatch_start is not None]
        self.dispatched_cycles = len(dispatched)
        cmd_cycles = [c for c in cycles if c.has_command()]
        self.cmd_cycles = len(cmd_cycles)

        self.cycle_latency = [v for c in dispatched if (v := c.cycle_latency_ms()) is not None]
        self.dispatch_latency = [v for c in dispatched if (v := c.dispatch_latency_ms()) is not None]
        self.assess_duration = [v for c in dispatched if (v := c.assess_duration_ms()) is not None]
        self.strategy_duration = [v for c in dispatched if (v := c.strategy_duration_ms()) is not None]

        # Kommandofrequenz
        if dispatched:
            span = dispatched[-1].p1_in - dispatched[0].p1_in  # type: ignore[operator]
            if span > 0:
                self.cmd_per_minute = self.cmd_cycles / (span / 60)

        # Oszillation: Vorzeichenwechsel der p1-Werte bei aufeinanderfolgenden CMD-Zyklen
        p1_cmd = [c.p1_value for c in cmd_cycles if c.p1_value is not None]
        for a, b in zip(p1_cmd, p1_cmd[1:]):
            if (a > 0) != (b > 0):
                self.direction_flips += 1

    def _stat_row(self, label: str, values: list[float]) -> str:
        if not values:
            return f"  {label:<28} n/a"
        return (
            f"  {label:<28}"
            f"  mean={_fmt(mean(values))}"
            f"  median={_fmt(median(values))}"
            f"  p95={_fmt(percentile(values, 95))}"
            f"  max={_fmt(max(values))}"
        )

    def report(self) -> str:
        lines = [
            f"=== {self.label} ===",
            f"  Zyklen total:          {self.total_cycles}",
            f"  Zyklen mit Dispatch:   {self.dispatched_cycles}",
            f"  Zyklen mit CMD:        {self.cmd_cycles}",
            "",
            "  --- M2: Zykluslatenz (P1_IN → CMD_SENT) ---",
            self._stat_row("Zykluslatenz", self.cycle_latency),
            self._stat_row("Dispatch-Verzögerung", self.dispatch_latency),
            self._stat_row("_assess()-Dauer", self.assess_duration),
            self._stat_row("Strategie-Dauer", self.strategy_duration),
            "",
            "  --- M4: Regelgüte ---",
            f"  Befehle/Min (M4):      {self.cmd_per_minute:.2f}" if self.cmd_per_minute is not None else "  Befehle/Min:           n/a",
            f"  Richtungswechsel:      {self.direction_flips}  (0=kein Oszillieren)",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare(before: Metrics, after: Metrics) -> str:
    def delta(b: list[float], a: list[float]) -> str:
        if not b or not a:
            return "n/a"
        d = (mean(a) - mean(b)) / mean(b) * 100
        return f"{d:+.1f}%"

    lines = [
        "=== VERGLEICH: vor → nach ===",
        f"  Zykluslatenz (mean):   {_fmt(mean(before.cycle_latency) if before.cycle_latency else None)} → {_fmt(mean(after.cycle_latency) if after.cycle_latency else None)}  Δ={delta(before.cycle_latency, after.cycle_latency)}",
        f"  Strategie-Dauer (mean):{_fmt(mean(before.strategy_duration) if before.strategy_duration else None)} → {_fmt(mean(after.strategy_duration) if after.strategy_duration else None)}  Δ={delta(before.strategy_duration, after.strategy_duration)}",
        f"  Befehle/Min:           {before.cmd_per_minute:.2f if before.cmd_per_minute else 'n/a'} → {after.cmd_per_minute:.2f if after.cmd_per_minute else 'n/a'}",
        f"  Richtungswechsel:      {before.direction_flips} → {after.direction_flips}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    files = sys.argv[1:]
    results: list[tuple[str, Metrics]] = []

    for path in files:
        label = Path(path).name
        print(f"Lese {path} ...", flush=True)
        events = parse_log(path)
        cycles = assemble_cycles(events)
        print(f"  {len(events)} PERF-Ereignisse, {len(cycles)} Zyklen gefunden")

        m = Metrics(label=label)
        m.compute(cycles)
        results.append((label, m))
        print()
        print(m.report())
        print()

    if len(results) == 2:
        print(compare(results[0][1], results[1][1]))


if __name__ == "__main__":
    main()
