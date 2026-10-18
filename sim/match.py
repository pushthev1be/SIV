"""Match state and player movement at 10 Hz.

Players move toward a target (team shape, a chase, or a scripted override) under speed and
acceleration limits, so positions in the event feed stay physically plausible and the engine's
derived speeds and distances land in real ranges.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from contracts.common import PITCH_LENGTH_M as L
from contracts.common import PITCH_WIDTH_M as W
from sim.teams import PlayerSpec, TeamSpec

DT = 0.1  # seconds per tick
ACCEL = 4.0  # m/s^2
HALFWAY = L / 2


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """Distance from p to segment ab, and the projection parameter t in 0..1."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else clamp(((p[0] - ax) * dx + (p[1] - ay) * dy) / n, 0.0, 1.0)
    return dist(p, (ax + t * dx, ay + t * dy)), t


@dataclass
class Player:
    pid: str
    team: str
    spec: PlayerSpec
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    on_pitch: bool = True
    yellows: int = 0
    wander: tuple[float, float] = (0.0, 0.0)
    wander_until: float = 0.0
    override: tuple[float, float] | None = None
    urgency: float | None = None  # fraction of top speed; None picks jog or run by distance
    chase: bool = False  # target the ball every tick
    max_af_x: float | None = None  # scripted cap on depth, in the opposing team's attacking frame
    run_until: float | None = None  # end of a sprint run; the override clears then

    @property
    def top(self) -> float:
        return self.spec.top_speed_kmh / 3.6

    @property
    def pos(self) -> tuple[float, float]:
        return (self.x, self.y)

    @property
    def is_gk(self) -> bool:
        return self.spec.role == "GK"


