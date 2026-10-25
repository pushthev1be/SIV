"""Seeded match simulator: free play from a simple decision model, plus scripted incidents.

    sim = Simulator(load_scenario("golden"))
    events = sim.run()          # list[MatchEvent], identical for the same scenario every time

The simulator plays the officials too: it emits var_check and var_decision events. It never states a
speed or a margin; the engine derives those from positions, body frames and the ball track.
"""

from __future__ import annotations

import math
import random

from contracts import BodyFrame, MatchEvent
from contracts.common import PITCH_LENGTH_M as L
from contracts.common import PITCH_WIDTH_M as W
from sim.match import DT, HALFWAY, Match, Player, clamp, dist, seg_dist, wide_open
from sim.plays import PLAYS, role_number
from sim.scenario import (
    BallOutGoal,
    BoxEdgeFoul,
    DogsoFoul,
    Goal,
    GoalLineClearance,
    OffsideGoal,
    PenaltyRetake,
    PenaltyReview,
    RedCard,
    Scenario,
)
from sim.teams import TEAMS

PERIOD_START = {1: 0.0, 2: 2700.0}
# How early a script starts steering play toward its incident: a passing build-up, then the incident as soon
# as it is set (so it can land a few seconds before `at`; nobody stands holding the ball). DOGSO keeps a long
# lead: those seconds are the other defenders dropping off so the fouler is the last man.
LEAD_S = {
    "offside_goal": 18.0, "red_card": 20.0, "goal": 0.0, "penalty_review": 10.0, "ball_out_goal": 10.0,
    "box_edge_foul": 10.0, "goal_line_clearance": 10.0, "penalty_retake": 10.0, "dogso_foul": 30.0,
}  # fmt: skip
BALL_RADIUS_M = 0.11
LINE_WIDTH_M = 0.12
BUILD_HOPS = 3  # passes in a scripted build-up before the final ball
BUILD_REACH_M = 12.0  # the scripted player receives within this of where the incident happens
BUILD_MIN = 2  # even if he wins it right there: lay it off and get it back, a one-two, not a solo run
BUILD_DONE = 99

# Body-part offsets (along the attacking direction, lateral), metres from the player's centre.
# Arms are deliberately the most forward part: Law 11 excludes them, and the engine must too.
ATTACKER_BODY = {
    "head": (0.10, 0.0),
    "torso": (0.0, 0.0),
    "knee_l": (0.24, 0.12),
    "foot_l": (0.38, 0.14),
    "knee_r": (-0.08, -0.12),
    "foot_r": (-0.28, -0.14),
    "arm": (0.52, 0.30),
}
DEFENDER_BODY = {
    "head": (-0.04, 0.0),
    "torso": (0.0, 0.0),
    "knee_l": (0.12, 0.12),
    "foot_l": (0.22, 0.15),
    "knee_r": (-0.10, -0.12),
    "foot_r": (-0.25, -0.15),
    "arm": (0.34, -0.32),
}
ATTACKER_FRONT = max(v[0] for k, v in ATTACKER_BODY.items() if k != "arm")
DEFENDER_FRONT = max(v[0] for k, v in DEFENDER_BODY.items() if k != "arm")
# A keeper facing out: "along" points at his own goal line, so the rear foot is the one nearest it.
KEEPER_BODY = {
    "head": (0.0, 0.0), "torso": (0.0, 0.0), "knee_l": (0.08, 0.12), "foot_l": (0.15, 0.16),
    "knee_r": (0.02, -0.12), "foot_r": (0.04, -0.16), "arm": (-0.10, 0.45),
}  # fmt: skip
KEEPER_REAR_FOOT = max(v[0] for k, v in KEEPER_BODY.items() if k.startswith("foot"))


def _r(p: tuple[float, float]) -> tuple[float, float]:
    return (round(p[0], 2), round(p[1], 2))


