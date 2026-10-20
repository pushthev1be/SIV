"""Deterministic match statistics, derived only from positions and events.

Nothing here reads a stated speed for a player: speeds and distances come from consecutive position
snapshots (the 2 Hz tracking feed plus every on-ball event).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from contracts import MatchEvent
from contracts.common import PITCH_LENGTH_M, PITCH_WIDTH_M

SPEED_WINDOW_S = 1.0  # speeds over shorter gaps amplify position noise
SPRINT_START_KMH = 30.0  # a sprint worth a graphic
SPRINT_END_KMH = 24.0  # hysteresis, so one run is one sprint
ROLLING_WINDOW_S = 300.0  # five minutes of match time


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


# ── Movement: speed, distance, sprints ───────────────────────────────────────


@dataclass
class Sprint:
    player: str
    start_clock: float
    end_clock: float
    peak_kmh: float
    distance_m: float


@dataclass
class _Run:
    start: float
    peak: float
    distance: float


class Movement:
    def __init__(self) -> None:
        self.distance_m: dict[str, float] = {}
        self.top_kmh: dict[str, float] = {}
        self.current_kmh: dict[str, float] = {}
        self._last: dict[str, tuple[int, float, tuple[float, float]]] = {}
        self._anchor: dict[str, tuple[int, float, tuple[float, float]]] = {}
        self._runs: dict[str, _Run] = {}

    def update(self, e: MatchEvent) -> list[Sprint]:
        """Feed one snapshot; returns sprints that ended with it."""
        ended = []
        for pid, pos in e.positions.items():
            last = self._last.get(pid)
            step = 0.0
            if last and last[0] == e.period:
                step = math.dist(last[2], pos)
                self.distance_m[pid] = self.distance_m.get(pid, 0.0) + step
            self._last[pid] = (e.period, e.clock, pos)

            ref = self._anchor.get(pid)
            if not ref or ref[0] != e.period:
                self._anchor[pid] = (e.period, e.clock, pos)
                continue
            dt = e.clock - ref[1]
            if dt < SPEED_WINDOW_S:
                run = self._runs.get(pid)
                if run:
                    run.distance += step
                continue
            kmh = math.dist(ref[2], pos) / dt * 3.6
            self._anchor[pid] = (e.period, e.clock, pos)
            self.current_kmh[pid] = kmh
            self.top_kmh[pid] = max(self.top_kmh.get(pid, 0.0), kmh)

            run = self._runs.get(pid)
            if run is None and kmh >= SPRINT_START_KMH:
                self._runs[pid] = _Run(start=ref[1], peak=kmh, distance=step)
            elif run is not None:
                run.distance += step
                run.peak = max(run.peak, kmh)
                if kmh < SPRINT_END_KMH:
                    ended.append(Sprint(pid, run.start, e.clock, round(run.peak, 1), round(run.distance, 1)))
                    del self._runs[pid]
        return ended


# ── Pass difficulty ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PassQuality:
    length_m: float
    progress_m: float  # toward the opponents' goal
    difficulty: float  # 0 easy .. 1 very hard
    lane_defenders: int  # opponents within 2 m of the ball's path
    passer_pressure_m: float  # nearest opponent to the passer
    receiver_space_m: float  # nearest opponent to the receiver's position
    components: dict[str, float] = field(default_factory=dict)


def _seg_dist(p, a, b) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n)
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy)), t


def pass_quality(e: MatchEvent, attack_dir: int) -> PassQuality:
    """Difficulty from length, defenders near the lane, pressure on the passer, space at the target."""
    start, end = e.start, e.end
    team = e.team
    opps = [pos for pid, pos in e.positions.items() if not pid.startswith(team)]
    length = math.dist(start, end)
    progress = attack_dir * (end[0] - start[0])
    lane = [d for d, t in (_seg_dist(o, start, end) for o in opps) if t > 0.15]
    lane_close = sum(1 for d in lane if d < 2.0)
    pressure = min((math.dist(o, start) for o in opps), default=99.0)
    receiver = e.positions.get(e.receiver, end) if e.receiver else end
    space = min((math.dist(o, receiver) for o in opps), default=99.0)

    c = {
        "length": clamp((length - 8) / 37),
        "lane": clamp(sum(math.exp(-d / 1.5) for d in lane)),
        "pressure": clamp((3.5 - pressure) / 3.5),
        "receiver": clamp((5.0 - space) / 5.0),
        "progress": clamp(progress / 30),
    }
    weights = {"length": 0.30, "lane": 0.30, "pressure": 0.15, "receiver": 0.15, "progress": 0.10}
    difficulty = sum(weights[k] * v for k, v in c.items())
    return PassQuality(
        length_m=round(length, 1),
        progress_m=round(progress, 1),
        difficulty=round(difficulty, 2),
        lane_defenders=lane_close,
        passer_pressure_m=round(pressure, 1),
        receiver_space_m=round(space, 1),
        components={k: round(v, 2) for k, v in c.items()},
    )


def distance_to_goal(point: tuple[float, float], attack_dir: int) -> float:
    goal = (PITCH_LENGTH_M if attack_dir == 1 else 0.0, PITCH_WIDTH_M / 2)
    return math.dist(point, goal)


# ── Rolling possession, control, pressure ────────────────────────────────────

# What each event says about the flow of play, credited to the team in `team`.
_ON_BALL = {"pass", "carry", "shot"}
_PRESSING = {"pressure", "tackle", "interception"}
PRESSURE_FULL_PER_MIN = 9.0  # pressing actions per minute that read as 100
CHAOS_FULL_PER_MIN = 4.0  # turnovers per minute that read as 100


class Rolling:
    """Five-minute rolling window. All indices 0..100."""

    def __init__(self, window_s: float = ROLLING_WINDOW_S) -> None:
        self.window = window_s
        self._items: deque[tuple[int, float, str, str]] = deque()  # period, clock, team, tag

    def add(self, e: MatchEvent) -> None:
        if e.team is None:
            return
        if e.type in _ON_BALL:
            self._items.append((e.period, e.clock, e.team, "on_ball"))
            if e.type == "pass":
                ok = e.outcome == "complete"
                self._items.append((e.period, e.clock, e.team, "complete" if ok else "turnover"))
        elif e.type in _PRESSING:
            self._items.append((e.period, e.clock, e.team, "press"))
            if e.type == "interception" or (e.type == "tackle" and e.outcome == "won"):
                self._items.append((e.period, e.clock, e.team, "won_ball"))

    def metrics(self, period: int, clock: float, teams: tuple[str, str]) -> dict:
        while self._items and (self._items[0][0] != period or self._items[0][1] < clock - self.window):
            self._items.popleft()
        span_min = max(1.0, min(self.window, clock - (2700.0 if period == 2 else 0.0)) / 60)
        count = {(t, tag): 0 for t in teams for tag in ("on_ball", "complete", "turnover", "press", "won_ball")}
        for _, _, team, tag in self._items:
            if (team, tag) in count:
                count[(team, tag)] += 1
        on_ball = sum(count[(t, "on_ball")] for t in teams)
        passes = {t: count[(t, "complete")] + count[(t, "turnover")] for t in teams}
        turnovers = sum(count[(t, "turnover")] + count[(t, "won_ball")] for t in teams) / 2
        out = {
            t: {
                # an empty window (kickoff, long stoppage) reads as an even split; see "actions"
                "possession": round(100 * count[(t, "on_ball")] / on_ball) if on_ball else 50,
                # control: the team's own pass completion; chaos (below): turnovers by both teams
                "control": round(100 * count[(t, "complete")] / passes[t]) if passes[t] else 0,
                "pressure": round(100 * clamp(count[(t, "press")] / span_min / PRESSURE_FULL_PER_MIN)),
            }
            for t in teams
        }
        out["chaos"] = round(100 * clamp(turnovers / span_min / CHAOS_FULL_PER_MIN))
        out["actions"] = on_ball
        return out
