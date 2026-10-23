"""Contract 1: match event (simulator -> Event Hubs -> engine).

Every on-ball event carries the position of every player on the pitch (22, fewer after a red
card), so speeds are derived by the engine, never stated.
`frame` and `ball_track` appear only on events a check may measure: kicks, shots and challenges.
"""

from typing import Literal

from pydantic import Field, model_validator

from contracts.common import Clock, Contract, EventId, PlayerId, Point, TeamCode

EventType = Literal[
    "tracking",  # positions only, at a fixed rate; ball position in `start`
    "kickoff",
    "pass",
    "carry",
    "shot",
    "tackle",
    "interception",
    "pressure",
    "foul",
    "card",
    "goal",
    "var_check",
    "var_decision",
    "period_end",
]

# Playable body parts under Law 11 (arms and hands excluded), plus "arm" so the engine can ignore it.
BodyPart = Literal["head", "torso", "foot_l", "foot_r", "knee_l", "knee_r", "arm"]


BALL_TRACK_TYPES = frozenset({"pass", "shot", "tackle"})  # tackle: did the challenger reach the ball?
FRAME_TYPES = frozenset({"pass", "shot", "foul", "tackle"})


class BodyFrame(Contract):
    clock: Clock
    body: dict[PlayerId, dict[BodyPart, Point]]


class MatchEvent(Contract):
    event_id: EventId
    match_id: str = Field(pattern=r"^m\d{2}$")
    period: Literal[1, 2]
    clock: Clock
    type: EventType
    team: TeamCode | None = None
    player: PlayerId | None = None
    receiver: PlayerId | None = None
    outcome: str | None = None
    start: Point | None = None
    end: Point | None = None
    ball_speed_kmh: float | None = Field(default=None, ge=0, le=200)
    positions: dict[PlayerId, Point]
    # Evidence for checks that measure something: body parts at the key moment, the ball at 100 fps
    # around a kick or shot, and the point where a challenge made contact.
    frame: BodyFrame | None = None
    ball_track: list[tuple[Clock, float, float]] | None = None
    contact: Point | None = None

    @model_validator(mode="after")
    def _check(self) -> "MatchEvent":
        if not self.event_id.startswith(f"{self.match_id}-"):
            raise ValueError("event_id must be prefixed with match_id")
        if self.ball_track is not None and self.frame is None:
            raise ValueError("a ball_track needs a body frame at the same moment")
        if self.ball_track is not None and self.type not in BALL_TRACK_TYPES:
            raise ValueError("ball_track appears only on passes, shots and tackles")
        if self.frame is not None and self.type not in FRAME_TYPES:
            raise ValueError("frame appears only on passes, shots, fouls and tackles")
        if self.contact is not None and self.type not in ("foul", "tackle"):
            raise ValueError("contact appears only on fouls and tackles")
        if self.type not in ("period_end", "var_check", "var_decision") and not 14 <= len(self.positions) <= 22:
            raise ValueError("on-ball events must carry every on-pitch position (14 to 22)")
        return self
