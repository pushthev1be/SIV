"""Scenario files: a seed plus scripted incidents layered on top of free play."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCENARIO_DIR = Path(__file__).parent / "scenarios"


class _Script(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: float = Field(ge=0)


class OffsideGoal(_Script):
    """A through ball, a goal given on the field, then a VAR offside check.

    margin_m > 0: the attacker is ahead of the line (offside); < 0: behind it (onside).
    """

    kind: Literal["offside_goal"]
    team: str
    passer: str
    attacker: str
    defender: str
    passer_pos: tuple[float, float]
    attacker_y: float
    defender_pos: tuple[float, float]
    margin_m: float = Field(ge=-1.5, le=1.5)
    decision: Literal["no_goal", "goal_stands"] = "no_goal"
    check_s: float = Field(default=35, gt=0)


class RedCard(_Script):
    kind: Literal["red_card"]
    player: str
    var: bool = True
    var_result: Literal["upheld", "overturned"] = "upheld"  # overturned: downgraded to a yellow
    check_s: float = Field(default=40, gt=0)


class PenaltyReview(_Script):
    """A challenge in the box, play waved on, then a VAR review for a possible penalty."""

    kind: Literal["penalty_review"]
    team: str  # attacking team
    attacker: str
    defender: str
    awarded: bool = True
    scored: bool = True
    check_s: float = Field(default=55, gt=0)


class Goal(_Script):
    kind: Literal["goal"]
    team: str


Script = Annotated[OffsideGoal | RedCard | Goal | PenaltyReview, Field(discriminator="kind")]


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    match_id: str = Field(pattern=r"^m\d{2}$")
    seed: int
    home: str
    away: str
    random_goals: bool = True
    tracking_hz: Literal[0, 1, 2, 5] = 2  # tracking feed rate; 0 turns it off
    script: list[Script] = []


def load_scenario(name: str) -> Scenario:
    return Scenario.model_validate_json((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))


def scenario_names() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))
