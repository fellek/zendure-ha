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
        f"  Befehle/Min:           {f'{before.cmd_per_minute:.2f}' if before.cmd_per_minute else 'n/a'} → {f'{after.cmd_per_minute:.2f}' if after.cmd_per_minute else 'n/a'}",
        f"  Richtungswechsel:      {before.direction_flips} → {after.direction_flips}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# cProfile .prof comparison
# ---------------------------------------------------------------------------

def _load_prof(path: str, filter_str: str = "zendure_ha") -> dict[str, tuple[int, float, float]]:
    """Lade .prof-Datei, gib {funcname: (ncalls, tottime, cumtime)} zurück.

    Nur Funktionen deren Quelldatei `filter_str` enthält werden behalten.
    Bei Namenskollisionen (gleicher Funktionsname in verschiedenen Dateien)
    wird der vollständige Schlüssel `datei::func` verwendet.
    """
    import io
    import pstats

    s = pstats.Stats(path, stream=io.StringIO())
    result: dict[str, tuple[int, float, float]] = {}
    for (filename, _lineno, funcname), (_pcalls, ncalls, tt, ct, _callers) in s.stats.items():
        if filter_str not in filename:
            continue
        key = funcname
        if key in result:
            short = filename.split("/")[-1].replace(".py", "")
            key = f"{short}::{funcname}"
        result[key] = (ncalls, tt, ct)
    return result


def compare_prof(path_before: str, path_after: str) -> None:
    """Vergleiche zwei cProfile-.prof-Dateien und gib tabellarischen Diff aus."""
    import io
    import pstats

    def total_time(path: str) -> float:
        s = pstats.Stats(path, stream=io.StringIO())
        return sum(tt for (_f, _l, _n), (_p, _nc, tt, _ct, _ca) in s.stats.items())

    before = _load_prof(path_before)
    after = _load_prof(path_after)
    tt_before = total_time(path_before)
    tt_after = total_time(path_after)

    delta_total = (tt_after - tt_before) / tt_before * 100 if tt_before else float("nan")
    print("=== cProfile-Vergleich ===")
    print(f"Gesamtlaufzeit:  before={tt_before:.2f}s  after={tt_after:.2f}s  Δ={delta_total:+.1f}%")
    print()

    common = set(before) & set(after)
    only_before = set(before) - set(after)
    only_after = set(after) - set(before)

    # Zeilen für gemeinsame Funktionen
    rows: list[tuple[float, str, int, float, float, int, float, float]] = []
    for name in common:
        nc_b, tt_b, ct_b = before[name]
        nc_a, tt_a, ct_a = after[name]
        delta_ct = (ct_a - ct_b) / ct_b * 100 if ct_b else float("nan")
        ppc_b = ct_b / nc_b * 1000 if nc_b else 0.0
        ppc_a = ct_a / nc_a * 1000 if nc_a else 0.0
        rows.append((abs(ct_a - ct_b), name, nc_b, ct_b, ct_a, nc_a, ppc_b, ppc_a))

    rows.sort(reverse=True)

    hdr = f"  {'Funktion':<40} {'cum_b':>7} {'cum_a':>7} {'Δ%':>7}  {'calls_b':>7} {'calls_a':>7}  {'ms/call_b':>9} {'ms/call_a':>9}"
    print("Gemeinsame Funktionen (sortiert nach Δ cumtime):")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for _abs_d, name, nc_b, ct_b, ct_a, nc_a, ppc_b, ppc_a in rows:
        delta_ct = (ct_a - ct_b) / ct_b * 100 if ct_b else float("nan")
        print(f"  {name:<40} {ct_b:>7.3f} {ct_a:>7.3f} {delta_ct:>+7.1f}%  {nc_b:>7} {nc_a:>7}  {ppc_b:>9.2f} {ppc_a:>9.2f}")

    if only_before:
        print()
        print("Nur in 'before' (entfernte / umbenannte Funktionen):")
        for name in sorted(only_before):
            nc, tt, ct = before[name]
            print(f"  {name:<40} {ct:>7.3f}s  {nc} Aufrufe  {ct/nc*1000:.2f}ms/call")

    if only_after:
        print()
        print("Nur in 'after' (neue / umbenannte Funktionen):")
        for name in sorted(only_after):
            nc, tt, ct = after[name]
            print(f"  {name:<40} {ct:>7.3f}s  {nc} Aufrufe  {ct/nc*1000:.2f}ms/call")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    # --prof before.prof after.prof
    if sys.argv[1] == "--prof":
        if len(sys.argv) != 4:
            print("Verwendung: analyze_perf_log.py --prof before.prof after.prof")
            sys.exit(1)
        compare_prof(sys.argv[2], sys.argv[3])
        return

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
