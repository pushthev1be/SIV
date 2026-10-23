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
    played_ball: bool = False  # the defender got a touch on the ball before going through the attacker
    check_s: float = Field(default=55, gt=0)


class Goal(_Script):
    kind: Literal["goal"]
    team: str


class BallOutGoal(_Script):
    """The ball runs over the byline, is crossed back anyway, and the goal goes in: VAR rules it out."""

    kind: Literal["ball_out_goal"]
    team: str
    crosser: str
    scorer: str
    over_m: float = Field(default=0.04, gt=0, le=0.5)  # how far the whole ball is over the line
    check_s: float = Field(default=40, gt=0)


class BoxEdgeFoul(_Script):
    """A foul at the edge of the area, given as a penalty; VAR checks where contact started."""

    kind: Literal["box_edge_foul"]
    team: str  # attacking team
    attacker: str
    defender: str
    outside_m: float = Field(default=0.14, ge=-1.0, le=1.0)  # > 0 outside the area, < 0 inside
    check_s: float = Field(default=50, gt=0)


class GoalLineClearance(_Script):
    """A shot cleared off the line; goal-line evidence decides whether the whole ball was over."""

    kind: Literal["goal_line_clearance"]
    team: str
    shooter: str
    clearer: str
    over_m: float = Field(default=-0.03, ge=-0.5, le=0.5)  # < 0 short of wholly over
    check_s: float = Field(default=3, gt=0)


class PenaltyRetake(_Script):
    """A penalty saved with the keeper off the line; VAR orders a retake."""

    kind: Literal["penalty_retake"]
    team: str  # the taker's team
    taker: str
    fouler: str
    off_m: float = Field(default=0.38, gt=0, le=2.0)  # keeper's nearest foot off the line at the kick
    check_s: float = Field(default=35, gt=0)


class DogsoFoul(_Script):
    """A last-man foul on a breakaway, booked on the field; VAR reviews it as DOGSO."""

    kind: Literal["dogso_foul"]
    team: str  # attacking team
    attacker: str
    defender: str
    check_s: float = Field(default=45, gt=0)


Script = Annotated[
    OffsideGoal | RedCard | Goal | PenaltyReview | BallOutGoal | BoxEdgeFoul | GoalLineClearance | PenaltyRetake
    | DogsoFoul,
    Field(discriminator="kind"),
]  # fmt: skip


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
