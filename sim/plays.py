"""Play scripts: the move that leads to each kind of VAR call, written as a real tactical pattern.

Each play is a list of steps in the attacking team's frame (it attacks toward x = 105). A step is one pass
(or a carry), plus the runs made while the ball travels. Players are named by formation role, so the same
play works for either team:

    Northbridge 4-3-3:  GK, LB RB, LCB RCB, DM, LCM RCM, LW RW, ST
    Ashvale 4-4-2:      GK, LB RB, LCB RCB, LM RM, LCM RCM, ST (9) and ST2 (10)

Roles are relative to the flank the move attacks down ("FB" is the full-back on that side, "W" the
winger or wide midfielder, "CM" the central midfielder on that side, "CM2" the other one, "CB" the
centre-back on that side). "KEY" is the player the incident is about. A spot's y is a fraction across
the pitch from the flank's touchline: 0 is the touchline the move is on, 1 the far one.

The simulator follows these step by step (sim/simulator.py, `_play`), and tests/test_plays.py checks
that every scripted call in every match did: the same passes, in the same order, between the same roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Formation role -> shirt number, per team (see sim/teams.py; slot y < 0.5 is the left side).
ROLES: dict[str, dict[str, int]] = {
    "NBR": {"GK": 1, "LB": 2, "RB": 3, "LCB": 4, "RCB": 5, "DM": 6, "LCM": 8, "RCM": 10, "LW": 7, "RW": 11, "ST": 9},
    "ASH": {"GK": 1, "LB": 2, "RB": 3, "LCB": 4, "RCB": 5, "LM": 7, "RM": 11, "LCM": 6, "RCM": 8, "ST": 9, "ST2": 10},
}


@dataclass(frozen=True)
class Run:
    who: str
    to: tuple[float, float]  # (x, flank fraction)
    pace: float = 0.85


@dataclass(frozen=True)
class Step:
    to: str  # who receives (a role, or KEY)
    at: tuple[float, float]  # where he takes it: (x, flank fraction)
    runs: tuple[Run, ...] = ()  # made while this pass is on its way
    note: str = ""


@dataclass(frozen=True)
class Play:
    name: str
    pattern: str  # the tactic, in plain words
    steps: tuple[Step, ...]
    first: str = "CB"  # who the move starts from: the team wins it back and plays it to him
    tags: tuple[str, ...] = field(default_factory=tuple)


PLAYS: dict[str, Play] = {
    # Ball out of play: overload the flank, the full-back overlaps, the winger goes to the byline and pulls
    # it back. The cut-back itself is the incident (the ball has run over the line first).
    "ball_out_goal": Play(
        "Wide overload and byline cut-back",
        "Build through the centre-back and the pivot, switch to the flank, the full-back overlaps outside the "
        "winger, who takes it to the byline and cuts it back for the centre-forward arriving at the spot.",
        (
            Step("CM", (40.0, 0.35), note="into midfield"),
            Step("FB", (58.0, 0.08), runs=(Run("KEY", (76.0, 0.12)),), note="out to the full-back"),
            Step(
                "KEY",
                (78.0, 0.10),
                runs=(Run("FB", (82.0, 0.04)), Run("ST", (90.0, 0.45))),
                note="inside to the winger as the full-back overlaps",
            ),
        ),
    ),
    # Foul at the edge of the area: play between the lines, a wall pass, and the centre-back steps out.
    "box_edge_foul": Play(
        "Between the lines and a one-two at the edge",
        "A pass into the striker dropping between the lines, set back to the midfielder, who plays it first "
        "time into the striker's run: a wall pass that takes him at the edge of the area.",
        (
            Step("CM", (45.0, 0.42), note="into midfield"),
            Step("KEY", (72.0, 0.45), runs=(Run("CM", (68.0, 0.38)),), note="into the striker between the lines"),
            Step("CM", (70.0, 0.38), runs=(Run("KEY", (80.0, 0.45)),), note="set back"),
            Step("KEY", (80.0, 0.45), note="first time into his run"),
        ),
    ),
    # Goal-line clearance: a third-man run into the channel and a shot across goal to the far post.
    "goal_line_clearance": Play(
        "Third-man run into the channel",
        "Full-back to winger, winger inside to the midfielder, who finds the third man running in behind "
        "into the inside channel; he shoots across goal to the far post.",
        (
            Step("FB", (45.0, 0.10), note="out to the full-back"),
            Step("W", (66.0, 0.12), runs=(Run("KEY", (80.0, 0.33)),), note="up the line to the winger"),
            Step("CM", (64.0, 0.30), runs=(Run("KEY", (85.0, 0.36)),), note="inside, the third man is running"),
            Step("KEY", (85.0, 0.36), note="into the channel"),
        ),
    ),
    # Penalty incidents: a quick counter after winning the ball, into the box.
    "penalty_review": Play(
        "Counter-attack through the middle",
        "The ball is won in midfield and played straight into the striker, who lays it off; the midfielder "
        "slides the attacker through into the area.",
        (
            Step("ST", (70.0, 0.50), runs=(Run("KEY", (74.0, 0.35)),), note="straight into the striker"),
            Step("CM", (66.0, 0.42), runs=(Run("KEY", (80.0, 0.38)),), note="lay-off"),
            Step("KEY", (80.0, 0.38), note="through into the run"),
        ),
        first="CM",
    ),
    "penalty_retake": Play(
        "Counter-attack through the middle",
        "The ball is won in midfield and played straight into the striker, who lays it off; the midfielder "
        "slides the taker-to-be through into the area.",
        (
            Step("ST", (70.0, 0.50), runs=(Run("KEY", (74.0, 0.40)),), note="straight into the striker"),
            Step("CM", (66.0, 0.45), runs=(Run("KEY", (82.0, 0.42)),), note="lay-off"),
            Step("KEY", (82.0, 0.42), note="through into the run"),
        ),
        first="CM",
    ),
    # DOGSO: the defence is pushed high; one long ball over the top behind it.
    "dogso_foul": Play(
        "Ball over the top behind a high line",
        "With the defence pushed up to halfway, the centre-back looks up and plays it long over the top into "
        "the striker's run in behind; he's clean through with one defender chasing.",
        (
            Step("CB", (30.0, 0.40), note="back to the centre-back"),
            Step("KEY", (66.0, 0.45), note="long, over the top, into his run"),
        ),
        first="CM",
    ),
    # Offside goals: patient build-up to the playmaker, who looks for the run in behind (the run and the
    # pass are timed by the offside script itself).
    "offside_goal": Play(
        "Build-up to the playmaker",
        "Centre-back to the pivot, pivot to the playmaker in the half-space, who turns and looks for the "
        "striker's run against the defensive line.",
        (
            Step("CM2", (40.0, 0.55), note="into midfield"),
            Step("KEY", (60.0, 0.45), note="into the playmaker"),
        ),
    ),
}


def role_number(team: str, role: str, flank: str) -> int:
    """Shirt number for a role in a team, with flank-relative roles resolved ("FB" on the left is LB)."""
    side, other = ("L", "R") if flank == "L" else ("R", "L")
    roles = ROLES[team]
    alias = {
        "FB": f"{side}B",
        "CB": f"{side}CB",
        "CB2": f"{other}CB",
        "CM": f"{side}CM",
        "CM2": f"{other}CM",
        "W": f"{side}W" if f"{side}W" in roles else f"{side}M",
        "DM": "DM" if "DM" in roles else f"{other}CM",
    }.get(role, role)
    return roles[alias]
