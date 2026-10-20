"""Call replays: the build-up to every call, with the players involved, their speeds and, once the
officials have decided, the measurement.

A `during` package is built when a check opens, so screens have something true to show instead of
dead air; it carries no lines, margins or verdicts. The `after` package is built at the decision.
Status text uses player IDs (NBR-9); renderers swap in names.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from contracts import Incident, MatchEvent
from contracts.replay import (
    BallSample,
    OffsideLines,
    ReplayFocus,
    ReplayFrame,
    ReplayMoment,
    ReplayPackage,
    SlowWindow,
)
from engine.laws import RULE_SENTENCE
from engine.stats import distance_to_goal

SPEED_HALF_WINDOW_S = 0.5
BALL_TRACK_FPS = 100  # the sampling rate the kick point is found from
FINAL_TEXT = {
    "no_goal": "no goal", "goal": "goal", "red_card": "red card", "yellow": "yellow card",
    "penalty": "penalty", "no_penalty": "no penalty",
}  # fmt: skip


PART_TEXT = {
    "foot_l": "left foot", "foot_r": "right foot", "knee_l": "left knee", "knee_r": "right knee",
    "head": "head", "torso": "body", "centre": "body", "arm": "arm",
}  # fmt: skip


def margin_text(m: float) -> str:
    """0.31 -> '31 cm (12.2 in)'; 4.81 -> '4.8 m (15.8 ft)'."""
    m = abs(m)
    if m < 1.0:
        return f"{m * 100:.0f} cm ({m / 0.0254:.1f} in)"
    return f"{m:.1f} m ({m / 0.3048:.1f} ft)"


def clock_text(period: int, clock: float) -> str:
    base = 2700 if period == 1 else 5400
    if clock >= base:
        return f"{base // 60}+{int((clock - base) // 60) + 1}'"
    return f"{int(clock // 60)}:{int(clock % 60):02d}"


def _find(history: Sequence[MatchEvent], event_id: str | None) -> MatchEvent | None:
    return next((e for e in reversed(history) if e.event_id == event_id), None) if event_id else None


def _speeds(frames: list[ReplayFrame], pid: str) -> list[float | None]:
    """km/h at each frame from positions about half a second either side."""
    out: list[float | None] = []
    ts = [f.t for f in frames]
    for i, f in enumerate(frames):
        if pid not in f.positions:
            out.append(None)
            continue
        j = i
        while j > 0 and ts[i] - ts[j] < SPEED_HALF_WINDOW_S and pid in frames[j - 1].positions:
            j -= 1
        k = i
        while k < len(frames) - 1 and ts[k] - ts[i] < SPEED_HALF_WINDOW_S and pid in frames[k + 1].positions:
            k += 1
        dt = ts[k] - ts[j]
        if dt < 0.8:
            out.append(None)
            continue
        d = math.dist(frames[j].positions[pid], frames[k].positions[pid])
        out.append(round(d / dt * 3.6, 1))
    return out


def _window(history, period, t0, t1, extra_ball=()) -> tuple[list[ReplayFrame], list[BallSample]]:
    frames: list[ReplayFrame] = []
    ball: list[tuple[float, float, float]] = list(extra_ball)
    for e in history:
        if e.period != period or not (t0 <= e.clock <= t1):
            continue
        if e.positions:
            t = e.frame.clock if e.frame is not None else e.clock
            frames.append(ReplayFrame(t=t, positions=e.positions))
        if e.start is not None and e.type in ("tracking", "pass", "carry", "shot", "goal", "kickoff"):
            ball.append((e.frame.clock if e.frame is not None else e.clock, *e.start))
    frames.sort(key=lambda f: f.t)
    dedup: list[ReplayFrame] = []
    for f in frames:
        if not dedup or f.t - dedup[-1].t >= 0.05:
            dedup.append(f)
    ball.sort()
    samples: list[BallSample] = []
    for t, x, y in ball:
        if not samples or t - samples[-1].t >= 0.009:
            samples.append(BallSample(t=round(t, 2), x=x, y=y))
    return dedup, samples


def build_replay(
    history: Sequence[MatchEvent], inc: Incident, phase: str, direction: dict[str, int]
) -> ReplayPackage | None:
    if inc.kind == "offside" and "kick_event" in inc.facts:
        return _offside(history, inc, phase, direction)
    if inc.kind == "goal_check":
        return _goal(history, inc, phase, direction)
    if inc.kind in ("red_card", "foul", "penalty") and "foul_event" in inc.facts:
        return _challenge(history, inc, phase)
    return None


def _package(inc, phase, period, key_t, frames, ball, moments, focus, status, sources, lines=None, facts=None):
    if len(frames) < 2 or len(ball) < 2:
        return None
    t_first, t_last = frames[0].t, frames[-1].t
    key_t = min(max(key_t, t_first), t_last)
    s0, s1 = max(t_first, key_t - 1.5), min(t_last, key_t + 1.0)
    return ReplayPackage(
        incident_id=inc.incident_id,
        kind=inc.kind,
        phase=phase,
        period=period,
        key_t=key_t,
        frames=frames,
        ball=ball,
        moments=[m for m in moments if m.reveal in ("always", phase)],
        slow=SlowWindow(start=s0, end=s1, rate=0.25) if s1 > s0 else None,
        focus=focus,
        speeds={f.player: _speeds(frames, f.player) for f in focus},
        lines=lines if phase == "after" else None,
        status=status,
        facts=facts or {},
        sources=list(dict.fromkeys(sources)),
    )


def _offside(history, inc: Incident, phase, direction) -> ReplayPackage | None:
    f = inc.facts  # engine-internal: used for the window always, shown only after the decision
    kick = _find(history, f["kick_event"])
    if kick is None or kick.frame is None:
        return None
    goal = _find(history, inc.goal_event)
    t0, t1 = kick.clock - 8.0, (goal.clock + 1.5) if goal else kick.clock + 4.0
    track = [(t, x, y) for t, x, y in kick.ball_track or []]
    frames, ball = _window(history, kick.period, t0, t1, track)
    att, deff, passer = f["attacker"], f["defender"], kick.player
    focus = [ReplayFocus(player=att, role="attacker"), ReplayFocus(player=deff, role="defender")]
    if passer:
        focus.append(ReplayFocus(player=passer, role="passer"))
    reviewed = inc.var_outcome is not None or inc.state == "reviewing"
    kick_t = float(f["kick_clock"])
    margin = float(f["margin_m"])
    moments = [
        ReplayMoment(t=kick_t, label=f"The pass from {passer}: the moment that matters", reveal="always"),
        ReplayMoment(t=kick_t, label="Measuring against the second-last defender", reveal="during"),
        ReplayMoment(
            t=kick_t,
            label=f"{att} {margin_text(margin)} {'ahead of' if margin > 0 else 'behind'} {deff}",
            reveal="after",
        ),  # fmt: skip
    ]
    if phase == "during":
        status = {
            "stadium": "Check in progress: possible offside",
            "fan": (
                f"Checking for offside. {RULE_SENTENCE['offside']}. The officials are finding the exact frame "
                f"where {passer} played the pass, to see whether {att} was beyond the second-last defender."
            ),
            "analyst": (f"Measuring {att}'s most forward playable part against the second-last defender at the pass."),
            "officials": f"Review open: offside. On-field call: {inc.on_field}.",
        }
    else:
        final = FINAL_TEXT.get(inc.final or "", inc.final or "")
        check = f" The check took {f['check_s']:.0f} s." if "check_s" in f else ""
        stands = inc.final == "goal"
        a_part = PART_TEXT.get(f["attacker_part"], f["attacker_part"])
        d_part = PART_TEXT.get(f["defender_part"], f["defender_part"])
        side = "beyond" if margin > 0 else "behind"
        evidence = (
            f"At the moment {passer} played it, {att}'s {a_part} was {margin_text(margin)} {side} {deff}'s {d_part}"
        )
        agrees = (margin > 0) != stands  # the measurement and the decision point the same way
        if stands:
            stadium = "Check complete: goal stands, onside"
            why = ": level or behind is onside, so the goal stands." if agrees else "."
            fan = f"Why the goal stands. {RULE_SENTENCE['offside']}. {evidence}{why}{check}"
        else:
            stadium = f"{'Check complete: ' if reviewed else ''}{final.capitalize()}, offside"
            ruled_out = (
                ", so the goal is ruled out for offside." if goal else ", so it's offside: a free kick to the defence."
            )
            why = ruled_out if agrees else "."
            fan = f"Why it's offside. {RULE_SENTENCE['offside']}. {evidence}{why}{check}"
        if not agrees:  # never defend a call the measurement does not support
            fan += " The measurement alone does not explain this decision; the officials' notes will say why."
        status = {
            "stadium": stadium,
            "fan": fan,
            "analyst": (
                f"Margin {margin:.2f} m: {att} {f['attacker_part']} vs {deff} {f['defender_part']}. "
                f"Kick at {clock_text(kick.period, kick_t)} from the {BALL_TRACK_FPS} fps ball track.{check}"
            ),
            "officials": (
                f"Factual check under Law 11. Kick frame {clock_text(kick.period, kick_t)} from the "
                f"{BALL_TRACK_FPS} fps ball track: {att} {a_part} {abs(margin):.2f} m {side} the "
                f"second-last defender {deff} ({d_part}). The evidence supports "
                f"{'offside, no goal' if margin > 0 else 'onside, goal stands'}. Final decision: {final}."
            ),
        }
    lines = OffsideLines(
        attack_dir=direction.get(kick.team, 1),
        attacker=att,
        defender=deff,
        attacker_x=f["attacker_x"],
        defender_x=f["defender_x"],
        attacker_part=f["attacker_part"],
        defender_part=f["defender_part"],
        margin_m=margin,
        body={pid: parts for pid, parts in kick.frame.body.items() if pid in (att, deff)},
    )
    sources = [kick.event_id] + ([goal.event_id] if goal else []) + [inc.incident_id]
    facts = {} if phase == "during" else {k: f[k] for k in ("margin_m", "check_s") if k in f}
    if phase == "after":
        facts["ball_track_fps"] = BALL_TRACK_FPS
    return _package(inc, phase, kick.period, kick_t, frames, ball, moments, focus, status, sources, lines, facts)


def _goal(history, inc: Incident, phase, direction) -> ReplayPackage | None:
    goal = _find(history, inc.goal_event)
    if goal is None:
        return None
    before = [e for e in history if e.period == goal.period and goal.clock - 15 <= e.clock <= goal.clock]
    shot = next((e for e in reversed(before) if e.type == "shot" and e.player == goal.player), None)
    key = shot or goal
    passes_in = [e for e in before if e.type == "pass" and e.outcome == "complete" and e.receiver == goal.player]
    assist = next((e for e in reversed(passes_in) if e.clock <= key.clock), None)
    frames, ball = _window(history, goal.period, goal.clock - 12.0, goal.clock + 2.0)
    focus = [ReplayFocus(player=goal.player, role="scorer")]
    if assist and assist.player:
        focus.append(ReplayFocus(player=assist.player, role="assist"))
    detail = ""
    gfacts: dict = {}
    if assist is not None and assist.ball_speed_kmh is not None:
        gfacts["assist_pass_speed_kmh"] = assist.ball_speed_kmh
    if shot is not None:
        dist = distance_to_goal(shot.start, direction.get(shot.team, 1))
        detail = f" from {dist:.0f} m at {shot.ball_speed_kmh:.0f} km/h"
        gfacts |= {"shot_distance_m": round(dist, 1), "shot_speed_kmh": shot.ball_speed_kmh}
    moments = [ReplayMoment(t=key.clock, label=f"Shot by {goal.player}{detail}"[:80], reveal="always")]
    if assist:
        moments.insert(0, ReplayMoment(t=assist.clock, label=f"Pass from {assist.player}", reveal="always"))
    final = inc.final or "goal"
    status = {
        "stadium": f"Goal: {goal.player}" if final == "goal" else "Check in progress",
        "fan": f"{goal.player} scores{detail}." + (f" Set up by {assist.player}." if assist else ""),
        "analyst": f"Shot{detail}."
        + (f" Assist {assist.player}, {assist.ball_speed_kmh:.0f} km/h pass." if assist else ""),
        "officials": f"Goal event {goal.event_id}. On-field: goal. VAR: {inc.var_outcome or 'no review'}.",
    }
    if phase == "during":
        status = {k: "Check in progress: the goal is being reviewed" for k in status}
    sources = [goal.event_id] + ([shot.event_id] if shot else []) + ([assist.event_id] if assist else [])
    return _package(
        inc, phase, goal.period, key.clock, frames, ball, moments, focus, status, sources + [inc.incident_id],
        facts=gfacts if phase == "after" else {},
    )  # fmt: skip


def _challenge(history, inc: Incident, phase) -> ReplayPackage | None:
    f = inc.facts
    foul = _find(history, f["foul_event"])
    if foul is None:
        return None
    frames, ball = _window(history, foul.period, foul.clock - 8.0, foul.clock + 3.0)
    offender, fouled = f["offender"], f.get("fouled") or None
    focus = [ReplayFocus(player=offender, role="offender")]
    if fouled:
        focus.append(ReplayFocus(player=fouled, role="fouled"))
    pkg_probe = {ff.player: _speeds(frames, ff.player) for ff in focus}
    i = min(range(len(frames)), key=lambda i: abs(frames[i].t - foul.clock)) if frames else 0
    closing = pkg_probe[offender][i] if frames else None
    closing_text = f", arriving at {closing:.0f} km/h" if closing else ""
    red = inc.kind == "red_card"
    penalty = inc.kind == "penalty"
    where = f" {f['distance_to_goal_m']:.0f} m from goal" if "distance_to_goal_m" in f and phase == "after" else ""
    moments = [ReplayMoment(t=foul.clock, label=f"The challenge by {offender}{closing_text}"[:80], reveal="always")]
    rule = RULE_SENTENCE["penalty" if penalty else "red_card"]
    arriving = f" at {closing:.0f} km/h" if closing else ""
    if phase == "during":
        status = {
            "stadium": f"Check in progress: possible {'penalty' if penalty else 'red card'}",
            "fan": (
                f"Checking a possible penalty. {rule}. The officials are looking at {offender}'s challenge "
                f"on {fouled}; whether it was a foul is the referee's judgement."
                if penalty
                else f"Checking a possible red card. {rule}. The officials are reviewing {offender}'s "
                f"challenge on {fouled}."
            ),
            "analyst": f"Challenge by {offender}{closing_text}. Review open.",
            "officials": f"Review open: {inc.kind.replace('_', ' ')}. On-field call: {inc.on_field}.",
        }
    else:
        final = FINAL_TEXT.get(inc.final or "", inc.final or "")
        if penalty:
            given = inc.final == "penalty"
            outcome = (
                "referee judged the contact a foul, so it's a penalty."
                if given
                else "referee judged there was not enough for a foul, so play goes on."
            )
            fan = (
                f"Why {'a penalty' if given else 'no penalty'}. {RULE_SENTENCE['penalty']}. {offender} went "
                f"into the challenge on {fouled}{arriving} inside the area{',' if where else ''}{where}; "
                f"on review the {outcome}"
            )
        elif red and inc.var_outcome == "overturned":
            fan = (
                f"Why it's now a yellow. {RULE_SENTENCE['red_card']}. {offender} arrived{arriving} on "
                f"{fouled}; on review the referee judged it reckless rather than dangerous, so the red card "
                f"is downgraded to a yellow."
            )
        elif red:
            fan = (
                f"Why a red card. {RULE_SENTENCE['red_card']}. {offender} arrived{arriving} on {fouled}; "
                f"the referee judged the challenge endangered the opponent's safety, so {offender} is sent off."
            )
        else:
            fan = (
                f"Why a yellow card. {RULE_SENTENCE['foul']}. {offender}'s challenge on {fouled}{arriving} "
                f"was judged reckless."
            )
        status = {
            "stadium": f"{final.capitalize()}: {offender}" if not penalty else f"Check complete: {final}",
            "fan": fan,
            "analyst": f"Challenge by {offender}{closing_text}.",
            "officials": (
                f"Judgement check. Factors: {offender}{' arriving' + arriving if arriving else ''} on "
                f"{fouled}{where}. On-field: {inc.on_field.replace('_', ' ')}. Final after review: {final}. "
                f"This is the referee's call; the facts inform it, they do not make it."
            ),
        }  # fmt: skip
    sources = [foul.event_id, inc.incident_id]
    cfacts = {"closing_speed_kmh": closing} if closing else {}
    if phase == "after" and "distance_to_goal_m" in f:
        cfacts["distance_to_goal_m"] = f["distance_to_goal_m"]
    return _package(inc, phase, foul.period, foul.clock, frames, ball, moments, focus, status, sources, facts=cfacts)
