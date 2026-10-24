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
    Measurement,
    OffsideLines,
    ReplayFocus,
    ReplayFrame,
    ReplayMoment,
    ReplayPackage,
    SlowWindow,
)
from engine.laws import LAWS, RULE_SENTENCE
from engine.measure import BALL_RADIUS_M
from engine.stats import distance_to_goal

SPEED_HALF_WINDOW_S = 0.5
BALL_TRACK_FPS = 100  # the sampling rate the kick point is found from
MIN_CLOSING_KMH = 5.0  # below this a challenger's "arriving at" speed is noise, not a run-in
FINAL_TEXT = {
    "no_goal": "no goal", "goal": "goal", "red_card": "red card", "yellow": "yellow card",
    "penalty": "penalty", "no_penalty": "no penalty", "free_kick": "free kick", "retake": "retake",
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
    history: Sequence[MatchEvent], inc: Incident, phase: str, direction: dict[str, int], measured=None
) -> ReplayPackage | None:
    if inc.kind in MEASURED_KINDS:
        return _measured(history, inc, phase, direction, measured)
    if inc.kind == "offside" and "kick_event" in inc.facts:
        return _offside(history, inc, phase, direction)
    if inc.kind == "goal_check":
        return _goal(history, inc, phase, direction)
    if inc.kind in ("red_card", "foul", "penalty") and "foul_event" in inc.facts:
        return _challenge(history, inc, phase, measured)
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
            head = "Why the goal stands." if agrees else "The goal stands, but the measurement says offside."
            fan = f"{head} {RULE_SENTENCE['offside']}. {evidence}{why}{check}"
        else:
            stadium = f"{'Check complete: ' if reviewed else ''}{final.capitalize()}, offside"
            ruled_out = (
                ", so the goal is ruled out for offside." if goal else ", so it's offside: a free kick to the defence."
            )
            why = ruled_out if agrees else "."
            head = "Why it's offside." if agrees else "Ruled offside, but the measurement says onside."
            fan = f"{head} {RULE_SENTENCE['offside']}. {evidence}{why}{check}"
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
    if inc.facts.get("penalty_kick"):
        gfacts["penalty_kick"] = True
    pen = bool(inc.facts.get("penalty_kick"))
    what = ("the retaken penalty" if inc.facts.get("retake") else "the penalty") if pen else ""
    if pen:  # a penalty has no assist and no open play: say what it is
        assist, detail = None, ""
    shot_label = f"Penalty kick by {goal.player}" if pen else f"Shot by {goal.player}{detail}"
    moments = [ReplayMoment(t=key.clock, label=shot_label[:80], reveal="always")]
    if assist:
        moments.insert(0, ReplayMoment(t=assist.clock, label=f"Pass from {assist.player}", reveal="always"))
    final = inc.final or "goal"
    fan = (
        f"{goal.player} scores {what}. Everyone but the taker and the keeper waits outside the area until it's "
        f"struck, so there's no build-up and nobody can challenge him."
        if pen
        else f"{goal.player} scores{detail}." + (f" Set up by {assist.player}." if assist else "")
    )
    status = {
        "stadium": (f"Goal: {goal.player} ({what})" if pen else f"Goal: {goal.player}")
        if final == "goal"
        else "Check in progress",
        "fan": fan,
        "analyst": f"Penalty kick, {what}."
        if pen
        else f"Shot{detail}." + (f" Assist {assist.player}, {assist.ball_speed_kmh:.0f} km/h pass." if assist else ""),
        "officials": f"Goal event {goal.event_id}. On-field: goal. VAR: {inc.var_outcome or 'no review'}.",
    }
    if phase == "during":
        status = {k: "Check in progress: the goal is being reviewed" for k in status}
    sources = [goal.event_id] + ([shot.event_id] if shot else []) + ([assist.event_id] if assist else [])
    return _package(
        inc, phase, goal.period, key.clock, frames, ball, moments, focus, status, sources + [inc.incident_id],
        facts=gfacts if phase == "after" else {},
    )  # fmt: skip


