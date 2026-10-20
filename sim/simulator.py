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
from sim.match import DT, HALFWAY, Match, Player, clamp, dist, seg_dist
from sim.scenario import Goal, OffsideGoal, PenaltyReview, RedCard, Scenario
from sim.teams import TEAMS

PERIOD_START = {1: 0.0, 2: 2700.0}
LEAD_S = {"offside_goal": 30.0, "red_card": 20.0, "goal": 0.0, "penalty_review": 30.0}

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


def _r(p: tuple[float, float]) -> tuple[float, float]:
    return (round(p[0], 2), round(p[1], 2))


class Simulator:
    def __init__(self, scenario: Scenario) -> None:
        self.sc = scenario
        self.rng = random.Random(scenario.seed)
        self.m = Match(TEAMS[scenario.home], TEAMS[scenario.away], self.rng)
        self.events: list[MatchEvent] = []
        self.pending = sorted(scenario.script, key=lambda s: s.at)
        self.active: OffsideGoal | RedCard | Goal | PenaltyReview | None = None
        self.pressed = False
        # Constraints that scripts place on free play
        self.force_ball_to: str | None = None  # the other team loses the ball at its next action
        self.safe_team: str | None = None  # never loses the ball, never shoots unless allowed
        self.force_receiver: str | None = None
        self.goal_team: str | None = None  # every shot by this team scores

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
        self._wait(rng.uniform(0.6, 1.9))

        presser = m.nearest(opp, c.pos)
        d = dist(presser.pos, c.pos)
        safe = self.safe_team == team
        if self.active is None and rng.random() < 0.18:
            self._start_run(c)
        if d < 4.0 and not self.pressed and rng.random() < 0.4:
            self._emit("pressure", team=opp, player=presser.pid, receiver=c.pid, start=_r(c.pos))
            self.pressed = True
        if d < 2.2 and not safe and rng.random() < (0.6 if self.force_ball_to == opp else 0.2):
            if self._challenge(c, presser):
                return

        gx = L if m.direction(team) == 1 else 0.0
        dg = dist(c.pos, (gx, W / 2))
        shot_p = 0.30 if dg < 14 else 0.18 if dg < 20 else 0.07 if dg < 28 else 0.0
        if self.goal_team == team:
            shot_p = 0.9 if dg < 22 else 0.0
        elif safe or self.force_ball_to == opp:
            shot_p = 0.0
        if not c.is_gk and rng.random() < shot_p:
            self._shot(c)
        elif c.is_gk or rng.random() < 0.70 or self.force_receiver:
            self._pass(c)
        else:
            self._carry(c)

    def _challenge(self, c: Player, tackler: Player) -> bool:
        """Returns True when the challenge ends the carrier's action."""
        rng = self.rng
        if rng.random() < 0.13:
            self._emit("foul", team=tackler.team, player=tackler.pid, receiver=c.pid, start=_r(c.pos))
            if rng.random() < 0.14 and tackler.yellows == 0:
                tackler.yellows = 1
                self._wait(rng.uniform(8, 15))
                self._emit("card", team=tackler.team, player=tackler.pid, outcome="yellow")
            self._wait(rng.uniform(10, 22))  # free kick
            return False
        won = rng.random() < 0.55 or self.force_ball_to == tackler.team
        self._emit("tackle", team=tackler.team, player=tackler.pid, receiver=c.pid, outcome="won" if won else "lost")
        if won:
            self._give(tackler)
            return True
        return False

    def _choose_receiver(self, c: Player) -> Player:
        m, rng = self.m, self.rng
        if self.force_receiver and self.force_receiver != c.pid:
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
            if m.offside_position(p) and rng.random() > 0.04:
                continue
            space = min(dist(p.pos, o.pos) for o in opps)
            progress = m.af(p.team, p.x, p.y)[0] - cx
            gumbel = -math.log(-math.log(rng.uniform(1e-9, 1 - 1e-9)))
            s = 0.07 * progress - 0.03 * max(0.0, length - 22) + 0.3 * min(space, 7.0) + gumbel
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
        elif self.force_ball_to == opp or rng.random() < p_fail:
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
        self._fly(c.pos, end, flight, runner=interceptor or (r if outcome == "complete" else None))

        if outcome == "complete":
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
        xg = 0.6 * math.exp(-dg / 8.0) * (1 - 0.6 * min(1.0, abs(c.y - W / 2) / max(dg, 1.0)))
        speed = clamp(rng.gauss(95, 12) - dg * 0.5, 55, 130)
        roll = rng.random()
        if self.goal_team == team or (self.sc.random_goals and roll < xg and self.safe_team is None):
            outcome, end = "goal", (gx, W / 2 + rng.uniform(-3.2, 3.2))
        else:
            roll = rng.random()
            if roll < 0.34:
                outcome, end = "saved", (gx, W / 2 + rng.uniform(-3.0, 3.0))
            elif roll < 0.74:
                side = 1 if rng.random() < 0.5 else -1
                outcome, end = "off_target", (gx, W / 2 + side * rng.uniform(4.5, 12))
            else:
                outcome = "blocked"
                k = 2.0 / max(dg, 2.0)
                end = (c.x + (gx - c.x) * k, c.y + (W / 2 - c.y) * k)
        self._emit(
            "shot", team=team, player=c.pid, outcome=outcome, start=_r(c.pos), end=_r(end),
            ball_speed_kmh=round(speed, 1),
        )  # fmt: skip
        self._fly(c.pos, end, max(0.2, dist(c.pos, end) / (speed / 3.6)))
        keeper = next(p for p in m.on_pitch(opp) if p.is_gk)
        if outcome == "goal":
            self._emit("goal", team=team, player=c.pid, start=_r(end))
            if not isinstance(self.active, OffsideGoal):
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
            team_led = isinstance(s, OffsideGoal | Goal | PenaltyReview)
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
        self.safe_team = self.goal_team = s.team  # Goal: keep the ball and score from the next shot
        return False

    def _finish_script(self) -> None:
        for p in self.m.players.values():
            p.override, p.urgency, p.chase, p.max_af_x, p.run_until = None, None, False, None, None
        self.active = self.force_ball_to = self.safe_team = self.force_receiver = self.goal_team = None

    def _script_offside(self, s: OffsideGoal) -> bool:
        m, rng = self.m, self.rng
        passer, attacker, defender = m.players[s.passer], m.players[s.attacker], m.players[s.defender]
        self.safe_team = s.team

        # Set-up in the attacking frame: the attacker waits 8 m onside, the defender 1 m deeper than the line.
        # Then the attacker sprints, the defender steps up, and the pass goes the instant the gap between the
        # attacker's front foot and the defender's deepest playable part equals the scripted margin.
        dx, dy = m.af(s.team, *s.defender_pos)
        ay = m.af(s.team, 0.0, s.attacker_y)[1]
        att_start, def_start = m.af(s.team, dx - 8.0, ay), m.af(s.team, dx + 1.0, dy)
        for p, target in ((passer, s.passer_pos), (attacker, att_start), (defender, def_start)):
            if m.carrier != p.pid:
                p.override, p.urgency = target, 0.9
        for p in m.on_pitch(m.other(s.team)):
            if p.pid != defender.pid and not p.is_gk:
                p.max_af_x = dx - 6.5  # well behind, so the defender stays second-last as he steps up

        if m.carrier != passer.pid:
            self.force_receiver = passer.pid
            return False
        self.force_receiver = None
        passer.override = s.passer_pos
        settled = all(dist(p.pos, t) < 1.0 for p, t in ((attacker, att_start), (defender, def_start)))
        if (m.clock < s.at or not settled) and m.clock < s.at + 20:
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
        offender.chase = True
        c = m.players[m.carrier]
        if m.clock < s.at or (dist(offender.pos, c.pos) > 2.0 and m.clock < s.at + 60):
            return False
        self._emit("foul", team=offender.team, player=offender.pid, receiver=c.pid, start=_r(c.pos))
        m.carrier = None
        offender.chase = False
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
        self.safe_team = s.team
        if m.carrier != attacker.pid:
            self.force_receiver = attacker.pid
            return False
        self.force_receiver = None
        box = m.af(s.team, 96.0, W / 2 + (6.0 if attacker.y > W / 2 else -6.0))
        ax, ay = m.af(s.team, attacker.x, attacker.y)
        in_box = ax > L - 16.5 and abs(ay - W / 2) < 20.16
        defender.chase = True
        if (not in_box or m.clock < s.at) and m.clock < s.at + 90:
            attacker.override, attacker.urgency = box, 0.85
            self._wait(0.5)
            return True
        if dist(defender.pos, attacker.pos) > 1.8 and m.clock < s.at + 90:
            self._wait(0.3)
            return True

        # The challenge. The referee waves play on, so the feed has a tackle, not a foul.
        defender.chase = False
        attacker.override = None
        self._emit(
            "tackle", team=defender.team, player=defender.pid, receiver=attacker.pid, outcome="won",
            start=_r(attacker.pos),
        )  # fmt: skip
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
        attacker.override, attacker.urgency = m.af(s.team, L - 12.5, W / 2), 0.5
        self._wait(rng.uniform(35, 50))  # players clear the box, the kick is set
        attacker.override, attacker.urgency = None, None
        m.ball = spot
        self._give(attacker)
        self.safe_team = None
        self.force_ball_to = None
        self.goal_team = s.team if s.scored else None
        outcome = self._shot(attacker)
        if outcome != "goal":
            self._finish_script()
        return True
