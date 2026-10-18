"""Contract 4: replay package (engine -> replay player)."""

from typing import Literal

from pydantic import Field, model_validator

from contracts.common import Clock, Contract, IncidentId, PlayerId, Point

ReplayAudience = Literal["stadium", "fan", "analyst", "officials"]


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
    # always: shown from the start; during: only while the check runs; after: only once decided
    reveal: Literal["always", "during", "after"]


class SlowWindow(Contract):
    start: Clock
    end: Clock
    rate: float = Field(gt=0, le=1)


class ReplayPackage(Contract):
    incident_id: IncidentId
    frames: list[ReplayFrame] = Field(min_length=2)
    ball: list[BallSample] = Field(min_length=2)
    moments: list[ReplayMoment]
    slow: SlowWindow | None = None
    status: dict[ReplayAudience, str]

    @model_validator(mode="after")
    def _check(self) -> "ReplayPackage":
        ts = [f.t for f in self.frames]
        if ts != sorted(ts):
            raise ValueError("frames must be in time order")
        if self.slow and not (ts[0] <= self.slow.start < self.slow.end <= ts[-1]):
            raise ValueError("slow window must sit inside the replay")
        return self
