import functools

import pytest

from contracts import MatchEvent
from engine.decisions import DecisionDesk, IllegalTransition
from engine.engine import Engine
from engine.offside import find_kick, measure
from engine.stats import Movement, Rolling, pass_quality
from sim.scenario import load_scenario
from sim.simulator import Simulator


@functools.cache
def facts_for(name: str):
    events = Simulator(load_scenario(name)).run()
    engine = Engine(events[0].match_id)
    facts = [f for e in events for f in engine.ingest(e)]
    return engine, facts


def positions(**override) -> dict[str, list[float]]:
    p = {f"NBR-{n}": [40.0 + n, 10.0 + n * 4] for n in range(1, 12)}
    p |= {f"ASH-{n}": [60.0 + n, 10.0 + n * 4] for n in range(1, 12)}
    p["ASH-1"] = [102.0, 34.0]  # keeper deepest
    return p | override


def offside_pass(attacker_parts, defender_parts, ball=(60.0, 40.0), pos=None) -> MatchEvent:
    track = [(100.0 + i * 0.01, ball[0] + max(0, i) * 0.2, ball[1]) for i in range(-10, 11)]
    return MatchEvent.model_validate(
        {
            "event_id": "m01-e1", "match_id": "m01", "period": 2, "clock": 100.06, "type": "pass",
            "team": "NBR", "player": "NBR-8", "receiver": "NBR-9", "outcome": "complete",
            "start": list(ball), "end": [90.0, 30.0], "ball_speed_kmh": 70.0,
            "positions": pos or positions(**{"NBR-9": [85.0, 26.0], "ASH-4": [84.8, 30.0]}),
            "frame": {"clock": 100.0, "body": {"NBR-9": attacker_parts, "ASH-4": defender_parts}},
            "ball_track": track,
        }
    )  # fmt: skip


# ── Offside (Law 11) with fixed frames ───────────────────────────────────────


def test_kick_is_the_last_still_sample():
    track = [(10.00, 5.0, 5.0), (10.01, 5.0, 5.0), (10.02, 5.0, 5.0), (10.03, 5.2, 5.0)]
    assert find_kick(track) == (10.02, 5.0, 5.0)


def test_margin_measured_on_playable_parts():
    att = {"torso": [85.0, 26.0], "foot_l": [85.40, 26.1], "arm": [85.9, 26.3]}
    deff = {"torso": [84.8, 30.0], "foot_l": [85.02, 30.1], "arm": [85.6, 29.7]}
    m = measure(offside_pass(att, deff), attack_dir=1)
    assert m.margin_m == pytest.approx(0.38)  # foot to foot; arms ignored on both sides
    assert m.offside_position and m.kick_clock == pytest.approx(100.0)
    assert (m.attacker_x, m.defender_x) == (85.4, 85.02)


def test_level_or_behind_is_not_offside():
    att = {"torso": [84.6, 26.0], "foot_l": [84.95, 26.1], "arm": [85.9, 26.3]}
    deff = {"torso": [84.8, 30.0], "foot_l": [85.02, 30.1]}
    m = measure(offside_pass(att, deff), attack_dir=1)
    assert m.margin_m == pytest.approx(-0.07) and not m.offside_position  # the arm alone is ahead


def test_line_is_the_second_last_opponent_not_the_deepest():
    pos = positions(**{"NBR-9": [85.0, 26.0], "ASH-4": [84.8, 30.0], "ASH-5": [80.0, 40.0]})
    e = offside_pass({"foot_l": [85.4, 26.0]}, {"foot_l": [85.0, 30.0]}, pos=pos)
    assert measure(e, attack_dir=1).defender == "ASH-4"  # ASH-1 (keeper) is last, ASH-4 second-last


def test_own_half_is_never_offside():
    pos = positions(**{f"ASH-{n}": [30.0, 5.0 + n * 5] for n in range(2, 12)})
    pos |= {"ASH-1": [5.0, 34.0], "NBR-9": [45.0, 26.0], "ASH-4": [44.0, 30.0]}
    e = offside_pass({"foot_l": [45.5, 26.0]}, {"foot_l": [44.2, 30.0]}, ball=(20.0, 30.0), pos=pos)
    m = measure(e, attack_dir=1)
    assert m.margin_m > 0 and not m.offside_position


def test_attacking_toward_x_zero():
    att = {"foot_l": [19.6, 26.0]}
    deff = {"foot_l": [20.0, 30.0]}
    pos = positions(**{f"ASH-{n}": [40.0, 5.0 + n * 5] for n in range(1, 12)})
    pos |= {"ASH-1": [2.0, 34.0], "ASH-4": [20.2, 30.0], "NBR-9": [19.8, 26.0]}
    m = measure(offside_pass(att, deff, ball=(45.0, 40.0), pos=pos), attack_dir=-1)
    assert m.margin_m == pytest.approx(0.4) and m.offside_position


