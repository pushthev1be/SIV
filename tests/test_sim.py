import functools

import pytest

from contracts import MatchEvent
from sim import realism
from sim.scenario import load_scenario, scenario_names
from sim.simulator import ATTACKER_BODY, DEFENDER_BODY, Simulator


@functools.cache
def run(name: str) -> tuple[MatchEvent, ...]:
    return tuple(Simulator(load_scenario(name)).run())


def of(events, type_):
    return [e for e in events if e.type == type_]


# ── Determinism and stream shape ─────────────────────────────────────────────


def test_same_scenario_same_match():
    again = Simulator(load_scenario("golden")).run()
    assert [e.model_dump_json() for e in again] == [e.model_dump_json() for e in run("golden")]


def test_different_seed_different_match():
    sc = load_scenario("golden").model_copy(update={"seed": 1})
    other = Simulator(sc).run()
    assert [e.model_dump_json() for e in other[:200]] != [e.model_dump_json() for e in run("golden")[:200]]


@pytest.mark.parametrize("name", scenario_names())
def test_stream_is_ordered_with_unique_ids(name):
    events = run(name)
    assert [e.event_id for e in events] == [f"{events[0].match_id}-e{i}" for i in range(1, len(events) + 1)]
    keys = [(e.period, e.clock) for e in events]
    assert keys == sorted(keys)
    assert [e.type for e in events].count("period_end") == 2
    assert events[-1].type == "period_end"


@pytest.mark.parametrize("name", scenario_names())
def test_match_looks_like_football(name):
    assert realism.check(realism.summarize(list(run(name)))) == []


# ── Golden match: the scripted offside goal ──────────────────────────────────


def offside_sequence(decision: str = "no_goal"):
    """The scripted offside check that ends in `decision`: its events, check index and kick pass."""
    events = run("golden")
    checks = [i for i, e in enumerate(events) if e.type == "var_check" and e.outcome == "offside"]
    i = next(i for i in checks if next(e for e in events[i:] if e.type == "var_decision").outcome == decision)
    kick = next(e for e in reversed(events[:i]) if e.type == "pass" and e.frame is not None)
    return events, i, kick


def test_offside_pass_carries_evidence():
    _, _, kick = offside_sequence()
    assert (kick.player, kick.receiver, kick.team) == ("NBR-8", "NBR-9", "NBR")
    assert abs(kick.clock - 4679.4) < 30
    assert {"NBR-9", "ASH-4"} <= set(kick.frame.body)


def test_offside_margin_uses_playable_parts_only():
    _, _, kick = offside_sequence()
    playable = [k for k in ATTACKER_BODY if k != "arm"]
    att = max(kick.frame.body["NBR-9"][k][0] for k in playable)  # NBR attacks +x in period 2
    deff = max(kick.frame.body["ASH-4"][k][0] for k in playable)
    assert att - deff == pytest.approx(0.31, abs=0.002)
    # The arm sits further forward: counting it would overstate the margin.
    assert kick.frame.body["NBR-9"]["arm"][0] - deff > 0.31 + 0.1
    assert set(ATTACKER_BODY) == set(DEFENDER_BODY)


def test_ash_4_is_the_second_last_defender_at_the_kick():
    _, _, kick = offside_sequence()
    ash = sorted(((x, pid) for pid, (x, _) in kick.positions.items() if pid.startswith("ASH")), reverse=True)
    assert ash[0][1] == "ASH-1" and ash[1][1] == "ASH-4"


def test_ball_track_finds_the_kick():
    _, _, kick = offside_sequence()
    track = kick.ball_track
    moved = next(i for i in range(1, len(track)) if track[i][1:] != track[0][1:])
    assert track[moved - 1][0] == pytest.approx(kick.frame.clock)
    assert kick.clock > kick.frame.clock  # the event feed's timestamp is coarser than the track


def test_goal_then_check_then_no_goal():
    events, i, kick = offside_sequence()
    between = events[events.index(kick) : i]
    goal = next(e for e in between if e.type == "goal")
    assert goal.player == "NBR-9"
    check = events[i]
    decision = next(e for e in events[i:] if e.type == "var_decision")
    assert (check.outcome, decision.outcome) == ("offside", "no_goal")
    assert decision.clock - check.clock == pytest.approx(35, abs=0.2)
    assert not check.positions and not decision.positions


# ── Other scenarios ──────────────────────────────────────────────────────────


def test_red_card_removes_the_player():
    events = run("red_card")
    card = next(e for e in events if e.type == "card" and e.outcome == "red")
    assert card.player == "ASH-5"
    after = events[events.index(card) :]
    decision = next(e for e in after if e.type == "var_decision")
    assert decision.outcome == "red_card_upheld"
    later = [e for e in events[events.index(decision) :] if e.positions]
    assert later and all("ASH-5" not in e.positions and len(e.positions) == 21 for e in later)


def test_late_comeback_goal_order():
    goals = of(run("late_comeback"), "goal")
    assert [g.team for g in goals] == ["ASH", "ASH", "NBR", "NBR", "NBR"]
    assert goals[-1].clock >= 5395


# ── Producer pacing (no Azure needed) ────────────────────────────────────────


def test_schedule_scales_time_and_pauses_at_half_time():
    from sim.producer import HALF_TIME_PAUSE_S, schedule

    events = list(run("golden"))
    plan = schedule(events, speed=60)
    walls = [w for w, _ in plan]
    assert walls == sorted(walls) and walls[0] == 0
    i = next(i for i, e in enumerate(events) if e.period == 2)
    assert walls[i] - walls[i - 1] == HALF_TIME_PAUSE_S
    # Within a half, one wall second is 60 match seconds.
    a, b = events[10], events[400]
    assert walls[400] - walls[10] == pytest.approx((b.clock - a.clock) / 60)


def test_tight_onside_goal_stands():
    events, i, kick = offside_sequence("goal_stands")
    playable = [k for k in ATTACKER_BODY if k != "arm"]
    att = max(kick.frame.body[kick.receiver][k][0] for k in playable)
    deff = max(
        v[k][0] for pid, v in kick.frame.body.items() if pid.startswith("ASH") for k in playable if k in v
    )
    assert att - deff == pytest.approx(-0.06, abs=0.002)  # 6 cm behind the line: onside
    after = events[i:]
    assert next(e for e in after if e.type == "var_decision").outcome == "goal_stands"
    assert (
        next(e for e in after if e.type in ("kickoff", "pass")).type == "kickoff"
    )  # play restarts from the centre


def test_penalty_review_from_play_waved_on():
    events = list(run("golden"))
    i = next(i for i, e in enumerate(events) if e.type == "var_check" and e.outcome == "penalty")
    check = events[i]
    challenge = next(e for e in reversed(events[:i]) if e.type == "tackle" and e.player == check.player)
    assert not any(e.type == "foul" for e in events[events.index(challenge) : i])  # no whistle on the field
    decision = next(e for e in events[i:] if e.type == "var_decision")
    assert decision.outcome == "penalty_awarded"
    kick = next(e for e in events[i:] if e.type == "shot")
    assert kick.team != check.team and 10 < kick.start[0] < 95  # taken by the other side, from the spot


def test_red_card_can_be_overturned():
    events = list(run("red_card"))
    reds = [e for e in events if e.type == "card" and e.outcome == "red"]
    assert [r.player for r in reds] == ["ASH-5", "NBR-6"]
    after = events[events.index(reds[1]) :]
    assert next(e for e in after if e.type == "var_decision").outcome == "red_card_overturned"
    assert any("NBR-6" in e.positions for e in after if e.positions)  # still on the pitch