def _ball_contact_text(m, offender: str, fouled: str, given: bool) -> tuple[str, str, bool]:
    """(evidence sentence, fan headline, agrees) for a penalty review that measured the ball contact.
    Missing the ball and hitting the attacker inside the area points to a penalty; a touch on the ball
    first does not by itself rule a foul out, so only the first case can disagree with the decision."""
    fx = m.factors
    in_area = fx.get("contact_in_area", True)
    if fx["touched_ball"]:
        evidence = f"{offender} reached the ball before the contact on {fouled}"
        return evidence, f"Why {'a penalty' if given else 'no penalty'}.", True
    evidence = (
        f"{offender}'s nearest foot was still {margin_text(m.value_m)} from the ball at the moment of contact with "
        f"{fouled}{' inside the area' if in_area else ''}: the ball was never played"
    )
    agrees = given or not in_area
    head = f"Why {'a penalty' if given else 'no penalty'}." if agrees else "No penalty, but the measurement says foul."
    return evidence, head, agrees


def _challenge(history, inc: Incident, phase, m=None) -> ReplayPackage | None:
    f = inc.facts
    foul = _find(history, f["foul_event"])
    if foul is None:
        return None
    frames, ball = _window(history, foul.period, foul.clock - 8.0, foul.clock + 3.0, foul.ball_track or ())
    offender, fouled = f["offender"], f.get("fouled") or None
    focus = [ReplayFocus(player=offender, role="offender")]
    if fouled:
        focus.append(ReplayFocus(player=fouled, role="fouled"))
    pkg_probe = {ff.player: _speeds(frames, ff.player) for ff in focus}
    i = min(range(len(frames)), key=lambda i: abs(frames[i].t - foul.clock)) if frames else 0
    closing = pkg_probe[offender][i] if frames else None
    closing = closing if closing and closing >= MIN_CLOSING_KMH else None  # a standing lunge has no run-in speed
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
            if m is not None and m.kind == "ball_contact":
                evidence, head, agrees = _ball_contact_text(m, offender, fouled, given)
                came = f", arriving{arriving}" if arriving else ""
                fan = f"{head} {RULE_SENTENCE['penalty']}. {evidence}{came}. On review the {outcome}"
                if not agrees:  # never defend a call the measurement does not support
                    fan += " The measurement alone does not explain this decision; the officials' notes will say why."
        elif red and inc.var_outcome == "overturned":
            fan = (
                f"Why it's now a yellow. {RULE_SENTENCE['red_card']}. {offender} arrived{arriving} on "
                f"{fouled}; on review the referee judged it reckless rather than dangerous, so the red card "
                f"is downgraded to a yellow."
            )
        elif red and inc.var_outcome == "upgraded":
            fan = (
                f"Why it's now a red. {RULE_SENTENCE['red_card']}. {offender} arrived{arriving} on {fouled}{where}. "
                f"The referee showed a yellow; VAR recommended a review, and at the monitor the referee judged the "
                f"challenge endangered the opponent's safety, so the yellow becomes a red and {offender} is sent off."
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
    measurement = None
    if phase == "after" and m is not None and m.kind == "ball_contact":
        touched = m.factors["touched_ball"]
        gap = "on the ball" if touched else f"{margin_text(m.value_m)} from the ball"
        status["analyst"] += f" Nearest foot {gap} at contact."
        status["officials"] = (
            f"Measured: at the contact frame {clock_text(foul.period, m.at)}, {offender}'s nearest foot was {gap}"
            f"{'' if touched else '; the ball track shows no deflection'}. " + status["officials"]
        )
        cfacts["ball_gap_m"] = m.factors["ball_gap_m"]
        measurement = Measurement(
            kind=m.kind, value_m=m.value_m, line=m.line, line_label=m.line_label, point=m.point,
            point_label=m.point_label, subject=m.subject, focus=m.focus(), factors=m.factors,
            sources=[m.evidence_event, inc.incident_id],
        )  # fmt: skip
        moments.append(ReplayMoment(t=m.at, label=f"Nearest foot {gap}"[:80], reveal="after"))
    pkg = _package(inc, phase, foul.period, foul.clock, frames, ball, moments, focus, status, sources, facts=cfacts)
    return pkg.model_copy(update={"measurement": measurement}) if pkg and measurement else pkg


# ── Measured checks beyond offside ───────────────────────────────────────────

MEASURED_KINDS = {"ball_out", "goal_line", "penalty_location", "keeper_encroachment", "dogso"}
KEY_LABEL = {
    "ball_out": "The cross from {subject}: the ball's furthest point",
    "goal_line": "The shot from {subject}, and the clearance",
    "penalty_location": "Where the contact started",
    "keeper_encroachment": "The moment the penalty is struck",
    "dogso": "The foul on the breakaway",
}


def _measured(history, inc: Incident, phase: str, direction: dict[str, int], m) -> ReplayPackage | None:
    f = inc.facts
    ev = _find(history, f.get("evidence_event"))
    if ev is None or m is None:
        return None
    goal = _find(history, inc.goal_event)
    t1 = max(ev.clock + 3.0, (goal.clock + 1.5) if goal else 0.0)
    track = [(t, x, y) for t, x, y in ev.ball_track or []]
    frames, ball = _window(history, ev.period, ev.clock - 8.0, t1, track)
    kind = inc.kind
    roles: list[tuple[str | None, str]] = {
        "ball_out": [(ev.player, "crosser"), (goal.player if goal else None, "scorer")],
        "goal_line": [(ev.player, "shooter")]
        + [(pid, "clearer") for pid in (ev.frame.body if ev.frame else {}) if not pid.startswith(ev.team or "")][:1],
        "penalty_location": [(ev.player, "offender"), (ev.receiver, "fouled")],
        "keeper_encroachment": [(m.subject, "keeper"), (ev.player, "taker")],
        "dogso": [(ev.player, "offender"), (ev.receiver, "fouled")],
    }[kind]
    focus = [ReplayFocus(player=p, role=r) for p, r in roles if p]
    mtext = margin_text(m.value_m)
    side = {
        "ball_out": "wholly over the line" if m.value_m > 0 else "still touching the line",
        "goal_line": "wholly over the line" if m.value_m > 0 else "short of wholly over",
        "penalty_location": "outside the area" if m.value_m > 0 else "inside the area",
        "keeper_encroachment": "off the line" if m.value_m > 0 else "on the line",
        "dogso": "from goal",
    }[kind]
    subj = m.subject or ""
    moments = [ReplayMoment(t=m.at, label=KEY_LABEL[kind].format(subject=ev.player)[:80], reveal="always")]
    res = f.get("data_resolution_m")
    if res is not None and abs(m.value_m) <= res + BALL_RADIUS_M:
        moments.append(ReplayMoment(t=m.at, label=f"On the {m.line_label}, within the data's {margin_text(res)}"[:80],
                                    reveal="after"))  # fmt: skip
    elif kind != "dogso":
        moments.append(ReplayMoment(t=m.at, label=f"{mtext} {side}"[:80], reveal="after"))
    rule = RULE_SENTENCE[kind]
    check = f" The check took {f['check_s']:.0f} s." if "check_s" in f else ""
    final = FINAL_TEXT.get(inc.final or "", (inc.final or "").replace("_", " "))

    if phase == "during":
        what = {
            "ball_out": f"whether the ball went out of play before {ev.player}'s cross",
            "goal_line": f"whether the whole of {ev.player}'s shot crossed the goal line",
            "penalty_location": f"where {ev.player}'s challenge on {ev.receiver} started",
            "keeper_encroachment": f"where {subj}'s feet were when {ev.player} struck the penalty",
            "dogso": f"whether {ev.player}'s foul on {ev.receiver} denied an obvious goal-scoring opportunity",
        }[kind]
        status = {
            "stadium": f"Check in progress: {kind.replace('_', ' ')}",
            "fan": f"Checking {what}. {rule}.",
            "analyst": f"Checking {what}. Evidence: the frame at {clock_text(ev.period, m.at)}.",
            "officials": f"Review open: {kind.replace('_', ' ')}. On-field call: {inc.on_field.replace('_', ' ')}.",
        }
        facts: dict = {}
    else:
        clearer = next((p for p, r in roles if r == "clearer" and p), "the defender")
        evidence = {
            "ball_out": f"Before {ev.player} crossed it, the ball was {mtext} {side}",
            "goal_line": f"When {clearer} cleared it, the ball was {mtext} {side}",
            "penalty_location": f"{ev.player}'s contact on {ev.receiver} started {mtext} {side}",
            "keeper_encroachment": f"As {ev.player} struck it, {subj}'s nearest foot was {mtext} {side}",
            "dogso": "",
        }[kind]
        outcome = {
            ("ball_out", "no_goal"): "so play had already stopped and the goal is ruled out.",
            ("ball_out", "goal"): "so the ball was still in play and the goal is given.",
            ("goal_line", "no_goal"): "so it is not a goal.",
            ("goal_line", "goal"): "so it is a goal.",
            ("penalty_location", "free_kick"): "so it is a free kick, not a penalty.",
            ("penalty_location", "penalty"): "so the penalty stands.",
            ("keeper_encroachment", "retake"): "and the save came from that, so the penalty is retaken.",
        }.get((kind, inc.final or ""), f"so the decision is {final}.")
        if kind == "dogso":
            fx = f
            between = fx.get("defenders_between", 0)
            direction_txt = (
                f", heading {fx['direction_off_goal_deg']} degrees off the line to goal"
                if "direction_off_goal_deg" in fx else ""
            )  # fmt: skip
            control = "with the ball under control" if fx.get("in_control") else "without clear control"
            fan = (
                f"Why a red card. {rule}. {ev.receiver} was {fx['distance_to_goal_m']:.0f} m from goal{direction_txt}, "
                f"{control}, with {between} outfield defender{'s' if between != 1 else ''} able to get back; the "
                f"referee judged it an obvious chance denied, so the yellow card becomes a red.{check}"
            )
            officials = (
                f"Judgement check under Law 12. Factors: distance {fx['distance_to_goal_m']:.0f} m; "
                f"defenders between {between}; {control}{direction_txt}. Final after review: {final}. "
                f"This is the referee's call; the facts inform it, they do not make it."
            )
            analyst = f"Distance {fx['distance_to_goal_m']:.1f} m, defenders between {between}{direction_txt}."
            facts = {k: fx[k] for k in ("distance_to_goal_m", "defenders_between", "direction_off_goal_deg") if k in fx}
        else:
            why = {
                "ball_out": "no goal" if inc.final == "no_goal" else "it's a goal",
                "goal_line": "no goal" if inc.final == "no_goal" else "it's a goal",
                "penalty_location": "a free kick"
                if inc.final == "free_kick" else "the penalty", "keeper_encroachment": "a retake",
            }[kind]  # fmt: skip
            fan = f"Why {why}. {rule}. {evidence}, {outcome}{check}"
            officials = (
                f"Factual check under {LAWS[kind]['law']}. Evidence at {clock_text(ev.period, m.at)}: "
                f"{m.point_label} {abs(m.value_m):.2f} m {side} ({m.line_label}). Final decision: {final}."
            )
            analyst = f"{m.point_label.capitalize()}: {abs(m.value_m):.2f} m {side} the {m.line_label}.{check}"
            facts = {"value_m": abs(m.value_m)}
            res = f.get("data_resolution_m")
            if res is not None and abs(m.value_m) <= res + BALL_RADIUS_M:  # too close to call from this data
                where = {"ball_out": f"when {ev.player} played it", "goal_line": f"when {clearer} cleared it"}.get(
                    kind, "at the key moment"
                )
                fan = (
                    f"Why {why}. {rule}. The event data puts the ball on the {m.line_label} {where}, but it "
                    f"places the ball only to about {margin_text(res)}, too coarse for a call decided by "
                    f"millimetres. The officials decided it from the goal-line camera images, {outcome}"
                )
                officials = (
                    f"Under {LAWS[kind]['law']}. Event data at {clock_text(ev.period, m.at)} puts the ball on the "
                    f"{m.line_label}, within its {margin_text(res)} resolution: it cannot settle this. Decide from "
                    f"the goal-line camera images. Final decision: {final}."
                )
                analyst = f"Ball on the {m.line_label} in event data (resolution {margin_text(res)}).{check}"
                facts = {"data_resolution_m": res}
        if "check_s" in f:
            facts["check_s"] = f["check_s"]
        status = {
            "stadium": f"{'Check complete: ' if inc.var_outcome else ''}{final}",
            "fan": fan,
            "analyst": analyst,
            "officials": officials,
        }
    measurement = None
    if phase == "after":
        measurement = Measurement(
            kind=m.kind, value_m=m.value_m, line=m.line, line_label=m.line_label, point=m.point,
            point_label=m.point_label, subject=m.subject, focus=m.focus(), factors=m.factors,
            sources=[m.evidence_event, inc.incident_id],
        )  # fmt: skip
    sources = [ev.event_id] + ([goal.event_id] if goal else []) + [inc.incident_id]
    pkg = _package(inc, phase, ev.period, m.at, frames, ball, moments, focus, status, sources, facts=facts)
    return pkg.model_copy(update={"measurement": measurement}) if pkg else None