# ── Stats with fixed frames ──────────────────────────────────────────────────


def tracking(clock: float, x: float) -> MatchEvent:
    pos = positions(**{"NBR-9": [x, 30.0]})
    return MatchEvent.model_validate(
        {"event_id": f"m01-e{int(clock * 10)}", "match_id": "m01", "period": 1, "clock": clock,
         "type": "tracking", "positions": pos}
    )  # fmt: skip


def test_speed_and_sprint_from_positions():
    mv = Movement()
    sprints = []
    xs = [10.0, 13.0, 17.5, 22.0, 26.5, 31.0, 34.0, 35.5, 36.0, 36.2]  # 9 m/s in the middle
    for i, x in enumerate(xs):
        sprints += mv.update(tracking(i * 0.5, x))
    assert mv.top_kmh["NBR-9"] == pytest.approx(9.0 * 3.6, abs=0.1)
    assert mv.distance_m["NBR-9"] == pytest.approx(26.2)
    assert len(sprints) == 1 and sprints[0].peak_kmh == pytest.approx(32.4, abs=0.1)


def test_long_pass_through_defenders_is_harder():
    base = offside_pass({"foot_l": [85.4, 26.0]}, {"foot_l": [85.0, 30.0]})
    short = base.model_copy(update={"start": (50.0, 30.0), "end": (58.0, 30.0), "frame": None, "ball_track": None})
    lane = positions(**{"ASH-6": [70.0, 33.0], "ASH-7": [80.0, 30.5], "NBR-9": [90.0, 30.0]})
    long_ = short.model_copy(update={"start": (55.0, 34.0), "end": (90.0, 30.0), "positions": lane})
    easy, hard = pass_quality(short, 1), pass_quality(long_, 1)
    assert hard.difficulty > easy.difficulty + 0.3
    assert hard.lane_defenders >= 1 and hard.progress_m == pytest.approx(35.0)


def test_rolling_metrics_shares_add_up():
    engine, facts = facts_for("golden")
    for f in (f for f in facts if f.kind == "metrics"):
        teams = [k for k in f.values if len(k) == 3 and k.isupper()]
        assert abs(sum(f.values[t]["possession"] for t in teams) - 100) <= 1
        assert all(0 <= f.values[t][k] <= 100 for t in teams for k in ("control", "pressure"))
    assert isinstance(Rolling().metrics(1, 0.0, ("ASH", "NBR")), dict)


# ── Decision state machine ───────────────────────────────────────────────────


def test_transitions_only_move_forward():
    desk = DecisionDesk("m01")
    inc = desk.open("offside", "goal", 10.0)
    with pytest.raises(IllegalTransition):
        desk.restart(inc.incident_id)  # never restart an undecided call
    desk.review(inc.incident_id)
    desk.decide(inc.incident_id, "no_goal", 50.0)
    desk.restart(inc.incident_id)
    with pytest.raises(IllegalTransition):
        desk.review(inc.incident_id)


def test_facts_locked_until_decided():
    desk = DecisionDesk("m01")
    inc = desk.open("offside", "goal", 10.0)
    desk.review(inc.incident_id)
    desk.add_facts(inc.incident_id, {"margin_m": 0.31})
    assert desk.incidents[inc.incident_id].overlay_facts() == {}
    desk.decide(inc.incident_id, "no_goal", 45.0)
    assert desk.incidents[inc.incident_id].overlay_facts() == {"margin_m": 0.31}
    assert desk.incidents[inc.incident_id].reason_codes == ["tight_margin"]  # 35 s check, under 0.5 m


# ── Golden match fixture ─────────────────────────────────────────────────────


def golden_offside():
    _, facts = facts_for("golden")
    var = [f for f in facts if f.kind == "incident" and f.values["var_outcome"] == "offside"]
    iid = var[0].values["incident_id"]
    return [f for f in facts if f.kind == "incident" and f.values["incident_id"] == iid]


def test_golden_offside_runs_open_reviewing_decided_restarted():
    states = [f.values["state"] for f in golden_offside()]
    assert states == ["open", "reviewing", "decided", "restarted"]


def test_golden_offside_facts():
    decided = next(f for f in golden_offside() if f.values["state"] == "decided")
    v = decided.values
    assert (v["kind"], v["on_field"], v["final"], v["var_outcome"]) == (
        "offside",
        "goal",
        "no_goal",
        "offside",
    )
    assert v["facts"]["margin_m"] == 0.31
    assert (v["facts"]["attacker"], v["facts"]["defender"]) == ("NBR-9", "ASH-4")
    assert v["facts"]["check_s"] == 35.0
    assert v["reason_codes"] == ["tight_margin"]
    assert v["goal_event"] in decided.sources and v["incident_id"] in decided.sources


