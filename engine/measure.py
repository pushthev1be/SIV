"""Measured VAR checks beyond offside. Each function reads only what the feed carries (ball track, body
frame, contact point, positions) and returns a signed distance against the line the law cares about.

Conventions: pitch lines sit on x = 0 / 105 and y = 0 / 68, the outer edge of each line. A positive
value_m always means the condition the check asks about is met:

    ball_out     the whole ball was over the goal line or touchline       (Law 9)
    goal_line    the whole ball was over the goal line between the posts  (Law 10)
    box_edge     the contact started outside the penalty area             (Laws 12, 14)
    keeper_line  the keeper had no part of either foot on the goal line   (Law 14)
    dogso        no single line; value_m is the attacker's distance to goal (Law 12)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from contracts import MatchEvent
from contracts.common import PITCH_LENGTH_M as L
from contracts.common import PITCH_WIDTH_M as W

BALL_RADIUS_M = 0.11  # a size 5 ball is about 22 cm across
LINE_WIDTH_M = 0.12  # lines are part of the area they bound; this is how far one reaches into the pitch
AREA_DEPTH_M = 16.5
AREA_HALF_WIDTH_M = 20.16
POST_HALF_GAP_M = 3.66
DOGSO_CONE_PAD_M = 4.0  # how far outside the line to the posts a defender still counts as "between"


@dataclass(frozen=True)
class Measured:
    kind: str
    value_m: float
    line: tuple[tuple[float, float], tuple[float, float]]
    line_label: str
    point: tuple[float, float]
    point_label: str
    subject: str | None
    evidence_event: str
    at: float  # clock of the measured moment
    factors: dict = field(default_factory=dict)

    def focus(self, pad: float = 2.5) -> tuple[float, float, float, float]:
        xs = [self.point[0], *(p[0] for p in self.line)] if self.kind == "dogso" else [self.point[0]]
        ys = [self.point[1], *(p[1] for p in self.line)] if self.kind == "dogso" else [self.point[1]]
        x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
        return (round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2))


def _r(v: float, nd: int = 2) -> float:
    return round(v, nd)


def ball_out(e: MatchEvent) -> Measured:
    """How far the whole ball was over the nearest boundary, from the 100 fps track before the kick."""
    best = None
    for t, x, y in e.ball_track or []:
        for over, line, label in (
            (x - L, ((L, 0.0), (L, W)), "goal line"),
            (-x, ((0.0, 0.0), (0.0, W)), "goal line"),
            (y - W, ((0.0, W), (L, W)), "touchline"),
            (-y, ((0.0, 0.0), (L, 0.0)), "touchline"),
        ):
            if best is None or over > best[0]:
                best = (over, line, label, (x, y), t)
    over, line, label, point, t = best
    return Measured(
        "ball_out", _r(over - BALL_RADIUS_M), line, label, (_r(point[0], 3), _r(point[1], 3)),
        "ball's edge nearest the pitch", e.player, e.event_id, t,
    )  # fmt: skip


def goal_line(e: MatchEvent, attack_dir: int) -> Measured:
    """How far the whole ball got over the goal line it was shot at, from the shot's ball track."""
    gx = L if attack_dir == 1 else 0.0
    best = max(e.ball_track or [], key=lambda s: attack_dir * (s[1] - gx))
    t, x, y = best
    over = attack_dir * (x - gx) - BALL_RADIUS_M
    line = ((gx, W / 2 - POST_HALF_GAP_M), (gx, W / 2 + POST_HALF_GAP_M))
    return Measured(
        "goal_line", _r(over), line, "goal line between the posts", (_r(x, 3), _r(y, 3)),
        "ball's trailing edge", e.player, e.event_id, t,
    )  # fmt: skip