class Match:
    def __init__(self, home: TeamSpec, away: TeamSpec, rng: random.Random) -> None:
        self.rng = rng
        self.home, self.away = home.code, away.code
        self.players: dict[str, Player] = {}
        for team in (home, away):
            for spec in team.players:
                pid = f"{team.code}-{spec.number}"
                self.players[pid] = Player(pid, team.code, spec)
        self.period = 1
        self.clock = 0.0
        self.ball: tuple[float, float] = (HALFWAY, W / 2)
        self.carrier: str | None = None
        self.possession = self.home
        self.flight: tuple[tuple[float, float], tuple[float, float], float, float] | None = None

    # ── Frames of reference ──────────────────────────────────────────────────

    def direction(self, team: str) -> int:
        """+1 when the team attacks toward x = 105. Home attacks -x in period 1, +x in period 2."""
        home_dir = -1 if self.period == 1 else 1
        return home_dir if team == self.home else -home_dir

    def af(self, team: str, x: float, y: float) -> tuple[float, float]:
        """Absolute <-> the team's attacking frame (it attacks toward af_x = 105). Its own inverse."""
        return (x, y) if self.direction(team) == 1 else (L - x, W - y)

    def other(self, team: str) -> str:
        return self.away if team == self.home else self.home

    def on_pitch(self, team: str | None = None) -> list[Player]:
        return [p for p in self.players.values() if p.on_pitch and (team is None or p.team == team)]

    def nearest(self, team: str, point: tuple[float, float], exclude: str | None = None) -> Player:
        return min(
            (p for p in self.on_pitch(team) if p.pid != exclude),
            key=lambda p: dist(p.pos, point),
        )

    def second_last_af_x(self, attacking: str) -> float:
        """Second-deepest defender, in the attacking team's frame (Law 11 uses this line)."""
        xs = sorted(
            (self.af(attacking, p.x, p.y)[0] for p in self.on_pitch(self.other(attacking))), reverse=True
        )
        return xs[1]

    def offside_position(self, p: Player) -> bool:
        ax = self.af(p.team, p.x, p.y)[0]
        bx = self.af(p.team, *self.ball)[0]
        return ax > HALFWAY and ax > bx and ax > self.second_last_af_x(p.team)

    # ── Positions ────────────────────────────────────────────────────────────

    def shape_target(self, p: Player) -> tuple[float, float]:
        bx, by = self.af(p.team, *self.ball)
        attacking = self.possession == p.team
        sx, sy = p.spec.slot
        if p.is_gk:
            ax, ay = clamp(3 + bx * 0.12, 2, 16), W / 2 + (by - W / 2) * 0.25
        else:
            line = clamp(bx - (34 if attacking else 18), 8, 52 if attacking else 40)
            length = 50 if attacking else 34
            ax = line + (sx - 0.18) / 0.6 * length + p.wander[0]
            ay = W / 2 + (sy - 0.5) * (58 if attacking else 40) + (by - W / 2) * (0.15 if attacking else 0.35)
            ay += p.wander[1]
        return self.af(p.team, clamp(ax, 1, L - 1), clamp(ay, 1, W - 1))

    def kickoff_slots(self, kicking: str) -> dict[str, tuple[float, float]]:
        """Everyone in their own half, the kicking team's centre forward on the spot."""
        saved = self.ball, self.possession
        self.ball, self.possession = (HALFWAY, W / 2), kicking
        slots = {}
        for p in self.on_pitch():
            ax, ay = self.af(p.team, *self.shape_target(p))
            ax = min(ax - p.wander[0], HALFWAY - 1.0 if p.team == kicking else HALFWAY - 9.5)
            slots[p.pid] = self.af(p.team, ax, ay - p.wander[1])
        taker = self.kickoff_taker(kicking)
        slots[taker.pid] = (HALFWAY, W / 2)
        self.ball, self.possession = saved
        return slots

    def kickoff_taker(self, team: str) -> Player:
        fwds = [p for p in self.on_pitch(team) if p.spec.role == "FW"] or self.on_pitch(team)
        return max(fwds, key=lambda p: p.spec.slot[0] - abs(p.spec.slot[1] - 0.5))

    def place(self, slots: dict[str, tuple[float, float]]) -> None:
        for pid, (x, y) in slots.items():
            p = self.players[pid]
            p.x, p.y, p.vx, p.vy = x, y, 0.0, 0.0

    def snapshot(self) -> dict[str, tuple[float, float]]:
        return {p.pid: (round(p.x, 2), round(p.y, 2)) for p in self.on_pitch()}

    # ── Time ─────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        rng = self.rng
        defending = self.other(self.possession)
        chasers: list[str] = []
        if self.carrier and self.flight is None:
            near = sorted(
                (p for p in self.on_pitch(defending) if not p.is_gk), key=lambda p: dist(p.pos, self.ball)
            )
            chasers = [p.pid for p in near[:2]]

        for p in self.on_pitch():
            if p.run_until is not None and self.clock >= p.run_until:
                p.override, p.urgency, p.run_until = None, None, None
            if self.clock >= p.wander_until:
                r, a = 3.0 * math.sqrt(rng.random()), rng.uniform(0, 2 * math.pi)
                p.wander = (r * math.cos(a), r * math.sin(a))
                p.wander_until = self.clock + rng.uniform(4, 9)

            urgency = p.urgency
            if p.pid == self.carrier:
                target = p.override or p.pos
            elif p.chase:
                target, urgency = self.ball, 1.0
            elif p.override is not None:
                target = p.override
            elif p.pid in chasers:
                first = p.pid == chasers[0]
                bx, by = self.ball
                target = (bx, by) if first else ((p.x + bx) / 2, (p.y + by) / 2)
                urgency = 0.85 if first else 0.6
            else:
                target = self.shape_target(p)

            if p.max_af_x is not None:
                opp = self.other(p.team)
                ax, ay = self.af(opp, *target)
                target = self.af(opp, min(ax, p.max_af_x), ay)

            self._move(p, target, urgency)

        if self.flight is not None:
            (sx, sy), (ex, ey), t0, t1 = self.flight
            f = clamp((self.clock + DT - t0) / (t1 - t0), 0.0, 1.0) if t1 > t0 else 1.0
            self.ball = (sx + (ex - sx) * f, sy + (ey - sy) * f)
        elif self.carrier:
            self.ball = self.players[self.carrier].pos
        self.clock = round(self.clock + DT, 2)

    def _move(self, p: Player, target: tuple[float, float], urgency: float | None) -> None:
        dx, dy = target[0] - p.x, target[1] - p.y
        d = math.hypot(dx, dy)
        if urgency is None:
            urgency = 0.8 if d > 12 else 0.4
        want = min(p.top * urgency, d * 0.9)
        wx, wy = (dx / d * want, dy / d * want) if d > 1e-6 else (0.0, 0.0)
        ax, ay = wx - p.vx, wy - p.vy
        a = math.hypot(ax, ay)
        if a > ACCEL * DT:
            ax, ay = ax / a * ACCEL * DT, ay / a * ACCEL * DT
        p.vx, p.vy = p.vx + ax, p.vy + ay
        s = math.hypot(p.vx, p.vy)
        if s > p.top:
            p.vx, p.vy = p.vx / s * p.top, p.vy / s * p.top
        p.x = clamp(p.x + p.vx * DT, -1.0, L + 1.0)
        p.y = clamp(p.y + p.vy * DT, -1.0, W + 1.0)
