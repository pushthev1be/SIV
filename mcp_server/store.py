"""Match state behind the MCP tools: the engine plus what it has seen.

The store only reads. Whatever the source (a scenario replayed up to a moment for demos and tests,
or the live Event Hubs feed in Azure), facts come from the engine, and anything about an incident
goes through the same lock the engine uses: nothing decisive before the decision.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from contracts import MatchEvent
from engine.engine import Engine, incident_view
from engine.replay import clock_text

ON_BALL_TYPES = {
    "kickoff", "pass", "carry", "shot", "tackle", "interception", "pressure", "foul", "card", "goal",
    "var_check", "var_decision", "period_end",
}  # fmt: skip


class MatchStore:
    def __init__(self, match_id: str, roster: dict[str, str] | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or Engine(match_id)
        self.roster = roster or {}
        self.events: deque[MatchEvent] = deque(maxlen=4000)  # on-ball only; tracking stays in the engine
        self._lock = threading.Lock()

    # ── Feeding ──────────────────────────────────────────────────────────────

    def ingest(self, e: MatchEvent) -> None:
        with self._lock:
            self.engine.ingest(e)
            if e.type in ON_BALL_TYPES:
                self.events.append(e)

    @classmethod
    def from_real(cls, name: str) -> MatchStore:
        """A real-match clip from StatsBomb open data (see real/clips.py)."""
        from real.clips import CLIPS, build

        events, engine, _, roster, _, _ = build(name)
        store = cls(CLIPS[name].match_code, {pid: r["name"] for pid, r in roster.items()}, engine)
        for e in events:
            store.ingest(e)
        return store

    @classmethod
    def from_scenario(cls, name: str, until: tuple[int, float] | None = None) -> MatchStore:
        """Replay a seeded scenario into a store, up to (period, clock) if given."""
        from sim.scenario import load_scenario
        from sim.simulator import Simulator
        from sim.teams import TEAMS

        sc = load_scenario(name)
        roster = {f"{c}-{p.number}": p.name for c in (sc.home, sc.away) for p in TEAMS[c].players}
        store = cls(sc.match_id, roster)
        for e in Simulator(sc).run():
            if until and (e.period, e.clock) > until:
                break
            store.ingest(e)
        return store

    # ── Reads used by the tools ──────────────────────────────────────────────

    def _names(self, ids) -> dict[str, str]:
        return {i: self.roster[i] for i in ids if i in self.roster}

    def now(self) -> dict[str, Any]:
        eng = self.engine
        return {"period": eng.period, "clock": eng.clock, "clock_text": clock_text(eng.period, eng.clock)}

    def incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._lock:
            inc = self.engine.desk.incidents.get(incident_id)
            if inc is None:
                return None
            view = incident_view(inc)
            players = [v for k, v in view["facts"].items() if isinstance(v, str) and v in self.roster]
            goal = next((e for e in self.events if e.event_id == inc.goal_event), None)
            if goal and goal.player:
                view["goal"] = {"event_id": goal.event_id, "player": goal.player, "clock": goal.clock}
                players.append(goal.player)
            replay = self.engine.replays.get(incident_id, {})
            phase = "after" if "after" in replay and inc.state in ("decided", "restarted") else "during"
            pkg = replay.get(phase)
            if pkg is not None:
                view["involved"] = [{"player": f.player, "role": f.role} for f in pkg.focus]
                players += [f.player for f in pkg.focus]
            view["names"] = self._names(players)
            view["sources"] = [s for s in (inc.goal_event, inc.incident_id) if s] + (pkg.sources if pkg else [])
            view["sources"] = list(dict.fromkeys(view["sources"]))
            return view

    def incidents(self, state: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            ids = [i.incident_id for i in self.engine.desk.incidents.values() if state in (None, i.state)]
        return [self.incident(i) for i in ids]

    def open_check(self) -> dict[str, Any] | None:
        reviewing = self.incidents("reviewing")
        return reviewing[-1] if reviewing else None

    def replay(self, incident_id: str) -> dict[str, Any] | None:
        """The replay for the incident's current phase, without frames: what a writer needs."""
        with self._lock:
            inc = self.engine.desk.incidents.get(incident_id)
            phases = self.engine.replays.get(incident_id, {})
            if inc is None or not phases:
                return None
            phase = "after" if inc.state in ("decided", "restarted") and "after" in phases else "during"
            pkg = phases.get(phase)
            if pkg is None:
                return None
            i = min(range(len(pkg.frames)), key=lambda i: abs(pkg.frames[i].t - pkg.key_t))
            out: dict[str, Any] = {
                "incident_id": incident_id,
                "phase": phase,
                "key_clock": pkg.key_t,
                "key_clock_text": clock_text(pkg.period, pkg.key_t),
                "involved": [
                    {"player": f.player, "role": f.role, "speed_kmh_at_key_moment": pkg.speeds[f.player][i]}
                    for f in pkg.focus
                ],
                "moments": [{"clock": m.t, "label": m.label} for m in pkg.moments],
                "status": pkg.status,
                "facts": pkg.facts,
                "names": self._names(f.player for f in pkg.focus),
                "sources": pkg.sources,
            }
            if pkg.lines is not None:
                ln = pkg.lines
                out["measurement"] = {
                    "margin_m": ln.margin_m,
                    "attacker": ln.attacker,
                    "attacker_part": ln.attacker_part,
                    "defender": ln.defender,
                    "defender_part": ln.defender_part,
                }
            return out

    def recent_events(self, seconds: float = 30.0, types: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            now = (self.engine.period, self.engine.clock)
            rows = [
                {"event_id": e.event_id, "clock": e.clock, "type": e.type, "team": e.team, "player": e.player,
                 "receiver": e.receiver, "outcome": e.outcome}
                for e in self.events
                if e.period == now[0] and e.clock >= now[1] - seconds and (not types or e.type in types)
            ]  # fmt: skip
        players = {r[k] for r in rows for k in ("player", "receiver") if r[k]}
        return {"events": rows, "names": self._names(players), "sources": [r["event_id"] for r in rows]}

    def rolling_metrics(self) -> dict[str, Any]:
        with self._lock:
            snap = self.engine.snapshot()
        return {"now": self.now(), "score": snap["score"], "metrics": snap["metrics"], "sources": []}

    def player_stats(self, player_id: str) -> dict[str, Any] | None:
        with self._lock:
            if player_id not in self.roster:
                return None
            eng = self.engine
            log = list(eng.player_log.get(player_id, []))
        counts: dict[str, int] = {}
        for a in log:
            counts[a["action"]] = counts.get(a["action"], 0) + 1
        calls = [{"incident_id": a["incident_id"], "kind": a["kind"], "role": a["role"]} for a in log
                 if a["action"] == "call"]  # fmt: skip
        return {
            "player": player_id,
            "name": self.roster[player_id],
            "distance_km": round(eng.movement.distance_m.get(player_id, 0.0) / 1000, 2),
            "top_speed_kmh": round(eng.movement.top_kmh.get(player_id, 0.0), 1),
            "actions": counts,
            "calls": calls,
            "sources": [a["event_id"] for a in log[-10:]],
        }