@pytest.mark.parametrize("name", ["golden", "red_card", "late_comeback"])
def test_no_premature_reveal(name):
    """N4: no margin, measurement or verdict leaves the engine before the decision."""
    _, facts = facts_for(name)
    for f in facts:
        if f.kind == "incident" and f.values["state"] in ("open", "reviewing"):
            assert f.values["facts"] == {} and f.values["final"] is None and f.values["var_outcome"] is None


def test_golden_score_counts_the_disallowed_goal_out():
    engine, facts = facts_for("golden")
    disallowed = [f for f in facts if f.kind == "goal" and f.values.get("disallowed")]
    assert len(disallowed) == 1 and disallowed[0].team == "NBR"
    goals = [f for f in facts if f.kind == "goal" and not f.values.get("disallowed")]
    assert engine.score == {t: sum(g.team == t for g in goals) - (t == "NBR") for t in ("ASH", "NBR")}


def test_red_card_incident_is_upheld_with_foul_facts():
    _, facts = facts_for("red_card")
    first = next(f.values["incident_id"] for f in facts if f.kind == "incident" and f.values["kind"] == "red_card")
    red = [f for f in facts if f.kind == "incident" and f.values["incident_id"] == first]
    assert [f.values["state"] for f in red] == ["open", "reviewing", "decided", "restarted"]
    v = red[2].values
    assert (v["final"], v["var_outcome"]) == ("red_card", "upheld")
    assert v["facts"]["offender"] == "ASH-5" and v["facts"]["fouled"].startswith("NBR-")


def test_every_fact_cites_its_events():
    _, facts = facts_for("golden")
    assert all(f.sources for f in facts)


# ── Call replays ─────────────────────────────────────────────────────────────


def golden_offside_replays():
    engine, facts = facts_for("golden")
    iid = next(f.values["incident_id"] for f in facts if f.kind == "incident" and f.values["var_outcome"] == "offside")
    return engine.replays[iid]


def test_replay_during_the_check_shows_no_measurement():
    during = golden_offside_replays()["during"]
    assert during.lines is None
    assert all(m.reveal != "after" for m in during.moments)
    text = " ".join(during.status.values()) + " ".join(m.label for m in during.moments)
    assert "cm" not in text and "0.31" not in text and "no goal" not in text.lower()


def test_replay_after_the_decision_carries_the_line():
    after = golden_offside_replays()["after"]
    assert after.lines.margin_m == 0.31 and after.lines.attacker_part != "arm"
    assert {f.role for f in after.focus} == {"attacker", "defender", "passer"}
    assert "31 cm (12.2 in)" in after.status["fan"]
    assert after.frames[0].t <= after.key_t <= after.frames[-1].t


def test_replay_speeds_are_derived_and_plausible():
    after = golden_offside_replays()["after"]
    i = min(range(len(after.frames)), key=lambda i: abs(after.frames[i].t - after.key_t))
    attacker = after.lines.attacker
    assert 8 < after.speeds[attacker][i] < 36  # running onto the pass, not standing
    assert all(v is None or 0 <= v < 40 for vs in after.speeds.values() for v in vs)


def test_every_call_gets_a_replay():
    engine, facts = facts_for("golden")
    decided = {f.values["incident_id"] for f in facts if f.kind == "incident" and f.values["state"] == "decided"}
    assert decided == {iid for iid, phases in engine.replays.items() if "after" in phases}


def test_player_log_cites_events():
    engine, _ = facts_for("golden")
    log = engine.player_log["NBR-9"]
    assert log and all(a["event_id"] for a in log)
    assert any(a["action"] == "call" and a["role"] == "attacker" for a in log)


def incidents_of(name, **match):
    engine, _ = facts_for(name)
    return [i for i in engine.desk.incidents.values() if all(getattr(i, k) == v for k, v in match.items())]


def test_goal_stands_after_a_tight_check():
    (inc,) = incidents_of("golden", kind="offside", final="goal")
    assert inc.var_outcome == "no_offence" and inc.facts["margin_m"] == -0.06
    assert "tight_margin" in inc.reason_codes and inc.facts["offside_position"] is False


def test_penalty_review_opens_its_own_incident():
    engine, _ = facts_for("golden")
    (inc,) = incidents_of("golden", kind="penalty")
    assert (inc.on_field, inc.final, inc.var_outcome) == ("no_penalty", "penalty", "penalty")
    assert inc.reason_codes == ["subjective_judgement"]
    assert inc.facts["offender"].startswith("NBR") and inc.facts["fouled"].startswith("ASH")
    assert "Why a penalty" in engine.replays[inc.incident_id]["after"].status["fan"]


def test_red_card_overturned_is_downgraded():
    engine, _ = facts_for("red_card")
    (inc,) = incidents_of("red_card", kind="red_card", var_outcome="overturned")
    assert inc.final == "yellow"
    assert "downgraded" in engine.replays[inc.incident_id]["after"].status["fan"]
