"""Contract 4: replay package (engine -> replay player).

One package per incident and phase. A `during` package (built when a check opens) shows the build-up
only; an `after` package (built at the decision) adds the measurement. The contract enforces it:
a during package cannot carry lines or after-moments.
"""

from typing import Literal

from pydantic import Field, model_validator

from contracts.common import Clock, Contract, IncidentId, PlayerId, Point
from contracts.event import BodyPart
from contracts.incident import IncidentKind

ReplayAudience = Literal["stadium", "fan", "analyst", "officials"]
FocusRole = Literal["attacker", "defender", "passer", "scorer", "assist", "offender", "fouled"]


class ReplayFrame(Contract):
    t: Clock
    positions: dict[PlayerId, Point]


class BallSample(Contract):
    t: Clock
    x: float
    y: float
    z: float = 0.0


class ReplayMoment(Contract):
    t: Clock
    label: str = Field(max_length=80)
    # always: shown in every phase; during: only while the check runs; after: only once decided
    reveal: Literal["always", "during", "after"]


class SlowWindow(Contract):
    start: Clock
    end: Clock
    rate: float = Field(gt=0, le=1)


class ReplayFocus(Contract):
    player: PlayerId
    role: FocusRole


class OffsideLines(Contract):
    """The measurement at the moment of the pass. Only ever present after the decision."""

    attack_dir: Literal[1, -1]
    attacker: PlayerId
    defender: PlayerId
    attacker_x: float
    defender_x: float
    attacker_part: BodyPart | Literal["centre"]  # centre: no body frame for that player
    defender_part: BodyPart | Literal["centre"]
    margin_m: float
    body: dict[PlayerId, dict[BodyPart, Point]]


class ReplayPackage(Contract):
    incident_id: IncidentId
    kind: IncidentKind
    phase: Literal["during", "after"]
    period: Literal[1, 2]
    key_t: Clock  # the moment that matters: the pass, the shot, the challenge
    frames: list[ReplayFrame] = Field(min_length=2)
    ball: list[BallSample] = Field(min_length=2)
    moments: list[ReplayMoment]
    slow: SlowWindow | None = None
    focus: list[ReplayFocus] = Field(min_length=1)
    # km/h per frame for each focus player, derived from positions; None where it cannot be measured
    speeds: dict[PlayerId, list[float | None]]
    lines: OffsideLines | None = None
    status: dict[ReplayAudience, str]
    sources: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "ReplayPackage":
        ts = [f.t for f in self.frames]
        if ts != sorted(ts):
            raise ValueError("frames must be in time order")
        if self.slow and not (ts[0] <= self.slow.start < self.slow.end <= ts[-1]):
            raise ValueError("slow window must sit inside the replay")
        if not ts[0] <= self.key_t <= ts[-1]:
            raise ValueError("key moment must sit inside the replay")
        if any(len(v) != len(self.frames) for v in self.speeds.values()):
            raise ValueError("speeds must align with frames")
        if self.phase == "during" and (
            self.lines is not None or any(m.reveal == "after" for m in self.moments)
        ):
            raise ValueError("a during replay cannot carry the measurement (N4)")
        return self