class Simulator:
    def __init__(self, scenario: Scenario) -> None:
        self.sc = scenario
        self.rng = random.Random(scenario.seed)
        self.m = Match(TEAMS[scenario.home], TEAMS[scenario.away], self.rng)
        self.events: list[MatchEvent] = []
        self.pending = sorted(scenario.script, key=lambda s: s.at)
        self.hops: dict[str, int] = {}  # passes so far in the build-up to each scripted player
        self.play_log: list[dict] = []  # every play script followed: the passes made, in order (tests check it)
        self.active: OffsideGoal | RedCard | Goal | PenaltyReview | None = None
        self.pressed = False
        self.held_since = 0.0  # when the carrier got the ball
        self.offside_restaged = 0  # times the scripted offside run had to be set up again
        # Constraints that scripts place on free play
        self.force_ball_to: str | None = None  # the other team loses the ball in its next action outside its third
        self.safe_team: str | None = None  # never loses the ball, never shoots unless allowed
        self.force_receiver: str | None = None
        self.goal_team: str | None = None  # every shot by this team scores
        self.suppress_after_goal = False  # a script handles what happens after its goal (a VAR check)

    # ── Public ───────────────────────────────────────────────────────────────

    def run(self) -> list[MatchEvent]:
        m, rng = self.m, self.rng
        for period in (1, 2):
            m.period, m.clock = period, PERIOD_START[period]
            kicking = m.home if period == 1 else m.away
            m.place(m.kickoff_slots(kicking))
            self._kickoff(kicking)
            end = PERIOD_START[period] + 2700 + (rng.uniform(60, 180) if period == 1 else rng.uniform(180, 330))
            while m.clock < end or (self.active is not None and m.clock < end + 120):
                self._step()
            m.carrier, m.flight = None, None
            self._emit("period_end", positions=False)
        return self.events

    # ── Emission and time ────────────────────────────────────────────────────

    def _emit(self, type_: str, *, clock: float | None = None, positions: bool = True, **fields) -> MatchEvent:
        m = self.m
        ev = MatchEvent(
            event_id=f"{self.sc.match_id}-e{len(self.events) + 1}",
            match_id=self.sc.match_id,
            period=m.period,
            clock=round(m.clock if clock is None else clock, 2),
            type=type_,
            positions=m.snapshot() if positions else {},
            **fields,
        )
        self.events.append(ev)
        return ev

    def _wait(self, seconds: float) -> None:
        every = round(1 / (self.sc.tracking_hz * DT)) if self.sc.tracking_hz else 0
        for _ in range(max(1, round(seconds / DT))):
            self.m.tick()
            if every and round(self.m.clock / DT) % every == 0:
                self._emit("tracking", start=_r(self.m.ball))

    def _fly(self, start, end, duration: float, runner: Player | None = None) -> None:
        m = self.m
        m.carrier = None
        m.flight = (start, end, m.clock, m.clock + duration)
        if runner is not None:
            runner.override, runner.urgency = end, 0.95
        self._wait(duration)
        m.flight, m.ball = None, end
        if runner is not None:
            runner.override, runner.urgency = None, None

    def _give(self, p: Player) -> None:
        m = self.m
        m.carrier, m.possession = p.pid, p.team
        p.override, p.urgency = None, None
        m.ball = p.pos
        self.pressed = False
        self.held_since = m.clock

    def _kickoff(self, team: str) -> None:
        taker = self.m.kickoff_taker(team)
        self.m.ball = (HALFWAY, W / 2)
        self._give(taker)
        self._emit("kickoff", team=team, player=taker.pid, start=_r(self.m.ball))

    # ── One decision by the ball carrier ─────────────────────────────────────

    def _step(self) -> None:
        if self._run_script():
            return
        m, rng = self.m, self.rng
        c = m.players[m.carrier]
        team, opp = c.team, m.other(c.team)
        tight = dist(m.nearest(opp, c.pos).pos, c.pos) < 3.0
        self._wait(rng.uniform(0.3, 0.9) if tight else rng.uniform(0.6, 1.9))  # closed down, he releases it sooner

        presser = m.nearest(opp, c.pos)
        d = dist(presser.pos, c.pos)
        safe = self.safe_team == team
        forced = self._forced_loss(c)
        if self.active is None and rng.random() < 0.18:
            self._start_run(c)
        if d < 4.0 and not self.pressed and rng.random() < 0.4:
            self._emit("pressure", team=opp, player=presser.pid, receiver=c.pid, start=_r(c.pos))
            self.pressed = True
        # Per decision; a closed-down carrier decides about twice as often, so the chance per decision is lower.
        if d < 2.2 and not safe and rng.random() < (0.6 if forced else 0.15):
            if self._challenge(c, presser):
                return

        gx = L if m.direction(team) == 1 else 0.0
        dg = dist(c.pos, (gx, W / 2))
        shot_p = 0.16 if dg < 14 else 0.07 if dg < 20 else 0.025 if dg < 28 else 0.0  # about 12-18 a match
        if tight:  # per decision, and he decides twice as often: closed down, he looks for a pass instead
            shot_p *= 0.5
        if self.goal_team == team:
            shot_p = 0.9 if dg < 22 else 0.0
        elif safe or self.force_ball_to == opp:
            shot_p = 0.0
        if self._clear_shot(c):
            # Free with a sight of goal, he shoots: no script holds him back. Whether it goes in is still the
            # match's business (_shot only scores for a scripted goal or a random-goals match).
            shot_p = 1.0
        # A carry now and then, less the longer he's had it and with a man on him: nobody dribbles for 15 s.
        held = m.clock - self.held_since
        carry_p = (0.30 if held < 4 else 0.12 if held < 8 else 0.0) * (0.5 if tight else 1.0)
        if not c.is_gk and rng.random() < shot_p:
            self._shot(c)
        elif c.is_gk or rng.random() >= carry_p or self._forced_for(c):
            self._pass(c)
        else:
            self._carry(c)

    def _challenge(self, c: Player, tackler: Player) -> bool:
        """Returns True when the challenge ends the carrier's action."""
        rng = self.rng
        if rng.random() < 0.2:  # about 10 fouls a team a match
            self._close_in(tackler, c)
            spot = c.pos
            self._emit("foul", team=tackler.team, player=tackler.pid, receiver=c.pid, start=_r(spot), contact=_r(spot))
            self._whistle(c, tackler, spot)
            if rng.random() < 0.14 and tackler.yellows == 0:
                tackler.yellows = 1
                self._wait(rng.uniform(8, 15))
                self._emit("card", team=tackler.team, player=tackler.pid, outcome="yellow")
            c.override, c.urgency = spot, 0.3  # once he's up, he takes the free kick where it happened
            self._wait(rng.uniform(10, 22))
            c.override, c.urgency = None, None
            self._give(c)
            return False
        won = rng.random() < 0.55 or self._forced_loss(c)
        self._emit("tackle", team=tackler.team, player=tackler.pid, receiver=c.pid, outcome="won" if won else "lost")
        if won:
            self._give(tackler)
            return True
        return False

    def _penalty_setup(self, team: str, taker: Player) -> None:
        """Law 14 positions: the taker behind the spot, the keeper on his line, and everyone else outside the
        area and at least 9.15 m from the spot (outside the arc), waiting there until the kick."""
        m = self.m
        keeper = next(p for p in m.on_pitch(m.other(team)) if p.is_gk)
        self._go(taker, team, (L - 12.2, W / 2), 0.4)
        self._go(keeper, team, (L - 0.4, W / 2), 0.5)
        for p in m.on_pitch():
            if p.pid in (taker.pid, keeper.pid) or p.is_gk:
                continue
            ax, ay = m.af(team, p.x, p.y)
            central = abs(ay - W / 2) < 9.5  # in line with the arc, which reaches 20.15 m out
            edge = L - (21.0 if central else 17.5)
            p.override, p.urgency = m.af(team, min(ax, edge - self.rng.uniform(0, 3)), clamp(ay, 3, W - 3)), 0.45

    def _drop(self, p: Player, team: str, af_xy) -> None:
        """A defender takes up his spot for the coming move only while the ball is in his half; otherwise he
        plays normally, instead of standing in his box for a minute."""
        if self.m.af(team, *self.m.ball)[0] > HALFWAY:  # shuffling across with the play, not rooted to the spot
            self._go(p, team, (af_xy[0], af_xy[1] + 1.5 * math.sin(self.m.clock / 2.5)), 0.8)
        else:
            p.override, p.urgency = None, None

    def _drive_and_meet(
        self, team, att: Player, deff: Player, goal_af, ready, deadline: float, hold_x: float, gap: float = 1.6
    ) -> bool:
        """One step of a run at goal: the attacker keeps driving at `goal_af`; the defender, goal-side, holds his
        ground at `hold_x` (attacking frame) on the attacker's line and backs off in front of him, but never steps
        out past `hold_x`, so the challenge comes there rather than after a chase from behind. True when they
        meet and `ready(ax, ay)` holds for the attacker, or at the deadline."""
        m = self.m
        att.override, att.urgency = m.af(team, *goal_af), 0.7
        ax, ay = m.af(team, att.x, att.y)
        gx, gy = goal_af
        n = math.hypot(gx - ax, gy - ay) or 1.0
        mx = max(ax + (gx - ax) / n * 1.4, hold_x)  # just ahead of him on his line, but no further out than hold_x
        my = ay + (gy - ay) / n * 1.4
        meet = m.af(team, mx, my)
        deff.chase = False
        deff.override, deff.urgency = meet, 0.6
        late = m.clock >= deadline and dist(att.pos, deff.pos) <= 3.0  # past time, only once they're close
        if (dist(att.pos, deff.pos) <= gap and ready(ax, ay)) or late:
            att.override = deff.override = None
            return True
        self._wait(0.2)
        return False

    def _close_in(self, tackler: Player, c: Player, gap: float = 0.8, most: float = 1.5) -> None:
        """The challenger actually reaches the player: contact at about `gap` metres, centre to centre.
        Never more than `most` metres of correction, so it reads as a lunge, not a jump."""
        d = dist(tackler.pos, c.pos)
        if d > gap:
            k = min(d - gap, most) / d
            tackler.x += (c.x - tackler.x) * k
            tackler.y += (c.y - tackler.y) * k

    def _whistle(self, fouled: Player | None = None, offender: Player | None = None, spot=None) -> None:
        """Play stops. The fouled player goes down, the offender pulls up, the ball stays where it was."""
        m, rng = self.m, self.rng
        m.carrier, m.flight = None, None
        if spot is not None:
            m.ball = spot
        for p in m.on_pitch():
            if p.run_until is not None:  # a run in progress ends at the whistle; he doesn't stand where it was going
                p.override, p.urgency = None, None
            p.run_until, p.chase = None, False
        if fouled is not None:
            fouled.override, fouled.urgency = None, None
            fouled.down_until = m.clock + rng.uniform(2.5, 4.0)
        if offender is not None:
            offender.override, offender.urgency = None, None
            offender.down_until = m.clock + rng.uniform(0.8, 1.5)

    def _clear_shot(self, c: Player) -> bool:
        """Wide open: inside 18 m, nobody within 3 m of him, nobody within 1.5 m of his line to goal."""
        return wide_open(c.pos, [o.pos for o in self.m.on_pitch(self.m.other(c.team)) if not o.is_gk],
                         (L if self.m.direction(c.team) == 1 else 0.0, W / 2))  # fmt: skip

    def _forced_loss(self, c: Player) -> bool:
        """A script needs the other side to have the ball: this carrier loses it, by a tackle or a stray pass. Not
        a keeper, and not deep in his own third (he plays it out first), so it's won back in midfield, where a
        move can start, rather than handed over in front of his own goal."""
        return self.force_ball_to == self.m.other(c.team) and not c.is_gk and self.m.af(c.team, c.x, c.y)[0] >= 25.0

    def _forced_for(self, c: Player) -> bool:
        """A scripted receiver applies only to his own team: the other side never passes the ball to him."""
        f = self.force_receiver
        return bool(f) and f != c.pid and self.m.players[f].team == c.team

    def _choose_receiver(self, c: Player) -> Player:
        m, rng = self.m, self.rng
        if self._forced_for(c):
            return m.players[self.force_receiver]
        cx = m.af(c.team, c.x, c.y)[0]
        opps = m.on_pitch(m.other(c.team))
        best, best_s = None, -1e9
        for p in m.on_pitch(c.team):
            if p.pid == c.pid:
                continue
            length = dist(c.pos, p.pos)
            if length < 4 or length > 55 or (p.is_gk and rng.random() > 0.12):
                continue
            if m.offside_position(p):
                # A mistimed run happens, but near the line: nobody plays a ball 15 m offside.
                beyond = m.af(p.team, p.x, p.y)[0] - m.second_last_af_x(p.team)
                if beyond > 3.0 or rng.random() > 0.04:
                    continue
            space = min(dist(p.pos, o.pos) for o in opps)
            progress = m.af(p.team, p.x, p.y)[0] - cx
            gumbel = -math.log(-math.log(rng.uniform(1e-9, 1 - 1e-9)))
            s = 0.07 * progress - 0.03 * max(0.0, length - 22) + 0.3 * min(space, 7.0) + gumbel
            if cx > L - 35 and progress < -3:  # in the final third, going backwards is the last resort
                s -= 0.1 * -progress
            if s > best_s:
                best, best_s = p, s
        return best or m.nearest(c.team, c.pos, exclude=c.pid)

    def _pass(self, c: Player, receiver: Player | None = None) -> None:
        m, rng = self.m, self.rng
        team, opp = c.team, m.other(c.team)
        r = receiver or self._choose_receiver(c)
        target = (clamp(r.x + r.vx * 0.8, 0.5, L - 0.5), clamp(r.y + r.vy * 0.8, 0.5, W - 0.5))
        length = dist(c.pos, target)
        speed = clamp(32 + length * 1.5 + rng.gauss(0, 6), 30, 105)
        flight = max(0.3, length / (speed / 3.6 * 0.75))

        lane = sorted(((seg_dist(o.pos, c.pos, target), o) for o in m.on_pitch(opp)), key=lambda t: t[0][0])
        # The presser at the passer's feet is not in the lane; only count the last 85% of it.
        risk = sum(0.22 * math.exp(-ld / 1.2) for (ld, t), _ in lane if ld < 4 and t > 0.15)
        p_fail = clamp(0.04 + 0.0035 * length + risk - 0.1 * (c.spec.skill - 0.75), 0.02, 0.8)
        offside = m.offside_position(r)
        if offside:
            outcome = "offside"
        elif self.safe_team == team or self.force_receiver == r.pid:
            outcome = "complete"
        elif self._forced_loss(c) or rng.random() < p_fail:
            in_lane = [(ld_t, o) for ld_t, o in lane if ld_t[1] > 0.15]
            outcome = "intercepted" if in_lane and in_lane[0][0][0] < 3.0 else "incomplete"
        else:
            outcome = "complete"

        end = target
        interceptor = None
        if outcome == "intercepted":
            (_, t), interceptor = in_lane[0]
            end = (c.x + (target[0] - c.x) * t, c.y + (target[1] - c.y) * t)
            flight *= max(t, 0.15)

        frame, track = None, None
        near_line = m.af(team, r.x, r.y)[0] > HALFWAY and abs(m.af(team, r.x, r.y)[0] - m.second_last_af_x(team)) < 2.5
        if offside or near_line:
            frame, track, kick_clock = self._offside_evidence(c, r, end, speed, jitter=True)
        else:
            kick_clock = m.clock
        self._emit(
            "pass",
            clock=kick_clock + rng.uniform(0.02, 0.09) if frame else None,
            team=team,
            player=c.pid,
            receiver=r.pid,
            outcome=outcome,
            start=_r(c.pos),
            end=_r(end),
            ball_speed_kmh=round(speed, 1),
            frame=frame,
            ball_track=track,
        )
        # The offside player runs onto it too: the flag goes up when he plays the ball, not at the kick.
        self._fly(c.pos, end, flight, runner=interceptor or (r if outcome in ("complete", "offside") else None))

        if outcome == "offside":
            self._whistle(spot=end)
            r.down_until = m.clock + rng.uniform(1.0, 1.6)  # pulls up at the flag
            self._wait(rng.uniform(8, 18))  # free kick to the defending side where he played it
            self._give(m.nearest(opp, end))
        elif outcome == "complete":
            self._give(r)
        elif outcome == "intercepted":
            self._emit("interception", team=opp, player=interceptor.pid, start=_r(end))
            self._give(interceptor)
        else:  # incomplete or offside: dead ball, restart to the other team
            self._wait(rng.uniform(8, 18))
            self._give(m.nearest(opp, end))

    def _start_run(self, c: Player) -> None:
        """A teammate sprints into space ahead of the ball for a few seconds."""
        m, rng = self.m, self.rng
        runners = [p for p in m.on_pitch(c.team) if p.spec.role in ("FW", "MF") and p.pid != c.pid]
        if not runners:
            return
        p = rng.choice(runners)
        ax, ay = m.af(p.team, p.x, p.y)
        target = (clamp(ax + rng.uniform(12, 22), 1, L - 4), clamp(ay + rng.uniform(-8, 8), 2, W - 2))
        p.override, p.urgency = m.af(p.team, *target), rng.uniform(0.9, 1.0)
        p.run_until = m.clock + rng.uniform(3, 5)

    def _carry(self, c: Player, end: tuple[float, float] | None = None) -> None:
        m, rng = self.m, self.rng
        d = m.direction(c.team)
        if end is None:
            length, a = rng.uniform(4, 14), rng.gauss(0, 0.5)
            end = (
                clamp(c.x + d * length * math.cos(a), 1, L - 1),
                clamp(c.y + length * math.sin(a), 1, W - 1),
            )
        space = min(dist(c.pos, o.pos) for o in m.on_pitch(m.other(c.team)))
        urgency = rng.uniform(0.85, 1.0) if space > 6 and rng.random() < 0.3 else rng.uniform(0.55, 0.8)
        self._emit("carry", team=c.team, player=c.pid, start=_r(c.pos), end=_r(end))
        c.override, c.urgency = end, urgency
        self._wait(dist(c.pos, end) / (c.top * urgency) + 0.5)
        c.override, c.urgency = None, None

    def _shot(self, c: Player) -> str:
        m, rng = self.m, self.rng
        team, opp = c.team, m.other(c.team)
        gx = L if m.direction(team) == 1 else 0.0
        dg = dist(c.pos, (gx, W / 2))
        xg = 0.45 * math.exp(-dg / 8.0) * (1 - 0.6 * min(1.0, abs(c.y - W / 2) / max(dg, 1.0)))
        speed = clamp(rng.gauss(95, 12) - dg * 0.5, 55, 130)
        roll = rng.random()
        keeper = next(p for p in m.on_pitch(opp) if p.is_gk)
        d = m.direction(team)
        if self.goal_team == team or (self.sc.random_goals and roll < xg and self.safe_team is None):
            # Into the net, in the half of the goal the keeper isn't covering
            side = -1 if keeper.y > W / 2 else 1
            outcome, end = "goal", (gx + d * 1.2, W / 2 + side * rng.uniform(1.2, 3.2))
        else:
            roll = rng.random()
            if roll < 0.34:  # he gets to it: the ball ends in his hands, in front of his line
                outcome, end = "saved", (keeper.x - d * 0.3, keeper.y + rng.uniform(-1.0, 1.0))
            elif roll < 0.74:
                side = 1 if rng.random() < 0.5 else -1
                outcome, end = "off_target", (gx + d * 3.0, W / 2 + side * rng.uniform(4.5, 12))
            else:
                outcome = "blocked"
                k = 2.0 / max(dg, 2.0)
                end = (c.x + (gx - c.x) * k, c.y + (W / 2 - c.y) * k)
        self._emit(
            "shot", team=team, player=c.pid, outcome=outcome, start=_r(c.pos), end=_r(end),
            ball_speed_kmh=round(speed, 1),
        )  # fmt: skip
        self._fly(c.pos, end, max(0.2, dist(c.pos, end) / (speed / 3.6)))
        if outcome == "goal":
            self._emit("goal", team=team, player=c.pid, start=_r(end))
            if not isinstance(self.active, OffsideGoal) and not self.suppress_after_goal:
                self._after_goal(team)
        elif outcome == "saved":
            self._give(keeper)
            self._wait(rng.uniform(2, 5))
        elif outcome == "off_target":
            self._wait(rng.uniform(15, 25))  # goal kick
            self._give(keeper)
        else:
            self._give(m.nearest(opp, end))
        return outcome

    def _after_goal(self, scoring: str) -> None:
        m, rng = self.m, self.rng
        kicking = m.other(scoring)
        m.carrier = None
        slots = m.kickoff_slots(kicking)
        for pid, slot in slots.items():
            m.players[pid].override, m.players[pid].urgency = slot, 0.45
        self._wait(rng.uniform(45, 70))
        for p in m.on_pitch():
            p.override, p.urgency = None, None
        if self.goal_team == scoring:
            self._finish_script()
        self._kickoff(kicking)

    # ── Offside evidence: body frame and 100 fps ball track ──────────────────

    def _offside_evidence(self, passer: Player, attacker: Player, end, speed_kmh: float, jitter: bool):
        """Body frame at the true kick, and the 100 fps track. The feed timestamp for the pass trails
        the kick by under one tick (0.02 to 0.09 s), so it never passes the next tracking sample."""
        m = self.m
        d = m.direction(attacker.team)
        line_x = m.second_last_af_x(attacker.team)
        body: dict[str, dict[str, tuple[float, float]]] = {attacker.pid: self._body(attacker, ATTACKER_BODY, d, jitter)}
        for p in m.on_pitch(m.other(attacker.team)):
            if abs(m.af(attacker.team, p.x, p.y)[0] - line_x) < 10:
                body[p.pid] = self._body(p, DEFENDER_BODY, d, jitter)
        tk = m.clock
        frame = BodyFrame(clock=tk, body=body)

        # Ball sits at the passer's foot until tk, then leaves at the pass speed.
        foot = (passer.x + d * 0.3, passer.y)
        ux, uy = end[0] - foot[0], end[1] - foot[1]
        n = math.hypot(ux, uy) or 1.0
        v = speed_kmh / 3.6
        track = []
        for i in range(-15, 16):
            s = max(0, i) * 0.01 * v
            track.append((round(tk + i * 0.01, 2), round(foot[0] + ux / n * s, 3), round(foot[1] + uy / n * s, 3)))
        return frame, track, tk

    def _body(self, p: Player, offsets, d: int, jitter: bool) -> dict[str, tuple[float, float]]:
        out = {}
        for part, (along, lat) in offsets.items():
            j = self.rng.uniform(-0.05, 0.05) if jitter else 0.0
            out[part] = (round(p.x + d * (along + j), 3), round(p.y + d * lat, 3))
        return out

    # ── Scripted incidents ───────────────────────────────────────────────────

    def _run_script(self) -> bool:
        m = self.m
        if self.active is None and self.pending and m.clock >= self.pending[0].at - LEAD_S[self.pending[0].kind]:
            self.active = self.pending.pop(0)
            s = self.active
            team_led = not isinstance(s, RedCard)
            self.force_ball_to = s.team if team_led else m.other(m.players[s.player].team)
        s = self.active
        if s is None:
            return False
        if m.possession != self.force_ball_to:
            return False  # free play, with the other team due to lose the ball
        if isinstance(s, OffsideGoal):
            return self._script_offside(s)
        if isinstance(s, RedCard):
            return self._script_red_card(s)
        if isinstance(s, PenaltyReview):
            return self._script_penalty(s)
        handlers = {
            BallOutGoal: self._script_ball_out, BoxEdgeFoul: self._script_box_edge,
            GoalLineClearance: self._script_goal_line, PenaltyRetake: self._script_penalty_retake,
            DogsoFoul: self._script_dogso,
        }  # fmt: skip
        if type(s) in handlers:
            return handlers[type(s)](s)
        self.safe_team = self.goal_team = s.team  # Goal: keep the ball and score from the next shot
        return False

    def _finish_script(self) -> None:
        for p in self.m.players.values():
            p.override, p.urgency, p.chase, p.max_af_x, p.run_until = None, None, False, None, None
        self.active = self.force_ball_to = self.safe_team = self.force_receiver = self.goal_team = None
        self.hops.clear()
        self.suppress_after_goal = False
        self.offside_restaged = 0

    def _script_offside(self, s: OffsideGoal) -> bool:
        m, rng = self.m, self.rng
        passer, attacker, defender = m.players[s.passer], m.players[s.attacker], m.players[s.defender]
        self.safe_team = s.team

        # Set-up in the attacking frame: the attacker waits 8 m onside, the defender 1 m deeper than the line.
        # Then the attacker sprints, the defender steps up, and the pass goes the instant the gap between the
        # attacker's front foot and the defender's deepest playable part equals the scripted margin.
        # The scenario gives these in the attacking frame (toward x = 105), so the move is staged at the right end
        # whichever way the team is kicking in that half.
        (dx, dy), ay = s.defender_pos, s.attacker_y
        passer_at = m.af(s.team, *s.passer_pos)
        att_start, def_start = m.af(s.team, dx - 8.0, ay), m.af(s.team, dx + 1.0, dy)
        for p, target in ((passer, passer_at), (attacker, att_start), (defender, def_start)):
            if m.carrier != p.pid:
                p.override, p.urgency = target, 0.9
        for p in m.on_pitch(m.other(s.team)):
            if p.pid != defender.pid and not p.is_gk:
                p.max_af_x = dx - 6.5  # well behind, so the defender stays second-last as he steps up

        if not self._play(s.team, passer, "offside_goal"):
            return False
        passer.override = passer_at
        # While they wait, the defender shuffles across with the play, as a back line does; the run is timed off
        # wherever he is at the kick, so the margin doesn't depend on it.
        def_start = m.af(s.team, dx + 1.0, dy + 1.5 * math.sin(m.clock / 2.5))
        defender.override = def_start
        settled = all(dist(p.pos, t) < 1.0 for p, t in ((attacker, att_start), (defender, def_start)))
        if not settled and m.clock < s.at + 20 * (1 + self.offside_restaged):
            self._wait(0.5)
            return True

        d = m.direction(s.team)
        attacker.override, attacker.urgency = m.af(s.team, dx + 10.0, ay), 1.0
        defender.override, defender.urgency = m.af(s.team, dx - 5.0, dy), 0.4
        for _ in range(80):
            gap = (m.af(s.team, defender.x, 0)[0] + DEFENDER_FRONT + s.margin_m) - (
                m.af(s.team, attacker.x, 0)[0] + ATTACKER_FRONT
            )  # how far the attacker's front foot still is from the scripted position
            closing = d * attacker.vx - d * defender.vx
            if closing > 0 and gap <= closing * DT:
                f = max(0.0, gap) / (closing * DT)  # the pass falls inside this tick: move both that far
                for p in (attacker, defender):
                    p.x += p.vx * DT * f
                    p.y += p.vy * DT * f
                break
            self._wait(DT)
        else:  # the gap never closed: set them up and go again, never play it with the wrong margin
            if self.offside_restaged < 3:
                self.offside_restaged += 1
                attacker.override, attacker.urgency = att_start, 0.9
                defender.override, defender.urgency = def_start, 0.9
                return True
        att_pos = attacker.pos
        run_to = (clamp(attacker.x + d * 6.0, 1, L - 1), clamp(attacker.y + rng.uniform(-1.5, 1.5), 1, W - 1))
        length = dist(passer.pos, run_to)
        speed = clamp(48 + length * 1.2, 60, 95)
        frame, track, tk = self._offside_evidence(passer, attacker, run_to, speed, jitter=False)
        self._emit(
            "pass", clock=tk + rng.uniform(0.02, 0.09), team=s.team, player=passer.pid, receiver=attacker.pid,
            outcome="complete", start=_r(passer.pos), end=_r(run_to), ball_speed_kmh=round(speed, 1),
            frame=frame, ball_track=track,
        )  # fmt: skip
        attacker.override = None
        self._fly(passer.pos, run_to, length / (speed / 3.6 * 0.8), runner=attacker)
        self._give(attacker)
        gx = L if d == 1 else 0.0
        self._carry(attacker, end=(attacker.x + (gx - attacker.x) * 0.35, attacker.y + (W / 2 - attacker.y) * 0.3))
        self.goal_team = s.team
        self._shot(attacker)

        # VAR: check opens a few seconds after the goal, decision after check_s.
        m.carrier = None
        for p in m.on_pitch():
            p.override, p.urgency, p.max_af_x = None, None, None
        self._wait(rng.uniform(4, 7))
        self._emit("var_check", team=s.team, player=attacker.pid, outcome="offside", positions=False)
        self._wait(s.check_s)
        self._emit("var_decision", team=s.team, player=attacker.pid, outcome=s.decision, positions=False)
        if s.decision == "goal_stands":
            self._finish_script()
            self._after_goal(s.team)  # kickoff as normal
            return True
        spot = att_pos  # indirect free kick where the attacker stood at the pass
        defender.override, defender.urgency = spot, 0.5
        self._wait(rng.uniform(12, 18))  # indirect free kick from the offside spot
        m.ball = spot
        self._finish_script()
        self._give(defender)
        return True

    def _script_red_card(self, s: RedCard) -> bool:
        m, rng = self.m, self.rng
        offender = m.players[s.player]
        victim_team = m.other(offender.team)
        self.safe_team = victim_team
        c = m.players[m.carrier]
        if m.clock < s.at or c.is_gk:
            return False
        offender.chase = True  # he goes for the ball, and the man on it, at full speed
        if dist(offender.pos, c.pos) > 1.6:
            if dist(offender.pos, c.pos) < 15.0:  # closing at a sprint: he arrives before it can be moved on
                self._wait(DT)
                return True
            # Too far off: the ball goes to the man nearest him, and the challenge comes, late, as he takes it.
            mates = [
                q for q in m.on_pitch(c.team)
                if not q.is_gk and q.pid != c.pid and 6 <= dist(q.pos, c.pos) <= 35 and not m.offside_position(q)
            ]  # fmt: skip
            near = min(mates, key=lambda q: dist(q.pos, offender.pos), default=None)
            if near is not None and dist(near.pos, offender.pos) < 15:
                self._pass(c, receiver=near)
                return True
            return False
        self._close_in(offender, c)
        spot = c.pos
        self._emit("foul", team=offender.team, player=offender.pid, receiver=c.pid, start=_r(spot), contact=_r(spot))
        self._whistle(c, offender, spot)
        c.override, c.urgency = spot, 0.3  # treated, then up for the free kick where it happened
        self._wait(rng.uniform(15, 25))
        self._emit("card", team=offender.team, player=offender.pid, outcome="red")
        if s.var:
            self._wait(rng.uniform(4, 7))
            self._emit("var_check", team=offender.team, player=offender.pid, outcome="red_card", positions=False)
            self._wait(s.check_s)
            overturned = s.var_result == "overturned"
            self._emit(
                "var_decision", team=offender.team, player=offender.pid,
                outcome="red_card_overturned" if overturned else "red_card_upheld", positions=False,
            )  # fmt: skip
            if overturned:
                offender.yellows = 1
                self._wait(rng.uniform(15, 25))
                self._finish_script()
                self._give(c)
                return True
        offender.on_pitch = False
        self._wait(rng.uniform(15, 25))
        self._finish_script()
        self._give(c)
        return True

    def _script_penalty(self, s: PenaltyReview) -> bool:
        """Get the ball to the attacker, run him into the box, and have the defender go through him."""
        m, rng = self.m, self.rng
        attacker, defender = m.players[s.attacker], m.players[s.defender]
        lane = 7.0 if m.af(s.team, attacker.x, attacker.y)[1] > W / 2 else -7.0
        if self.hops.get(attacker.pid) != BUILD_DONE:
            self._drop(defender, s.team, (L - 13.0, W / 2 + lane * 0.7))  # drops into the area as the move builds
        if not self._play(s.team, attacker, "penalty_review"):
            return False
        goal = (L - 5.0, W / 2 + lane * 0.4)
        if not self._drive_and_meet(s.team, attacker, defender, goal, lambda ax, ay: ax > L - 15.5, s.at + 90,
                                    hold_x=L - 14.0):  # fmt: skip
            return True

        # The challenge. The referee waves play on, so the feed has a tackle, not a foul.
        defender.chase = False
        attacker.override = None
        frame, track, contact = self._challenge_evidence(attacker, defender, s.played_ball)
        self._emit(
            "tackle", team=defender.team, player=defender.pid, receiver=attacker.pid,
            outcome="won" if s.played_ball else "loose", start=_r(attacker.pos), contact=_r(contact),
            frame=frame, ball_track=track,
        )  # fmt: skip
        attacker.override, attacker.urgency = None, None
        attacker.down_until = m.clock + rng.uniform(2.5, 3.5)  # no whistle, but he's on the ground
        self._give(defender)
        self._wait(rng.uniform(1.0, 2.0))
        out = m.af(s.team, rng.uniform(40, 60), 0.5)
        self._emit(
            "pass", team=defender.team, player=defender.pid, outcome="incomplete", start=_r(defender.pos),
            end=_r(out), ball_speed_kmh=round(rng.uniform(70, 90), 1),
        )  # fmt: skip
        self._fly(defender.pos, out, 1.5)
        m.carrier = None
        self._wait(rng.uniform(3, 6))
        self._emit(
            "var_check", team=defender.team, player=defender.pid, receiver=attacker.pid, outcome="penalty",
            positions=False,
        )  # fmt: skip
        self._wait(s.check_s)
        self._emit(
            "var_decision", team=defender.team, player=defender.pid,
            outcome="penalty_awarded" if s.awarded else "no_penalty", positions=False,
        )  # fmt: skip
        if not s.awarded:
            self._finish_script()
            self._wait(rng.uniform(10, 18))
            self._give(next(p for p in m.on_pitch(defender.team) if p.is_gk))
            return True

        spot = m.af(s.team, L - 11.0, W / 2)
        self._penalty_setup(s.team, attacker)
        self._wait(rng.uniform(35, 50))  # players clear the box, the kick is set
        for p in m.on_pitch():
            p.override, p.urgency = None, None
        m.ball = spot
        self._give(attacker)
        self.safe_team = None
        self.force_ball_to = None
        self.goal_team = s.team if s.scored else None
        outcome = self._shot(attacker)
        if outcome != "goal":
            self._finish_script()
        return True

    # ── Measured VAR calls beyond offside ────────────────────────────────────

    def _kick_evidence(self, kicker: Player, ball_from, ball_to, speed_kmh: float, bodies):
        """Body frame at the kick plus a 100 fps ball track: still until the kick, then moving to ball_to."""
        m = self.m
        d = m.direction(kicker.team)
        tk = m.clock
        frame = BodyFrame(clock=tk, body={p.pid: self._body(p, offsets, d, False) for p, offsets in bodies})
        ux, uy = ball_to[0] - ball_from[0], ball_to[1] - ball_from[1]
        n = math.hypot(ux, uy) or 1.0
        v = speed_kmh / 3.6
        steps = min(120, int(n / v / 0.01) + 1)
        track = []
        for i in range(-15, steps + 6):
            s = min(n, max(0, i) * 0.01 * v)
            track.append(
                (round(tk + i * 0.01, 2), round(ball_from[0] + ux / n * s, 3), round(ball_from[1] + uy / n * s, 3))
            )
        return frame, track, tk

    def _challenge_evidence(self, att: Player, deff: Player, played_ball: bool):
        """Body frame and 100 fps ball track at a challenge. The attacker has pushed the ball on; the
        defender's leading foot either reaches it first (and it deflects) or misses it and hits the attacker."""
        m = self.m
        d = m.direction(att.team)
        tk = m.clock
        front = (att.x + d * ATTACKER_BODY["foot_l"][0], att.y + d * ATTACKER_BODY["foot_l"][1])
        ball0 = (front[0] + d * (0.3 if played_ball else 1.05), front[1])  # where the ball is at contact
        foot = (ball0[0] - d * 0.12, ball0[1] + d * 0.08) if played_ball else (front[0] + d * 0.05, front[1] + d * 0.1)
        lead = DEFENDER_BODY["foot_l"]
        deff.x, deff.y = foot[0] - d * lead[0], foot[1] - d * lead[1]  # a short lunge from within 1.8 m
        frame = BodyFrame(clock=tk, body={att.pid: self._body(att, ATTACKER_BODY, d, False),
                                          deff.pid: self._body(deff, DEFENDER_BODY, d, False)})  # fmt: skip
        v_in, v_out = 4.0, 7.0  # m/s: the attacker's push, then the ball off the defender
        turn = math.radians(70.0) if played_ball else 0.0
        track = []
        for i in range(-20, 41):
            s = i * 0.01
            if s <= 0 or not played_ball:
                x, y = ball0[0] + d * v_in * s, ball0[1]
            else:
                x = ball0[0] + d * v_out * s * math.cos(turn)
                y = ball0[1] + d * v_out * s * math.sin(turn)
            track.append((round(tk + s, 2), round(x, 3), round(y, 3)))
        return frame, track, front  # contact on the attacker: his front foot

    def _play(self, team: str, key: Player, kind: str) -> bool:
        """Follow the play script for this kind of incident (sim/plays.py), pass by pass, until KEY has the
        ball where the play leaves him. Blocking: the other 21 players keep moving every tick while it runs
        (the defence holds its shape, presses the ball and marks in the box). Idempotent once done."""
        m = self.m
        if self.hops.get(key.pid) == BUILD_DONE:
            return True
        if m.carrier is None or m.possession != team:
            return False
        play = PLAYS[kind]
        if m.af(team, *m.ball)[0] > L - 35:  # already near goal: no recycling it back to start a move
            self.safe_team = self.force_receiver = None
            return False
        self.safe_team = team
        flank = "L" if m.af(team, key.x, key.y)[1] < W / 2 else "R"

        def spot(xy: tuple[float, float]) -> tuple[float, float]:
            x, f = xy
            x = min(x, max(m.second_last_af_x(team) - 2.5, 30.0))  # a receiver stays onside, in front of the line
            return m.af(team, x, f * W if flank == "L" else W - f * W)

        def who(role: str, near: tuple[float, float], busy: set[str]) -> Player:
            if role == "KEY":
                return key
            p = m.players.get(f"{team}-{role_number(team, role, flank)}")
            if p is None or not p.on_pitch or p.pid in busy or p.pid == key.pid:  # role taken: nearest free mate
                p = min((q for q in m.on_pitch(team) if not q.is_gk and q.pid not in busy and q.pid != key.pid),
                        key=lambda q: dist(q.pos, near))  # fmt: skip
            return p

        log = {"kind": kind, "team": team, "key": key.pid, "pattern": play.name, "passes": [], "start": m.clock}
        first_at = spot((max(m.af(team, *m.ball)[0] - 8.0, 25.0), 0.35))
        first = who(play.first, first_at, {m.carrier})
        if m.carrier != first.pid:  # the move starts by getting it to its first player
            self._pass(m.players[m.carrier], receiver=first)
            if m.carrier != first.pid:
                return False
        for step in play.steps:
            c = m.players[m.carrier]
            target = spot(step.at)
            r = who(step.to, target, {c.pid})
            r.override, r.urgency, r.run_until = target, 0.85, None
            for run in step.runs:
                runner = who(run.who, spot(run.to), {c.pid, r.pid})
                runner.override, runner.urgency, runner.run_until = spot(run.to), run.pace, m.clock + 6.0
            c.override = None  # the man on the ball keeps moving while the pass is set up
            for _ in range(60):  # he plays it when the receiver arrives and is onside (12 s at most)
                if dist(r.pos, target) < 2.5 and not m.offside_position(r):
                    break
                self._wait(0.2)
            if m.offside_position(r) or m.carrier != c.pid:  # never into an offside man: try the move again
                return False
            self._pass(c, receiver=r)
            if m.carrier != r.pid:  # intercepted or dead: the move broke down, it starts again next time
                self.hops.pop(key.pid, None)
                return False
            r.override, r.urgency = None, None
            log["passes"].append((c.pid, r.pid, step.to))
        log["end"], log["key_at"] = m.clock, key.pos
        self.play_log.append(log)
        self.hops[key.pid] = BUILD_DONE
        self.force_receiver = None
        return True

    def _build_up(self, team: str, pid: str, zone_af: tuple[float, float], reach: float = BUILD_REACH_M) -> bool:
        """Work the ball to `pid` near `zone_af` (attacking frame) through a passing move, not a solo run.

        Each call is one pass: to the open teammate furthest forward short of the zone, until the ball is
        close or the move has had its passes; then the final ball goes to `pid`, who has been running
        toward the zone. True once `pid` has the ball within `reach` of it, so his own carry stays short."""
        m = self.m
        self.safe_team = team
        if m.carrier is None or m.possession != team:
            if self.hops.get(pid) != BUILD_DONE:
                self.hops.pop(pid, None)  # the move broke down: when they win it back, it starts again
            return False
        key, zone = m.players[pid], m.af(team, *zone_af)
        c = m.players[m.carrier]
        hops = self.hops.get(pid, 0)
        if c.pid == pid and ((dist(c.pos, zone) <= reach and hops >= BUILD_MIN) or hops >= BUILD_HOPS + 2):
            self.force_receiver = None
            self.hops[pid] = BUILD_DONE  # latched: the script now moves him on from here without passing again
            return True
        key.override, key.urgency = zone, 0.75  # the run that the final pass finds
        bx = m.af(team, c.x, c.y)[0]
        options = [
            q for q in m.on_pitch(team)
            if q.pid not in (pid, c.pid) and not q.is_gk and not m.offside_position(q)
            and bx + 5 <= m.af(team, q.x, q.y)[0] <= zone_af[0] - 3 and 6 <= dist(c.pos, q.pos) <= 35
        ]  # fmt: skip
        if c.pid == pid or (hops < BUILD_HOPS and zone_af[0] - bx > 25):
            opps = m.on_pitch(m.other(team))
            # Nobody ahead and open: recycle it to a teammate nearby and go again, rather than a long ball.
            support = options or [
                q for q in m.on_pitch(team)
                if q.pid not in (c.pid, pid) and not q.is_gk and not m.offside_position(q)
                and 6 <= dist(c.pos, q.pos) <= 28
            ] or [q for q in m.on_pitch(team) if q.pid != c.pid and not q.is_gk]  # fmt: skip
            nxt = max(
                support, key=lambda q: m.af(team, q.x, q.y)[0] + 0.8 * min(min(dist(q.pos, o.pos) for o in opps), 8)
            )
        else:
            nxt = key
        self.force_receiver = nxt.pid
        self.hops[pid] = hops + 1
        return False

    def _have_ball(self, s_team: str, pid: str) -> bool:
        """True once `pid` has the ball; until then free play is steered to give it to them."""
        self.safe_team = s_team
        if self.m.carrier != pid:
            self.force_receiver = pid
            return False
        self.force_receiver = None
        return True

    def _go(self, p: Player, team: str, af_xy, urgency: float = 0.9) -> tuple[float, float]:
        target = self.m.af(team, *af_xy)
        p.override, p.urgency = target, urgency
        return target

    def _var(self, team: str, player: str, check: str, decision: str, check_s: float, receiver=None, lead=(4, 7)):
        self.m.carrier = None
        for p in self.m.on_pitch():
            p.override, p.urgency, p.max_af_x, p.chase = None, None, None, False
        self._wait(self.rng.uniform(*lead))
        self._emit("var_check", team=team, player=player, receiver=receiver, outcome=check, positions=False)
        self._wait(check_s)
        self._emit("var_decision", team=team, player=player, outcome=decision, positions=False)

    def _restart_with(self, p: Player, wait=(15, 25)) -> None:
        self._wait(self.rng.uniform(*wait))
        self._finish_script()
        self._give(p)

    def _script_ball_out(self, s) -> bool:
        m, rng = self.m, self.rng
        crosser, scorer = m.players[s.crosser], m.players[s.scorer]
        side = 1 if m.af(s.team, crosser.x, crosser.y)[1] > W / 2 else -1
        self._go(scorer, s.team, (L - 20.0, W / 2 - side * 4.0), 0.7)  # the scorer drifts in as the move builds
        if not self._play(s.team, crosser, "ball_out_goal"):
            return False
        byline = self._go(crosser, s.team, (L - 1.2, W / 2 + side * 20), 0.85)
        self._go(scorer, s.team, (L - 11.5, W / 2 - side * 2.5), 0.9)
        if dist(crosser.pos, byline) > 1.0 and m.clock < s.at + 90:
            self._wait(0.5)
            return True
        # The ball runs on over the byline, wholly out, and is pulled back anyway.
        ball = m.af(s.team, L + BALL_RADIUS_M + s.over_m, W / 2 + side * 20.4)
        to = (scorer.x, scorer.y)
        frame, track, tk = self._kick_evidence(crosser, ball, to, 55.0, [(crosser, ATTACKER_BODY)])
        self._emit(
            "pass", clock=tk + rng.uniform(0.02, 0.09), team=s.team, player=crosser.pid, receiver=scorer.pid,
            outcome="complete", start=_r(ball), end=_r(to), ball_speed_kmh=55.0, frame=frame, ball_track=track,
        )  # fmt: skip
        self._fly(ball, to, dist(ball, to) / (55 / 3.6 * 0.8), runner=scorer)
        self._give(scorer)
        self.goal_team, self.suppress_after_goal = s.team, True
        self._shot(scorer)
        self._var(s.team, crosser.pid, "ball_out", "no_goal", s.check_s)
        keeper = next(p for p in m.on_pitch(m.other(s.team)) if p.is_gk)
        self._restart_with(keeper)
        return True

    def _script_box_edge(self, s) -> bool:
        m, rng = self.m, self.rng
        att, deff = m.players[s.attacker], m.players[s.defender]
        ay = m.af(s.team, att.x, att.y)[1]
        lane_y = min(max(ay, W / 2 - 12), W / 2 + 12)
        contact_x = L - 16.5 - s.outside_m
        if self.hops.get(att.pid) != BUILD_DONE:
            # The defender holds the edge of his area, goal-side of the move, as it builds.
            self._drop(deff, s.team, (contact_x + 3.0, lane_y + 1.0))
            if not self._play(s.team, att, "box_edge_foul"):
                return False

        # The attacker runs at the area; the defender holds the edge of it and catches him there.
        def at_edge(ax: float, ay: float) -> bool:
            return ax >= contact_x - 0.6

        if not self._drive_and_meet(s.team, att, deff, (contact_x + 6.0, lane_y), at_edge, s.at + 90, contact_x + 0.5):
            return True
        # The challenge: contact starts exactly at the scripted spot.
        contact = m.af(s.team, contact_x, lane_y)
        att.x, att.y = m.af(s.team, contact_x - 0.35, lane_y)
        deff.x, deff.y = m.af(s.team, contact_x + 0.45, lane_y + 0.5)
        d = m.direction(s.team)
        frame = BodyFrame(clock=m.clock, body={att.pid: self._body(att, ATTACKER_BODY, d, False),
                                               deff.pid: self._body(deff, DEFENDER_BODY, d, False)})  # fmt: skip
        self._emit(
            "foul", team=deff.team, player=deff.pid, receiver=att.pid, outcome="penalty", start=_r(contact),
            contact=_r(contact), frame=frame,
        )  # fmt: skip
        self._whistle(att, deff, contact)
        decision = "free_kick" if s.outside_m > 0 else "penalty_confirmed"
        self._var(deff.team, deff.pid, "penalty_location", decision, s.check_s, receiver=att.pid, lead=(6, 9))
        if decision == "free_kick":
            self._restart_with(att, (20, 30))
            return True
        self._penalty_setup(s.team, att)
        self._wait(rng.uniform(30, 40))
        m.ball = m.af(s.team, L - 11.0, W / 2)
        att.x, att.y = m.af(s.team, L - 11.6, W / 2)
        for p in m.on_pitch():
            p.override, p.urgency = None, None
        self._give(att)
        self.safe_team = self.force_ball_to = None
        self.goal_team = s.team
        self._shot(att)
        return True

    def _script_goal_line(self, s) -> bool:
        m, rng = self.m, self.rng
        shooter, clearer = m.players[s.shooter], m.players[s.clearer]
        # The covering defender drops onto the far post while the move builds, so he's already there.
        post = m.af(s.team, L - 0.3, W / 2 + 2.2)
        self._drop(clearer, s.team, (L - 0.3, W / 2 + 2.2))
        if not self._play(s.team, shooter, "goal_line_clearance"):
            return False
        spot = self._go(shooter, s.team, (L - 12.0, W / 2 - 6.0), 0.8)
        self._go(clearer, s.team, (L - 0.3, W / 2 + 2.2), 0.8)
        gk = next(p for p in m.on_pitch(m.other(s.team)) if p.is_gk)
        self._go(gk, s.team, (L - 1.5, W / 2 - 2.6), 0.9)  # the keeper narrows the near post; the shot goes far side
        in_place = dist(shooter.pos, spot) <= 1.0 and dist(clearer.pos, post) <= 0.8
        if not in_place and m.clock < s.at + 90:
            self._wait(0.3)
            return True
        clearer.x, clearer.y = post  # under 0.8 m: he's standing on the line when it's struck
        end = m.af(s.team, L + BALL_RADIUS_M + s.over_m, W / 2 + 2.2)
        foot = m.af(s.team, *m.af(s.team, shooter.x + m.direction(s.team) * 0.3, shooter.y))
        keeper = next(p for p in m.on_pitch(m.other(s.team)) if p.is_gk)
        frame, track, tk = self._kick_evidence(
            shooter, foot, end, 88.0, [(shooter, ATTACKER_BODY), (clearer, DEFENDER_BODY), (keeper, DEFENDER_BODY)]
        )
        self._emit(
            "shot", clock=tk + rng.uniform(0.02, 0.09), team=s.team, player=shooter.pid, outcome="cleared",
            start=_r(foot), end=_r(end), ball_speed_kmh=88.0, frame=frame, ball_track=track,
        )  # fmt: skip
        self._fly(foot, end, dist(foot, end) / (88 / 3.6))
        self._give(clearer)
        out = m.af(s.team, L - 20, W + 0.5)
        self._emit("pass", team=clearer.team, player=clearer.pid, outcome="incomplete", start=_r(clearer.pos),
                   end=_r(out), ball_speed_kmh=82.0)  # fmt: skip
        self._fly(clearer.pos, out, 1.2)
        decision = "goal" if s.over_m > 0 else "no_goal"
        self._var(s.team, shooter.pid, "goal_line", decision, s.check_s, lead=(0.8, 1.2))
        self._restart_with(m.nearest(s.team, m.af(s.team, L - 20, W)), (12, 20))
        return True

    def _script_penalty_retake(self, s) -> bool:
        m, rng = self.m, self.rng
        taker, fouler = m.players[s.taker], m.players[s.fouler]
        keeper = next(p for p in m.on_pitch(m.other(s.team)) if p.is_gk)
        if self.hops.get(taker.pid) != BUILD_DONE:
            self._drop(fouler, s.team, (L - 11.0, W / 2 - 4.0))  # drops into the area as the move builds
        if not self._play(s.team, taker, "penalty_retake"):
            return False
        if not self._drive_and_meet(s.team, taker, fouler, (L - 6.0, W / 2 - 4.0), lambda ax, ay: ax > L - 15.5,
                                    s.at + 90, hold_x=L - 14.0):  # fmt: skip
            return True
        self._close_in(fouler, taker)
        self._emit("foul", team=fouler.team, player=fouler.pid, receiver=taker.pid, outcome="penalty",
                   start=_r(taker.pos), contact=_r(taker.pos))  # fmt: skip
        self._whistle(taker, fouler, taker.pos)
        self._penalty_setup(s.team, taker)
        self._wait(rng.uniform(35, 45))

        def kick(off_m: float, outcome: str) -> None:
            m.carrier = None
            spot = m.af(s.team, L - 11.0, W / 2)
            taker.x, taker.y = m.af(s.team, L - 11.6, W / 2)
            keeper.x, keeper.y = m.af(s.team, L - LINE_WIDTH_M - off_m - KEEPER_REAR_FOOT, W / 2)
            m.ball = spot
            if outcome == "saved":  # he gets a hand to it in front of his line
                aim = m.af(s.team, L - LINE_WIDTH_M - off_m - 0.7, W / 2 + 1.4)
            else:  # the keeper dives one way, the ball goes into the net the other
                aim = m.af(s.team, L + 1.2, W / 2 - 2.6)
            frame, track, tk = self._kick_evidence(
                taker, spot, aim, 96.0, [(taker, ATTACKER_BODY), (keeper, KEEPER_BODY)]
            )
            dive = m.af(s.team, L - 0.6, W / 2 + (1.6 if outcome == "saved" else 2.2))
            keeper.override, keeper.urgency = dive, 1.0
            self._emit(
                "shot", clock=tk + rng.uniform(0.02, 0.09), team=s.team, player=taker.pid, outcome=outcome,
                start=_r(spot), end=_r(aim), ball_speed_kmh=96.0, frame=frame, ball_track=track,
            )  # fmt: skip
            self._fly(spot, aim, dist(spot, aim) / (96 / 3.6))

        kick(s.off_m, "saved")
        out = m.af(s.team, L + 1.5, W / 2 + 12.0)  # parried behind for a corner
        self._fly(m.ball, out, 0.8)
        m.carrier = None
        self._wait(rng.uniform(2, 4))
        self._var(keeper.team, keeper.pid, "keeper_encroachment", "retake", s.check_s, lead=(1.5, 3))
        # The taker fetches the ball and carries it back to the spot; the keeper returns to his line.
        taker.override, taker.urgency = m.ball, 0.45
        self._go(keeper, s.team, (L - 0.3, W / 2), 0.5)
        for _ in range(40):
            if dist(taker.pos, m.ball) < 1.0:
                break
            self._wait(0.5)
        m.carrier = taker.pid
        self._penalty_setup(s.team, taker)
        self._wait(rng.uniform(25, 35))
        kick(-0.05, "goal")  # on the line this time
        self._emit("goal", team=s.team, player=taker.pid, start=_r(m.ball))
        self.goal_team = s.team
        self._after_goal(s.team)
        return True

    def _script_dogso(self, s) -> bool:
        m, rng = self.m, self.rng
        att, deff = m.players[s.attacker], m.players[s.defender]
        if not getattr(self, "_dogso_run", False):
            lane = W / 2 + (6.0 if m.af(s.team, att.x, att.y)[1] > W / 2 else -6.0)
            for p in m.on_pitch(m.other(s.team)):
                if p.pid != deff.pid and not p.is_gk:
                    p.max_af_x = L - 47.0  # pushed up and caught upfield: the defender will be the last man
            if m.clock < s.at - 8:  # the team keeps the ball while the defence is drawn up
                self.safe_team = s.team
                # He works the line meanwhile, checking short and drifting across, never standing still.
                t = m.clock
                self._go(att, s.team, (L - 50.0 + 2.5 * math.sin(t / 3.0), lane + 4.0 * math.sin(t / 4.3)), 0.5)
                mate = m.nearest(s.team, att.pos, exclude=att.pid)
                self.force_receiver = mate.pid if m.carrier == att.pid else None
                return False
            # Then the move that plays him in behind, received 40 m out: the breakaway starts from a pass.
            if not self._play(s.team, att, "dogso_foul"):
                return False
        ax = m.af(s.team, att.x, att.y)[0]
        if att.override is None or not getattr(self, "_dogso_run", False):
            # The breakaway: one carry at goal, at a pace the last defender can just catch.
            goal_run = self._go(att, s.team, (L - 7, W / 2 + 2), 0.66)
            self._emit("carry", team=att.team, player=att.pid, start=_r(att.pos), end=_r(goal_run))
            self._dogso_run = True
        deff.chase, deff.urgency = True, 1.0
        for p in m.on_pitch(deff.team):  # the rest of the back line turns and sprints back, a stride behind him
            if p.pid != deff.pid and not p.is_gk:
                p.override, p.urgency = m.af(s.team, L - 8.0, m.af(s.team, p.x, p.y)[1]), 0.95
                p.max_af_x = ax - 2.5
        caught = dist(deff.pos, att.pos) <= 1.4 and ax >= L - 28  # inside the 30 m an obvious chance needs
        if not caught and ax < L - 20.5 and m.clock < s.at + 60:  # never let it reach the area
            self._wait(0.1)
            return True
        self._dogso_run = False
        self._close_in(deff, att)
        self._emit("foul", team=deff.team, player=deff.pid, receiver=att.pid, start=_r(att.pos), contact=_r(att.pos))
        att.override = None
        self._whistle(att, deff, att.pos)
        self._wait(rng.uniform(12, 18))
        self._emit("card", team=deff.team, player=deff.pid, outcome="yellow")
        self._var(deff.team, deff.pid, "dogso", "red_card_dogso", s.check_s, receiver=att.pid, lead=(5, 8))
        deff.on_pitch = False
        self._restart_with(att, (18, 28))
        return True
