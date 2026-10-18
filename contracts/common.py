"""Shared types and pitch geometry for every contract.

Pitch coordinates are metres: x runs 0..105 along the length, y runs 0..68 across.
Clocks are seconds of match time since kickoff of period 1.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0

X = Annotated[float, Field(ge=-5.0, le=PITCH_LENGTH_M + 5.0)]  # small margin for run-offs
Y = Annotated[float, Field(ge=-5.0, le=PITCH_WIDTH_M + 5.0)]
Point = tuple[X, Y]
Clock = Annotated[float, Field(ge=0.0)]

TeamCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
PlayerId = Annotated[str, Field(pattern=r"^[A-Z]{3}-\d{1,2}$")]
EventId = Annotated[str, Field(pattern=r"^m\d{2}-e\d+$")]
IncidentId = Annotated[str, Field(pattern=r"^inc-m\d{2}-\d+$")]

Persona = Literal["casual", "analyst", "ref_nerd"]
Lang = Literal["en", "es"]


class Contract(BaseModel):
    """Base for all contracts: reject unknown fields so the two sides cannot drift silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)
