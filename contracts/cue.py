"""Contract 3: overlay cue (orchestrator -> producer console -> renderer).

The broadcast layer gets one cue per moment; personal fields are resolved per viewer in the fan app.
"""

from typing import Literal

from pydantic import Field, model_validator

from contracts.common import Clock, Contract, Lang, Persona, PlayerId, TeamCode

CueKind = Literal[
    "speed",
    "pass_quality",
    "pressure",
    "momentum",
    "goal",
    "card",
    "var_check",
    "var_decision",
    "recap",
]
Layer = Literal[
    "scorebug",
    "player_tag",
    "chip",
    "momentum",
    "var_badge",
    "offside_lines",
    "band",
    "recap",
]
Metric = Literal["sprint_speed", "distance", "pass_difficulty", "possession", "pressure"]


class AudienceProfile(Contract):
    persona: Persona = "casual"
    club: TeamCode | None = None
    focus: PlayerId | None = None
    lang: Lang = "en"
    pin: Metric | None = None


class Band(Contract):
    tag: str = Field(max_length=24)
    text: str = Field(max_length=140)


class OverlayCue(Contract):
    cue_id: str = Field(pattern=r"^cue-\d+$")
    at: Clock
    ttl_s: float = Field(gt=0, le=60)
    kind: CueKind
    audience: AudienceProfile | None = None  # None on the broadcast layer
    layers: list[Layer] = Field(min_length=1)
    band: Band | None = None
    graphics: dict[str, dict[str, float | str]] = {}
    sources: list[str] = Field(min_length=1)  # event and incident IDs the cue traces to
    origin: Literal["agent", "template"]
    validated: bool
    approval: Literal["auto", "held", "approved", "killed"]

    @model_validator(mode="after")
    def _check(self) -> "OverlayCue":
        if not self.validated:
            raise ValueError("unvalidated text never airs (N3)")
        if ("band" in self.layers) != (self.band is not None):
            raise ValueError("band layer and band content must appear together")
        return self
