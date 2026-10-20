"""Decision state machine: one record per incident, owned by the engine.

States only move forward: open -> reviewing -> decided -> restarted (reviewing is skipped when there
is no VAR check). Facts are attached as soon as the engine measures them, but the Incident contract
withholds them from overlays and agents until the incident is decided.
"""

from __future__ import annotations

from contracts import Incident
from contracts.incident import IncidentKind, ReasonCode

# Allowed moves. A call is always decided before play restarts; there is no way back.
NEXT = {
    "open": {"reviewing", "decided"},
    "reviewing": {"decided"},
    "decided": {"restarted"},
    "restarted": set(),
}
TIGHT_MARGIN_M = 0.5
SLOW_CHECK_S = 30.0
SUBJECTIVE_KINDS = {"penalty", "red_card", "handball"}


class IllegalTransition(ValueError):
    pass


class DecisionDesk:
    """Holds every incident for one match and applies transitions."""

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self.incidents: dict[str, Incident] = {}
        self._n = 0

    def open(self, kind: IncidentKind, on_field: str, at: float, goal_event: str | None = None) -> Incident:
        self._n += 1
        inc = Incident(
            incident_id=f"inc-{self.match_id}-{self._n:02d}",
            kind=kind,
            goal_event=goal_event,
            state="open",
            on_field=on_field,
            opened_at=at,
        )
        self.incidents[inc.incident_id] = inc
        return inc

    def _move(self, incident_id: str, state: str, **changes) -> Incident:
        inc = self.incidents[incident_id]
        if state not in NEXT[inc.state]:
            raise IllegalTransition(f"{incident_id}: {inc.state} -> {state}")
        inc = inc.model_copy(update={"state": state, **changes})
        self.incidents[incident_id] = Incident.model_validate(inc.model_dump())  # re-run contract rules
        return self.incidents[incident_id]

    def review(self, incident_id: str, kind: IncidentKind | None = None) -> Incident:
        changes = {"kind": kind} if kind else {}
        return self._move(incident_id, "reviewing", **changes)

    def add_facts(self, incident_id: str, facts: dict, reason_codes: list[ReasonCode] | None = None) -> Incident:
        inc = self.incidents[incident_id]
        merged = {**inc.facts, **facts}
        codes = sorted(set(inc.reason_codes) | set(reason_codes or []))
        self.incidents[incident_id] = inc.model_copy(update={"facts": merged, "reason_codes": codes})
        return self.incidents[incident_id]

    def decide(self, incident_id: str, final: str, at: float, var_outcome: str | None = None) -> Incident:
        inc = self.incidents[incident_id]
        codes = set(inc.reason_codes)
        if inc.state == "reviewing" and at - inc.opened_at > SLOW_CHECK_S:
            margin = inc.facts.get("margin_m")
            if isinstance(margin, float) and abs(margin) < TIGHT_MARGIN_M:
                codes.add("tight_margin")
            if inc.kind in SUBJECTIVE_KINDS:
                codes.add("subjective_judgement")
        return self._move(
            incident_id,
            "decided",
            final=final,
            decided_at=at,
            var_outcome=var_outcome,
            reason_codes=sorted(codes),
        )

    def restart(self, incident_id: str) -> Incident:
        return self._move(incident_id, "restarted")

    def unresolved(self) -> list[Incident]:
        return [i for i in self.incidents.values() if i.state in ("open", "reviewing")]

    def awaiting_restart(self) -> list[Incident]:
        return [i for i in self.incidents.values() if i.state == "decided"]
