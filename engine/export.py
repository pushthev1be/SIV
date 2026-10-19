"""Export a simulated match plus engine facts for the browser preview.

    uv run python -m engine.export golden       # writes web/preview/data/golden.json

Frames are the 2 Hz tracking feed, packed as [t, period, clock, ball_x, ball_y, x1, y1, x2, y2, ...]
in a fixed player order. `t` is continuous playback time: added time in the first half does not
overlap the second.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from engine.engine import Engine
from sim.scenario import load_scenario, scenario_names
from sim.simulator import Simulator
from sim.teams import TEAMS

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "preview" / "data"


def export(name: str) -> Path:
    sc = load_scenario(name)
    events = Simulator(sc).run()
    engine = Engine(sc.match_id)

    players = [f"{t}-{p.number}" for t in (sc.home, sc.away) for p in TEAMS[t].players]
    p1_end = max(e.clock for e in events if e.period == 1)

    def t_of(period: int, clock: float) -> float:
        return round(clock if period == 1 else p1_end + 30 + (clock - 2700.0), 2)

    frames, facts, feed = [], [], []
    for e in events:
        for f in engine.ingest(e):
            d = asdict(f)
            d["t"] = t_of(f.period, f.clock)
            facts.append(d)
        if e.type == "tracking":
            row = [t_of(e.period, e.clock), e.period, e.clock, *e.start]
            for pid in players:
                x, y = e.positions.get(pid, (None, None))
                row += [x, y]
            frames.append(row)
        elif e.type in ("pass", "shot", "goal", "foul", "card", "var_check", "var_decision", "kickoff"):
            feed.append(
                {"t": t_of(e.period, e.clock), "id": e.event_id, "type": e.type, "team": e.team,
                 "player": e.player, "receiver": e.receiver, "outcome": e.outcome}
            )  # fmt: skip

    data = {
        "scenario": name,
        "match_id": sc.match_id,
        "teams": {
            code: {
                "name": TEAMS[code].name,
                "colour": TEAMS[code].colour,
                "players": {f"{code}-{p.number}": p.name for p in TEAMS[code].players},
            }
            for code in (sc.home, sc.away)
        },  # fmt: skip
        "home": sc.home,
        "away": sc.away,
        "players": players,
        "second_half_t": t_of(2, 2700.0),
        "frames": frames,
        "facts": facts,
        "feed": feed,
        "final": engine.snapshot(),
        "replays": {
            iid: {phase: pkg.model_dump(mode="json") for phase, pkg in phases.items()}
            for iid, phases in engine.replays.items()
        },
        "player_log": {
            pid: [a | {"t": t_of(a["period"], a["clock"])} for a in log]
            for pid, log in engine.player_log.items()
        },
        "roster": {
            f"{code}-{p.number}": {"name": p.name, "number": p.number, "role": p.role, "team": code}
            for code in (sc.home, sc.away)
            for p in TEAMS[code].players
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{name}.json"
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m engine.export")
    ap.add_argument("scenarios", nargs="*", default=scenario_names())
    for name in ap.parse_args().scenarios:
        out = export(name)
        print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
