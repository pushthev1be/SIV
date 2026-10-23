"""Real VAR calls replayed from StatsBomb open data. See docs/real-matches.md for each call and its sources.

uv run python -m real.clips            # fetches the data if needed, writes web/preview/data/real_*.json
"""

from __future__ import annotations

import sys

from engine.engine import Engine
from engine.export import write
from real.statsbomb import EVIDENCE_RESOLUTION_M, Clip, Converter

CLIPS = {
    # FIFA World Cup 2022, Japan 2-1 Spain, 1 Dec 2022. Mitoma cuts the ball back from the byline and
    # Tanaka scores (51'). The referee disallows it for the ball being out of play; VAR checks the
    # goal-line camera images and gives the goal: part of the ball was still over the line.
    "real_japan_spain": Clip(
        name="real_japan_spain",
        sb_match=3857255,
        match_code="m10",
        period=2,
        start="05:00",
        teams={"Japan": "JPN", "Spain": "ESP"},
        colours={"JPN": "#3d6fd6", "ESP": "#d64545"},
        home="JPN",
        var_check="ball_out",
        var_team="JPN",
        var_player_sb="Kaoru Mitoma",
        decision="goal_awarded",
        awarded_goal_sb="Ao Tanaka",
        score_before={"JPN": 1, "ESP": 1},
    ),
}
TEAM_NAMES = {"JPN": "Japan", "ESP": "Spain"}


def build(name: str):
    clip = CLIPS[name]
    conv = Converter(clip)
    events, meta = conv.convert()
    away = next(c for c in clip.teams.values() if c != clip.home)
    engine = Engine(clip.match_code, directions={clip.home: 1, away: -1}, score=clip.score_before,
                    timed_checks=False, evidence_resolution_m=EVIDENCE_RESOLUTION_M)  # fmt: skip
    pids = sorted({pid for e in events for pid in e.positions}, key=lambda p: (p[:3] != clip.home, int(p[4:])))
    roster = {}
    for pid in pids:
        named = pid in meta["names"]
        roster[pid] = {
            "name": meta["names"][pid] if named else f"{TEAM_NAMES[pid[:3]]} player",
            "number": int(pid[4:]) if named else None,
            "role": meta["roles"].get(pid, "MF"),
            "team": pid[:3],
        }
    teams = {
        code: {
            "name": TEAM_NAMES[code],
            "colour": clip.colours[code],
            "players": {p: r["name"] for p, r in roster.items() if r["team"] == code},
        }  # fmt: skip
        for code in (clip.home, away)
    }
    extra = {"source": {"name": "StatsBomb open data", "url": "https://github.com/statsbomb/open-data",
                        "real": True, "resolution_m": EVIDENCE_RESOLUTION_M},
             "start_score": clip.score_before}  # fmt: skip
    return events, engine, teams, roster, extra, away


def export(name: str):
    clip = CLIPS[name]
    events, engine, teams, roster, extra, away = build(name)
    return write(name, clip.match_code, clip.home, away, events, engine, teams, roster, extra)


if __name__ == "__main__":
    for n in sys.argv[1:] or list(CLIPS):
        out = export(n)
        print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
