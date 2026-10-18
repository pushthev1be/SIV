"""Contract 2: incident state (decision state machine -> agents, overlays).

One record per incident, written only by the engine. States run
open -> reviewing -> decided -> restarted. Facts unlock for overlays only once decided.
"""

from typing import Literal

from pydantic import model_validator

from contracts.common import Clock, Contract, EventId, IncidentId

IncidentKind = Literal["offside", "foul", "penalty", "handball", "red_card", "goal_check"]
IncidentStatus = Literal["open", "reviewing", "decided", "restarted"]
ReasonCode = Literal[
    "tight_margin",
    "subjective_interference",
    "obstructed_view",
    "multiple_phases",
    "earlier_offence",
]

UNLOCKED_STATES: frozenset[str] = frozenset({"decided", "restarted"})


class Incident(Contract):
    incident_id: IncidentId
    kind: IncidentKind
    goal_event: EventId | None = None
    state: IncidentStatus
    on_field: str
    var_outcome: str | None = None
    final: str | None = None
    reason_codes: list[ReasonCode] = []
    opened_at: Clock
    decided_at: Clock | None = None
    facts: dict[str, float | str | int | bool] = {}

    @model_validator(mode="after")
    def _check(self) -> "Incident":
        unlocked = self.state in UNLOCKED_STATES
        if unlocked and (self.final is None or self.decided_at is None):
            raise ValueError("a decided incident needs final and decided_at")
        if not unlocked and (self.final is not None or self.decided_at is not None):
            raise ValueError("final and decided_at must stay empty until the decision")
        if self.decided_at is not None and self.decided_at < self.opened_at:
            raise ValueError("decided_at cannot precede opened_at")
        return self

    @property
    def unlocked(self) -> bool:
        return self.state in UNLOCKED_STATES

    def overlay_facts(self) -> dict[str, float | str | int | bool]:
        """Facts an overlay or agent may show. Empty before the decision (requirement N4)."""
        return dict(self.facts) if self.unlocked else {}
