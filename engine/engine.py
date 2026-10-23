"""The engine: the only writer of match facts.

    engine = Engine("m01")
    for event in feed:
        for fact in engine.ingest(event):
            ...  # facts feed the moment detector, agents (through MCP) and deterministic overlays

Every Fact cites the event IDs it came from, so anything shown on screen traces back to the feed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from contracts import Incident, MatchEvent, ReplayPackage
from contracts.common import PITCH_LENGTH_M
from engine import measure as measured_checks
from engine.decisions import DecisionDesk
from engine.offside import measure
from engine.replay import build_replay
from engine.stats import Movement, Rolling, distance_to_goal, pass_quality

PASS_QUALITY_MIN = 0.55  # completed passes at or above this difficulty become facts
PRESSURE_SWING_UP, PRESSURE_SWING_RESET = 60, 45
METRICS_EVERY_S = 60.0
MULTIPLE_PHASES = 4  # on-ball events between the kick and the goal that make a check slow
MEASURED_KINDS = {"ball_out", "goal_line", "penalty_location", "keeper_encroachment", "dogso"}
HISTORY_EVENTS = 1500  # about five minutes of feed, enough for any replay window
# Per-player action log: event type -> what it means for the player in `player` (and `receiver`)
ACTION_OF = {
    "pass": "pass",
    "carry": "carry",
    "shot": "shot",
    "tackle": "tackle",
    "interception": "interception",
    "pressure": "pressure",
    "foul": "foul",
    "card": "card",
    "goal": "goal",
    "kickoff": "kickoff",
}
RECEIVER_ACTION = {"pass": "received", "foul": "fouled", "tackle": "tackled", "pressure": "pressed"}

RESTARTS = {"kickoff", "pass", "carry", "shot", "tackle", "interception"}
VAR_KIND = {
    "offside": "offside", "red_card": "red_card", "penalty": "penalty", "handball": "handball",
    "ball_out": "ball_out", "goal_line": "goal_line", "penalty_location": "penalty_location",
    "keeper_encroachment": "keeper_encroachment", "dogso": "dogso",
}  # fmt: skip
VAR_DECISION = {  # var_decision outcome -> (final, var_outcome)
    "no_goal": ("no_goal", None),
    "goal_stands": ("goal", "no_offence"),
    "red_card_upheld": ("red_card", "upheld"),
    "red_card_overturned": ("yellow", "overturned"),  # downgraded after review
    "penalty_awarded": ("penalty", "penalty"),
    "free_kick": ("free_kick", "outside_area"),
    "penalty_confirmed": ("penalty", "inside_area"),
    "retake": ("retake", "encroachment"),
    "red_card_dogso": ("red_card", "dogso"),
    "goal": ("goal", "goal_line"),
    "no_penalty": ("no_penalty", "no_change"),
    "goal_awarded": ("goal", "in_play"),  # disallowed on the field, given after review
}
# A check with no on-field call to review (e.g. play was waved on): what the referee had given
ON_FIELD_WHEN_UNCALLED = {
    "penalty": "no_penalty", "handball": "play_on", "red_card": "no_card",
    "goal_line": "play_on", "keeper_encroachment": "save_stands", "ball_out": "no_goal",
}  # fmt: skip


@dataclass(frozen=True)
class Fact:
    kind: str  # sprint | pass_quality | shot | goal | card | pressure_swing | metrics | incident
    period: int
    clock: float
    values: dict[str, Any]
    sources: list[str]
    team: str | None = None
    player: str | None = None


def incident_view(inc: Incident) -> dict[str, Any]:
    """What may leave the engine about an incident: facts stay locked until it is decided."""
    return inc.model_dump(exclude={"facts"}) | {"facts": inc.overlay_facts()}


@dataclass
class _Context:
    last_frame_pass: dict[str, MatchEvent] = field(default_factory=dict)  # per attacking team
    on_ball_since_frame: dict[str, int] = field(default_factory=dict)
    last_foul: MatchEvent | None = None
    last_metrics_clock: float = -1e9
    pressure_high: dict[str, bool] = field(default_factory=dict)
    review_opened: dict[str, float] = field(default_factory=dict)


class Engine:
    def __init__(
        self,
        match_id: str,
        *,
        directions: dict[str, int] | None = None,
        score: dict[str, int] | None = None,
        timed_checks: bool = True,
        evidence_resolution_m: float | None = None,
    ) -> None:
        """A feed that starts mid-match (a real clip) passes `directions` and `score` instead of a kickoff.
        `timed_checks=False` when the feed's VAR timings are not the real ones, so no duration is quoted;
        `evidence_resolution_m` when positions are coarser than tracking data (event data, about 10 cm)."""
        self.match_id = match_id
        self.desk = DecisionDesk(match_id)
        self.movement = Movement()
        self.rolling = Rolling()
        self.teams: tuple[str, str] | None = tuple(sorted(directions)) if directions else None  # type: ignore[assignment]
        self.direction: dict[str, int] = dict(directions or {})
        self.score: dict[str, int] = {t: 0 for t in self.direction} | dict(score or {})
        self.timed_checks = timed_checks
        self.evidence_resolution_m = evidence_resolution_m
        self.period = 1
        self.clock = 0.0
        self._ctx = _Context()
        self.history: deque[MatchEvent] = deque(maxlen=HISTORY_EVENTS)
        self.replays: dict[str, dict[str, ReplayPackage]] = {}
        self.player_log: dict[str, list[dict[str, Any]]] = {}
        self.measurements: dict[str, measured_checks.Measured] = {}

    # ── Public ───────────────────────────────────────────────────────────────

    def ingest(self, e: MatchEvent) -> list[Fact]:
        self.period, self.clock = e.period, e.clock
        self.history.append(e)
        self._log_actions(e)
        facts: list[Fact] = []
        if e.type == "kickoff":
            self._set_directions(e)
        if e.type in RESTARTS:
            facts += self._restart(e)
        for s in self.movement.update(e):
            values = {k: v for k, v in vars(s).items() if k != "player"}
            facts.append(self._fact("sprint", e, [e.event_id], player=s.player, team=s.player[:3], **values))
        self.rolling.add(e)

        handler = getattr(self, f"_on_{e.type}", None)
        if handler:
            facts += handler(e)
        if e.type == "tracking" and e.clock - self._ctx.last_metrics_clock >= METRICS_EVERY_S and self.teams:
            facts += self._metrics(e)
        facts += self._replays_for(facts, e)
        self._log_facts(facts)
        return facts

    def _replays_for(self, facts: list[Fact], e: MatchEvent) -> list[Fact]:
        """Build a during replay when a check opens and an after replay at every decision."""
        out = []
        for f in facts:
            if f.kind != "incident" or f.values["state"] not in ("reviewing", "decided"):
                continue
            phase = "during" if f.values["state"] == "reviewing" else "after"
            inc = self.desk.incidents[f.values["incident_id"]]
            pkg = build_replay(self.history, inc, phase, self.direction, self.measurements.get(inc.incident_id))
            if pkg is None:
                continue
            self.replays.setdefault(inc.incident_id, {})[phase] = pkg
            roles = {x.player: x.role for x in pkg.focus}
            out.append(self._fact("replay", e, pkg.sources, incident_id=inc.incident_id, phase=phase,
                                  kind=inc.kind, roles=roles))  # fmt: skip
        return out

    def _log_actions(self, e: MatchEvent) -> None:
        """Every on-ball involvement per player, each citing its event."""
        base = {"period": e.period, "clock": e.clock, "event_id": e.event_id}
        if e.player and e.type in ACTION_OF:
            self.player_log.setdefault(e.player, []).append(
                base | {"action": ACTION_OF[e.type], "outcome": e.outcome, "with": e.receiver}
            )
        if e.receiver and e.type in RECEIVER_ACTION and (e.type != "pass" or e.outcome == "complete"):
            self.player_log.setdefault(e.receiver, []).append(
                base | {"action": RECEIVER_ACTION[e.type], "outcome": e.outcome, "with": e.player}
            )

    def _log_facts(self, facts: list[Fact]) -> None:
        for f in facts:
            base = {"period": f.period, "clock": f.clock, "event_id": f.sources[0]}
            if f.kind == "sprint":
                self.player_log.setdefault(f.player, []).append(
                    base | {"action": "sprint", "kmh": f.values["peak_kmh"]}
                )
            elif f.kind == "pass_quality":
                self.player_log.setdefault(f.player, []).append(
                    base | {"action": "hard_pass", "difficulty": f.values["difficulty"]}
                )
            elif f.kind == "replay" and f.values["phase"] == "after":
                for pid, role in f.values["roles"].items():
                    self.player_log.setdefault(pid, []).append(
                        base | {"action": "call", "role": role, "incident_id": f.values["incident_id"],
                                "kind": f.values["kind"]}
                    )  # fmt: skip

    def snapshot(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "period": self.period,
            "clock": self.clock,
            "score": dict(self.score),
            "metrics": self.rolling.metrics(self.period, self.clock, self.teams) if self.teams else {},
            "distance_km": {k: round(v / 1000, 2) for k, v in sorted(self.movement.distance_m.items())},
            "top_speed_kmh": {k: round(v, 1) for k, v in sorted(self.movement.top_kmh.items())},
            "incidents": [incident_view(i) for i in self.desk.incidents.values()],
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _fact(self, fact_kind: str, e: MatchEvent, sources: list[str], /, team=None, player=None, **values) -> Fact:
        # positional-only, so a value named `kind` (an incident's kind) lands in values
        return Fact(fact_kind, e.period, e.clock, values, sources, team=team, player=player)

    def _set_directions(self, e: MatchEvent) -> None:
        """At kickoff both teams stand in their own half: that gives each team's attacking direction."""
        by_team: dict[str, list[float]] = {}
        for pid, (x, _) in e.positions.items():
            by_team.setdefault(pid[:3], []).append(x)
        a, b = sorted(by_team)
        self.teams = (a, b)
        for team, xs in by_team.items():
            self.direction[team] = 1 if sum(xs) / len(xs) < PITCH_LENGTH_M / 2 else -1
            self.score.setdefault(team, 0)

    def _incident_fact(self, inc: Incident, e: MatchEvent) -> Fact:
        sources = [s for s in (inc.goal_event, e.event_id, inc.incident_id) if s]
        return self._fact("incident", e, list(dict.fromkeys(sources)), **incident_view(inc))

    def _restart(self, e: MatchEvent) -> list[Fact]:
        """Play resumed: calls with no review are decided as given; decided calls are restarted."""
        out = []
        for inc in self.desk.unresolved():
            if inc.state == "open":
                final = "goal" if inc.kind == "goal_check" else inc.on_field
                out.append(self._incident_fact(self.desk.decide(inc.incident_id, final, e.clock), e))
        for inc in self.desk.awaiting_restart():
            out.append(self._incident_fact(self.desk.restart(inc.incident_id), e))
        return out

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_pass(self, e: MatchEvent) -> list[Fact]:
        ctx, d = self._ctx, self.direction.get(e.team, 1)
        out = []
        if e.frame is not None:
            ctx.last_frame_pass[e.team] = e
            ctx.on_ball_since_frame[e.team] = 0
        else:
            ctx.on_ball_since_frame[e.team] = ctx.on_ball_since_frame.get(e.team, 0) + 1
        if e.outcome == "complete":
            q = pass_quality(e, d)
            if q.difficulty >= PASS_QUALITY_MIN:
                out.append(
                    self._fact(
                        "pass_quality", e, [e.event_id], team=e.team, player=e.player,
                        receiver=e.receiver, ball_speed_kmh=e.ball_speed_kmh, **vars(q),
                    )
                )  # fmt: skip
        elif e.outcome == "offside":
            inc = self.desk.open("offside", "offside", e.clock)
            if e.frame is not None:
                self.desk.add_facts(inc.incident_id, self._offside_facts(e))
            inc = self.desk.decide(inc.incident_id, "offside", e.clock)
            out.append(self._incident_fact(inc, e))
        return out

    def _on_carry(self, e: MatchEvent) -> list[Fact]:
        self._ctx.on_ball_since_frame[e.team] = self._ctx.on_ball_since_frame.get(e.team, 0) + 1
        return []

    def _on_shot(self, e: MatchEvent) -> list[Fact]:
        self._ctx.on_ball_since_frame[e.team] = self._ctx.on_ball_since_frame.get(e.team, 0) + 1
        dist = distance_to_goal(e.start, self.direction.get(e.team, 1))
        return [
            self._fact(
                "shot", e, [e.event_id], team=e.team, player=e.player, outcome=e.outcome,
                ball_speed_kmh=e.ball_speed_kmh, distance_m=round(dist, 1),
            )
        ]  # fmt: skip

    def _on_goal(self, e: MatchEvent) -> list[Fact]:
        self.score[e.team] = self.score.get(e.team, 0) + 1
        inc = self.desk.open("goal_check", "goal", e.clock, goal_event=e.event_id)
        return [
            self._fact("goal", e, [e.event_id], team=e.team, player=e.player, score=dict(self.score)),
            self._incident_fact(inc, e),
        ]

    def _on_foul(self, e: MatchEvent) -> list[Fact]:
        self._ctx.last_foul = e
        if e.outcome != "penalty":
            return []
        inc = self.desk.open("penalty", "penalty", e.clock)
        facts = {
            "offender": e.player or "",
            "fouled": e.receiver or "",
            "foul_event": e.event_id,
            "foul_clock": e.clock,
        }
        where = e.contact or e.start
        if where is not None:
            facts["distance_to_goal_m"] = round(distance_to_goal(where, -self.direction.get(e.team, 1)), 1)
        inc = self.desk.add_facts(inc.incident_id, facts)
        return [self._incident_fact(inc, e)]

    def _on_card(self, e: MatchEvent) -> list[Fact]:
        kind = "red_card" if e.outcome in ("red", "second_yellow") else "foul"
        inc = self.desk.open(kind, e.outcome or "card", e.clock)
        foul = self._ctx.last_foul
        facts: dict[str, Any] = {"offender": e.player}
        if foul is not None and foul.player == e.player:
            facts |= {"foul_event": foul.event_id, "fouled": foul.receiver or "", "foul_clock": foul.clock}
        inc = self.desk.add_facts(inc.incident_id, facts)
        sources = [e.event_id] + ([foul.event_id] if foul is not None and foul.player == e.player else [])
        return [
            self._fact("card", e, sources, team=e.team, player=e.player, outcome=e.outcome),
            self._incident_fact(inc, e),
        ]

    def _on_var_check(self, e: MatchEvent) -> list[Fact]:
        open_ = [i for i in self.desk.unresolved() if i.state == "open"]
        if open_:
            inc = open_[-1]
        elif e.outcome in ON_FIELD_WHEN_UNCALLED:  # VAR looks at something the referee let go
            inc = self.desk.open(VAR_KIND[e.outcome], ON_FIELD_WHEN_UNCALLED[e.outcome], e.clock)
            challenge = self._last_challenge(e.player)
            if challenge is not None:
                d = distance_to_goal(challenge.start, -self.direction.get(e.team, 1)) if challenge.start else None
                facts = {"offender": e.player, "fouled": e.receiver or challenge.receiver or "",
                         "foul_event": challenge.event_id, "foul_clock": challenge.clock}  # fmt: skip
                if d is not None:
                    facts["distance_to_goal_m"] = round(d, 1)
                inc = self.desk.add_facts(inc.incident_id, facts)
        else:
            return []
        kind = VAR_KIND.get(e.outcome or "", inc.kind)
        inc = self.desk.review(inc.incident_id, kind=kind)
        self._ctx.review_opened[inc.incident_id] = e.clock
        if kind == "offside":
            kick = self._ctx.last_frame_pass.get(e.team)
            if kick is not None:
                codes = ["multiple_phases"] if self._ctx.on_ball_since_frame.get(e.team, 0) > MULTIPLE_PHASES else []
                inc = self.desk.add_facts(inc.incident_id, self._offside_facts(kick), codes)
        elif kind in MEASURED_KINDS or kind == "penalty":
            m = self._measure(kind, e)
            if m is not None:
                self.measurements[inc.incident_id] = m
                facts = {"value_m": m.value_m, "evidence_event": m.evidence_event, "measure": m.kind,
                         "subject": m.subject or "", **m.factors}  # fmt: skip
                if self.evidence_resolution_m is not None:
                    facts["data_resolution_m"] = self.evidence_resolution_m
                inc = self.desk.add_facts(inc.incident_id, facts)
        return [self._incident_fact(inc, e)]

    def _last(self, pred, within_s: float = 120.0) -> MatchEvent | None:
        for x in reversed(self.history):
            if x.period != self.period or x.clock < self.clock - within_s:
                return None
            if pred(x):
                return x
        return None

    def _measure(self, kind: str, e: MatchEvent):
        """Take the measurement a check needs from the evidence already in the feed."""
        mc = measured_checks
        if kind == "ball_out":
            ev = self._last(lambda x: x.type == "pass" and x.team == e.team and x.ball_track is not None)
            return mc.ball_out(ev) if ev else None
        if kind == "goal_line":
            ev = self._last(lambda x: x.type == "shot" and x.team == e.team and x.ball_track is not None)
            return mc.goal_line(ev, self.direction.get(e.team, 1)) if ev else None
        if kind == "penalty_location":
            ev = self._last(lambda x: x.type == "foul" and x.player == e.player and x.contact is not None)
            if ev is None:
                return None
            return mc.box_edge(ev, -self.direction.get(e.team, 1))
        if kind == "penalty":  # a judgement call, but whether the challenger reached the ball is measurable
            ev = self._last_challenge(e.player)
            if ev is None or ev.ball_track is None or ev.frame is None:
                return None
            return mc.ball_contact(ev, -self.direction.get(e.team, 1))
        if kind == "keeper_encroachment":
            ev = self._last(lambda x: x.type == "shot" and x.team != e.team and x.frame is not None)
            return mc.keeper_line(ev, e.player, self.direction.get(ev.team, 1)) if ev else None
        if kind == "dogso":
            foul = self._last(lambda x: x.type == "foul" and x.player == e.player)
            if foul is None or not foul.receiver:
                return None
            attacker = foul.receiver
            since_foul = self.clock - foul.clock  # the check opens a while after the foul: look back from the foul
            before = self._last(
                lambda x: x.type == "tracking" and x.clock <= foul.clock - 0.4 and attacker in x.positions,
                since_foul + 5.0,
            )
            velocity = None
            if before is not None and attacker in foul.positions:
                dt = foul.clock - before.clock
                (x0, y0), (x1, y1) = before.positions[attacker], foul.positions[attacker]
                velocity = ((x1 - x0) / dt, (y1 - y0) / dt)
            # Control: the attacker was the last player to have the ball before the foul.
            last_touch = self._last(
                lambda x: x.clock <= foul.clock and x.type in ("pass", "carry", "shot", "interception", "tackle"),
                since_foul + 30.0,
            )
            in_control = last_touch is not None and (
                (last_touch.type == "pass" and last_touch.outcome == "complete" and last_touch.receiver == attacker)
                or (last_touch.player == attacker and last_touch.type in ("carry", "interception"))
                or (last_touch.type == "tackle" and last_touch.player == attacker and last_touch.outcome == "won")
            )
            return mc.dogso(foul, attacker, -self.direction.get(e.team, 1), velocity, in_control)
        return None

    def _on_var_decision(self, e: MatchEvent) -> list[Fact]:
        reviewing = [i for i in self.desk.unresolved() if i.state == "reviewing"]
        if not reviewing:
            return []
        inc = reviewing[-1]
        final, var_outcome = VAR_DECISION.get(e.outcome or "", (e.outcome or "unknown", None))
        var_outcome = var_outcome or inc.kind
        opened = self._ctx.review_opened.get(inc.incident_id, inc.opened_at)
        if self.timed_checks:
            self.desk.add_facts(inc.incident_id, {"check_s": round(e.clock - opened, 1)})
        inc = self.desk.decide(inc.incident_id, final, e.clock, var_outcome=var_outcome)
        out = []
        if inc.goal_event is not None and final == "no_goal":  # a goal given on the field is taken off
            team = self.desk.incidents[inc.incident_id].facts.get("attacking_team") or e.team
            self.score[team] = max(0, self.score.get(team, 0) - 1)
            out.append(self._fact("goal", e, [e.event_id, inc.incident_id], team=team, disallowed=True,
                                  score=dict(self.score)))  # fmt: skip
        return [self._incident_fact(inc, e)] + out

    def _last_challenge(self, player: str | None) -> MatchEvent | None:
        """The most recent tackle or foul by this player in the last 30 s of feed."""
        for e in reversed(self.history):
            if e.clock < self.clock - 30:
                return None
            if e.type in ("tackle", "foul") and e.player == player:
                return e
        return None

    def _offside_facts(self, kick: MatchEvent) -> dict[str, Any]:
        m = measure(kick, self.direction.get(kick.team, 1))
        return {
            "margin_m": m.margin_m,
            "kick_clock": m.kick_clock,
            "kick_event": kick.event_id,
            "attacker": m.attacker,
            "defender": m.defender,
            "attacker_x": m.attacker_x,
            "defender_x": m.defender_x,
            "attacker_part": m.attacker_part,
            "defender_part": m.defender_part,
            "attacking_team": kick.team,
            "offside_position": m.offside_position,
        }

    def _metrics(self, e: MatchEvent) -> list[Fact]:
        self._ctx.last_metrics_clock = e.clock
        m = self.rolling.metrics(e.period, e.clock, self.teams)
        out = [self._fact("metrics", e, [e.event_id], score=dict(self.score), **m)]
        for team in self.teams:
            p = m[team]["pressure"]
            high = self._ctx.pressure_high.get(team, False)
            if not high and p >= PRESSURE_SWING_UP:
                self._ctx.pressure_high[team] = True
                out.append(self._fact("pressure_swing", e, [e.event_id], team=team, pressure=p,
                                      possession=m[team]["possession"], chaos=m["chaos"]))  # fmt: skip
            elif high and p < PRESSURE_SWING_RESET:
                self._ctx.pressure_high[team] = False
        return out
