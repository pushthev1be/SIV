"""Check that a simulated match looks like football, using only what the event feed carries.

    uv run python -m sim.realism golden late_comeback

Distances and speeds are derived from consecutive position snapshots, the same way the engine will.
"""

from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass

from contracts import MatchEvent

MIN_SPEED_WINDOW_S = 1.0  # shorter gaps amplify snapshot noise

# (low, high) per team unless noted. Loose on purpose: a plausible match, not a calibrated one.
RANGES = {
    "passes": (300, 700),
    "pass_completion": (0.70, 0.92),
    "shots": (4, 24),
    "fouls": (5, 20),
    "outfield_km_median": (8.0, 12.5),
    "outfield_km": (5.5, 14.0),  # every outfield player who finished the match
    "keeper_km": (1.5, 7.0),
    "top_speed_kmh": (26.0, 36.5),  # fastest player per team
    "pass_speed_kmh": (25.0, 115.0),
    "shot_speed_kmh": (50.0, 135.0),
}


@dataclass
class Summary:
    passes: dict[str, int]
    completed: dict[str, int]
    shots: dict[str, int]
    goals: dict[str, int]
    fouls: dict[str, int]
    km: dict[str, float]
    top_kmh: dict[str, float]
    finished: set[str]
    pass_speeds: list[float]
    shot_speeds: list[float]


def summarize(events: list[MatchEvent]) -> Summary:
    passes, completed, shots, goals, fouls = (defaultdict(int) for _ in range(5))
    km: dict[str, float] = defaultdict(float)
    top: dict[str, float] = defaultdict(float)
    last: dict[str, tuple[int, float, tuple[float, float]]] = {}
    anchor: dict[str, tuple[int, float, tuple[float, float]]] = {}
    pass_speeds, shot_speeds = [], []
    finished: set[str] = set()
    for e in events:
        if e.type == "pass":
            passes[e.team] += 1
            completed[e.team] += e.outcome == "complete"
            pass_speeds.append(e.ball_speed_kmh)
        elif e.type == "shot":
            shots[e.team] += 1
            shot_speeds.append(e.ball_speed_kmh)
        elif e.type == "goal":
            goals[e.team] += 1
        elif e.type == "foul":
            fouls[e.team] += 1
        if e.positions:
            finished = set(e.positions)
        for pid, pos in e.positions.items():
            prev = last.get(pid)  # previous snapshot: distance
            if prev and prev[0] == e.period:
                km[pid] += math.dist(prev[2], pos) / 1000
            last[pid] = (e.period, e.clock, pos)
            ref = anchor.get(pid)  # last snapshot at least a window ago: speed
            if ref and ref[0] == e.period and e.clock - ref[1] >= MIN_SPEED_WINDOW_S:
                top[pid] = max(top[pid], math.dist(ref[2], pos) / (e.clock - ref[1]) * 3.6)
                anchor[pid] = (e.period, e.clock, pos)
            elif not ref or ref[0] != e.period:
                anchor[pid] = (e.period, e.clock, pos)
    return Summary(passes, completed, shots, goals, fouls, km, top, finished, pass_speeds, shot_speeds)


def check(s: Summary) -> list[str]:
    failures = []

    def within(name: str, value: float, label: str) -> None:
        lo, hi = RANGES[name]
        if not lo <= value <= hi:
            failures.append(f"{label}: {value:.2f} outside {lo}..{hi}")

    for team in sorted(s.passes):
        within("passes", s.passes[team], f"{team} passes")
        within("pass_completion", s.completed[team] / s.passes[team], f"{team} pass completion")
        within("shots", s.shots[team], f"{team} shots")
        within("fouls", s.fouls[team], f"{team} fouls")
        within(
            "top_speed_kmh", max(v for k, v in s.top_kmh.items() if k.startswith(team)), f"{team} top speed"
        )
    outfield = {k: v for k, v in s.km.items() if not k.endswith("-1") and k in s.finished}
    within("outfield_km_median", statistics.median(outfield.values()), "outfield distance median")
    for pid, v in sorted(outfield.items()):
        within("outfield_km", v, f"{pid} distance")
    for pid in ("NBR-1", "ASH-1"):
        within("keeper_km", s.km[pid], f"{pid} distance")
    within("pass_speed_kmh", min(s.pass_speeds), "slowest pass")
    within("pass_speed_kmh", max(s.pass_speeds), "fastest pass")
    within("shot_speed_kmh", min(s.shot_speeds), "slowest shot")
    within("shot_speed_kmh", max(s.shot_speeds), "fastest shot")
    return failures


def report(name: str, events: list[MatchEvent]) -> tuple[str, list[str]]:
    s = summarize(events)
    teams = sorted(s.passes)
    lines = [f"{name}: {len(events)} events"]
    for t in teams:
        lines.append(
            f"  {t}: goals {s.goals[t]}, passes {s.passes[t]} ({s.completed[t] / s.passes[t]:.0%}), "
            f"shots {s.shots[t]}, fouls {s.fouls[t]}, "
            f"top {max(v for k, v in s.top_kmh.items() if k.startswith(t)):.1f} km/h"
        )
    outfield = [v for k, v in s.km.items() if not k.endswith("-1")]
    lines.append(
        f"  outfield km: median {statistics.median(outfield):.1f}, {min(outfield):.1f}..{max(outfield):.1f}"
    )
    return "\n".join(lines), check(s)


def main(names: list[str]) -> int:
    from sim.scenario import load_scenario, scenario_names
    from sim.simulator import Simulator

    failed = False
    for name in names or scenario_names():
        text, failures = report(name, Simulator(load_scenario(name)).run())
        print(text)
        for f in failures:
            print(f"  FAIL {f}")
        failed |= bool(failures)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