def box_edge(e: MatchEvent, attack_dir: int) -> Measured:
    """Where contact started relative to the penalty area the fouled team was attacking."""
    cx, cy = e.contact
    ax = cx if attack_dir == 1 else L - cx  # in the attacking frame
    edge_x = L - AREA_DEPTH_M
    front = edge_x - ax  # > 0: short of the front edge
    side = abs(cy - W / 2) - AREA_HALF_WIDTH_M  # > 0: wide of the side edge
    abs_edge = edge_x if attack_dir == 1 else L - edge_x
    if front >= side:
        line = ((abs_edge, W / 2 - AREA_HALF_WIDTH_M), (abs_edge, W / 2 + AREA_HALF_WIDTH_M))
        label, outside = "edge of the penalty area", front
    else:
        y = W / 2 + math.copysign(AREA_HALF_WIDTH_M, cy - W / 2)
        x0, x1 = sorted((abs_edge, L if attack_dir == 1 else 0.0))
        line = ((x0, y), (x1, y))
        label, outside = "side of the penalty area", side
    return Measured(
        "box_edge", _r(outside), line, label, (_r(cx, 3), _r(cy, 3)), "where contact started",
        e.receiver, e.event_id, e.clock,
    )  # fmt: skip


def keeper_line(e: MatchEvent, keeper: str, attack_dir: int) -> Measured:
    """How far the keeper's nearest foot was off the goal line when the penalty was struck."""
    gx = L if attack_dir == 1 else 0.0
    body = (e.frame.body if e.frame else {}).get(keeper, {})
    feet = [body[p] for p in ("foot_l", "foot_r") if p in body] or [e.positions[keeper]]
    nearest = min(feet, key=lambda p: attack_dir * (gx - p[0]))  # the foot closest to the line
    off = attack_dir * (gx - nearest[0]) - LINE_WIDTH_M  # beyond the line's inner edge, into the pitch
    line = ((gx, W / 2 - POST_HALF_GAP_M), (gx, W / 2 + POST_HALF_GAP_M))
    return Measured(
        "keeper_line", _r(off), line, "goal line", (_r(nearest[0], 3), _r(nearest[1], 3)),
        "keeper's foot nearest the line", keeper, e.event_id, e.frame.clock if e.frame else e.clock,
    )  # fmt: skip


def dogso(
    e: MatchEvent, attacker: str, attack_dir: int, velocity: tuple[float, float] | None, in_control: bool
) -> Measured:
    """The four considerations for denying an obvious goal-scoring opportunity, measured at the foul."""
    gx = L if attack_dir == 1 else 0.0
    ax, ay = e.positions[attacker]
    goal = (gx, W / 2)
    dist = math.dist((ax, ay), goal)
    attacking = attacker[:3]

    def between(p: tuple[float, float]) -> bool:
        if attack_dir * (p[0] - ax) <= 0.5:  # not goal-side of the attacker
            return False
        frac = (p[0] - ax) / (gx - ax) if gx != ax else 1.0
        half = POST_HALF_GAP_M * frac + DOGSO_CONE_PAD_M  # the cone from the attacker to the posts
        mid = ay + (W / 2 - ay) * frac
        return abs(p[1] - mid) <= half

    defenders = [pid for pid, p in e.positions.items() if not pid.startswith(attacking) and pid != e.player]
    keeper = f"{'ASH' if attacking == 'NBR' else 'NBR'}-1"
    outfield_between = sum(1 for pid in defenders if pid != keeper and between(e.positions[pid]))
    direction = None
    if velocity and math.hypot(*velocity) > 0.5:
        to_goal = math.atan2(goal[1] - ay, goal[0] - ax)
        heading = math.atan2(velocity[1], velocity[0])
        direction = round(abs(math.degrees((heading - to_goal + math.pi) % (2 * math.pi) - math.pi)))
    factors = {
        "distance_to_goal_m": round(dist, 1),
        "defenders_between": outfield_between,
        "keeper_goal_side": keeper in e.positions and between(e.positions[keeper]),
        "in_control": in_control,
    }
    if direction is not None:
        factors["direction_off_goal_deg"] = direction
    return Measured(
        "dogso", round(dist, 1), ((_r(ax), _r(ay)), goal), "line to goal", (_r(ax), _r(ay)), "attacker at the foul",
        attacker, e.event_id, e.clock, factors,
    )  # fmt: skip


def obvious_chance(f: dict) -> bool:
    """A simple reading of the four Ds: close enough, heading for goal, nobody in the way, ball under control."""
    return (
        f["distance_to_goal_m"] <= 30
        and f["defenders_between"] == 0
        and f.get("direction_off_goal_deg", 0) <= 35
        and f["in_control"]
    )
