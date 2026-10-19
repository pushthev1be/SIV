"""Offside measurement under Law 11, from a pass event's body frame and 100 fps ball track.

A player is in an offside position if any part of the head, body or feet is in the opponents' half
and nearer to their goal line than both the ball and the second-last opponent. Hands and arms do
not count, for attackers or defenders. The moment that matters is the first contact with the ball,
which we take from the ball track, not from the event's (coarser) timestamp.

This module measures; it never decides. The decision belongs to the officials.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from contracts import MatchEvent
from contracts.common import PITCH_LENGTH_M

PLAYABLE_PARTS = ("head", "torso", "knee_l", "knee_r", "foot_l", "foot_r")
KICK_EPS_M = 0.005  # ball displacement that counts as moving


@dataclass(frozen=True)
class OffsideMeasurement:
    kick_clock: float
    attacker: str
    defender: str  # second-last opponent, usually the deepest outfield defender
    attacker_x: float  # absolute x of the attacker's most forward playable part
    defender_x: float  # absolute x of the line: the second-last opponent's deepest playable part
    attacker_part: str  # which part set each line, e.g. foot_l; "centre" without a body frame
    defender_part: str
    margin_m: float  # positive: attacker ahead of the line (offside position), metres, to the cm
    ball_x: float
    offside_position: bool


def find_kick(ball_track: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Last sample before the ball starts to move: the moment of contact."""
    _, x0, y0 = ball_track[0]
    for i in range(1, len(ball_track)):
        _, x, y = ball_track[i]
        if math.hypot(x - x0, y - y0) > KICK_EPS_M:
            return ball_track[i - 1]
    raise ValueError("ball never moves in the track")


def measure(event: MatchEvent, attack_dir: int) -> OffsideMeasurement:
    """Measure the receiver of `event` against the defending team. attack_dir: +1 toward x = 105."""
    if event.frame is None or event.ball_track is None or event.receiver is None:
        raise ValueError("offside needs a pass with a receiver, a body frame and a ball track")
    kick_clock, ball_x, _ = find_kick(event.ball_track)
    attacking = event.receiver.split("-")[0]

    def front(pid: str) -> tuple[float, str]:
        """Most forward playable point along the attacking direction: (absolute x, part)."""
        body = event.frame.body.get(pid)
        if body:
            parts = [(body[p][0], p) for p in PLAYABLE_PARTS if p in body]
            if parts:
                return max(parts, key=lambda t: attack_dir * t[0])
        return event.positions[pid][0], "centre"  # no frame for this player: the tracked centre

    attacker_x, attacker_part = front(event.receiver)
    defenders = sorted(
        ((*front(pid), pid) for pid in event.positions if not pid.startswith(attacking)),
        key=lambda t: attack_dir * t[0],
        reverse=True,
    )
    defender_x, defender_part, defender = defenders[1]
    margin = attack_dir * (attacker_x - defender_x)
    halfway = PITCH_LENGTH_M / 2
    in_opp_half = attack_dir * (attacker_x - halfway) > 0
    ahead_of_ball = attack_dir * (attacker_x - ball_x) > 0
    return OffsideMeasurement(
        kick_clock=kick_clock,
        attacker=event.receiver,
        defender=defender,
        attacker_x=round(attacker_x, 3),
        defender_x=round(defender_x, 3),
        attacker_part=attacker_part,
        defender_part=defender_part,
        margin_m=round(margin, 2),
        ball_x=round(ball_x, 3),
        offside_position=margin > 0 and in_opp_half and ahead_of_ball,
    )
