"""Write docs/plays.md from sim/plays.py, so the written play scripts and the simulator never drift apart.

uv run python scripts/plays_doc.py
"""

from pathlib import Path

from sim.plays import PLAYS, ROLES

OUT = Path(__file__).resolve().parent.parent / "docs" / "plays.md"


def where(at: tuple[float, float]) -> str:
    x, f = at
    lane = "on the touchline" if f < 0.15 else "wide" if f < 0.3 else "in the half-space" if f < 0.42 else "central"
    return f"{105 - x:.0f} m from goal, {lane}"


lines = [
    "# Play scripts",
    "",
    "Every simulated VAR call is set up by one of these moves. The simulator follows them pass by pass",
    "(`sim/simulator.py`, `_play`), and `tests/test_plays.py` checks that every scripted call in every match did:",
    "the same passes, in the same order, between the same roles. It also checks, across every match, that",
    "nobody passes backwards when wide open in front of goal. Generated from `sim/plays.py` by",
    "`scripts/plays_doc.py`.",
    "",
    "## Formations",
    "",
    "| Team | Roles (shirt number) |",
    "|---|---|",
]
shapes = {"NBR": "Northbridge, 4-3-3", "ASH": "Ashvale, 4-4-2"}
for team, roles in ROLES.items():
    lines.append(f"| {shapes[team]} | {', '.join(f'{r} {n}' for r, n in roles.items())} |")
lines += [
    "",
    "Roles in the plays are relative to the flank the move attacks down: FB is that side's full-back, W its",
    "winger or wide midfielder, CM its central midfielder (CM2 the other), CB its centre-back. KEY is the player",
    "the call is about. Defenders aren't scripted: they hold their shape, press the ball from the goal side",
    "near their box, and mark attackers there.",
    "",
]
for kind, play in PLAYS.items():
    start = f"1. The team wins the ball and plays it to the {play.first}."
    lines += [f"## {play.name} (`{kind}`)", "", play.pattern, "", start]
    for i, step in enumerate(play.steps, 2):
        runs = "; ".join(f"{r.who} runs to {where(r.to)}" for r in step.runs)
        lines.append(f"{i}. Pass to {step.to}, {where(step.at)} ({step.note}).{' ' + runs + '.' if runs else ''}")
    lines.append("")
OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {OUT}")
