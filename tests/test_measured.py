"""Measured VAR checks beyond offside: unit tests on fixed events, then the showcase match."""

import functools

import pytest

from contracts import MatchEvent
from engine import measure
from mcp_server.store import MatchStore


def positions(**override):
    p = {f"NBR-{n}": [40.0 + n, 4.0 + n * 5] for n in range(1, 12)}
    p |= {f"ASH-{n}": [50.0 + n, 4.0 + n * 5] for n in range(1, 12)}
    return p | override


def ev(**fields) -> MatchEvent:
    base = {"event_id": "m04-e1", "match_id": "m04", "period": 1, "clock": 100.0, "type": "pass", "team": "NBR",
            "player": "NBR-7", "positions": positions()}  # fmt: skip
    return MatchEvent.model_validate(base | fields)


def track(points, t0=100.0):
    return [(round(t0 + i * 0.01, 2), x, y) for i, (x, y) in enumerate(points)]


FRAME = {"clock": 100.0, "body": {"NBR-7": {"foot_l": [104.0, 54.0]}}}


# ── Units ────────────────────────────────────────────────────────────────────


def test_ball_wholly_over_the_goal_line():
    m = measure.ball_out(ev(frame=FRAME, ball_track=track([(104.9, 54.4), (105.15, 54.4), (100.0, 40.0)])))
    assert m.value_m == pytest.approx(0.04) and m.line_label == "goal line"  # centre 0.15 over, minus radius


def test_ball_still_touching_the_touchline():
    m = measure.ball_out(ev(frame=FRAME, ball_track=track([(60.0, 67.95), (60.0, 68.05), (55.0, 60.0)])))
    assert m.value_m == pytest.approx(-0.06) and m.line_label == "touchline"  # part of it is still on the line


@pytest.mark.parametrize("x, expected", [(105.08, -0.03), (105.14, 0.03)])
def test_goal_line_needs_the_whole_ball(x, expected):
    m = measure.goal_line(ev(type="shot", frame=FRAME, ball_track=track([(93.0, 30.0), (x, 36.0)])), attack_dir=1)
    assert m.value_m == pytest.approx(expected)


@pytest.mark.parametrize(
    "contact, expected, label",
    [
        ((88.36, 36.0), 0.14, "edge of the penalty area"),  # 14 cm short of the area: a free kick
        ((88.60, 36.0), -0.1, "edge of the penalty area"),  # 10 cm inside: a penalty
        ((95.0, 54.30), 0.14, "side of the penalty area"),  # wide of the side line
    ],
)
def test_box_edge(contact, expected, label):
    foul = ev(type="foul", player="ASH-4", receiver="NBR-9", contact=list(contact), frame=FRAME)
    m = measure.box_edge(foul, attack_dir=1)
    assert m.value_m == pytest.approx(expected) and m.line_label == label


def test_box_edge_attacking_toward_x_zero():
    foul = ev(type="foul", player="ASH-4", receiver="NBR-9", contact=[16.64, 36.0], frame=FRAME)
    assert measure.box_edge(foul, attack_dir=-1).value_m == pytest.approx(0.14)


@pytest.mark.parametrize("foot_x, expected", [(104.5, 0.38), (104.95, -0.07)])
def test_keeper_needs_a_foot_on_the_line(foot_x, expected):
    frame = {"clock": 100.0, "body": {"ASH-1": {"foot_l": [foot_x, 34.2], "foot_r": [foot_x - 0.11, 33.8]}}}
    shot = ev(type="shot", player="NBR-9", frame=frame, ball_track=track([(94.0, 34.0), (104.0, 35.0)]))
    assert measure.keeper_line(shot, "ASH-1", attack_dir=1).value_m == pytest.approx(expected)


def test_dogso_counts_defenders_in_the_way():
    pos = positions(**{"NBR-9": [84.7, 34.5], "ASH-1": [103.0, 34.0], "ASH-5": [92.0, 33.0], "ASH-6": [92.0, 60.0]})
    pos |= {f"ASH-{n}": [70.0, 34.0] for n in (2, 3, 4, 7, 8, 9, 10, 11)}
    foul = ev(type="foul", player="ASH-4", receiver="NBR-9", positions=pos, contact=[84.7, 34.5])
    f = measure.dogso(foul, "NBR-9", 1, velocity=(6.0, 0.4), in_control=True).factors
    assert f["defenders_between"] == 1  # ASH-5 is in the cone; ASH-6 is wide; the rest are behind
    assert f["keeper_goal_side"] and f["direction_off_goal_deg"] <= 10
    assert not measure.obvious_chance(f)


# ── The showcase match ───────────────────────────────────────────────────────


@functools.cache
def showcase() -> MatchStore:
    return MatchStore.from_scenario("var_showcase")


EXPECTED = {  # kind: (final, var_outcome, measured value)
    "ball_out": ("no_goal", "ball_out", 0.04),
    "penalty_location": ("free_kick", "outside_area", 0.14),
    "goal_line": ("no_goal", "goal_line", -0.03),
    "keeper_encroachment": ("retake", "encroachment", 0.38),
    "offside": ("no_goal", "offside", None),
    "dogso": ("red_card", "dogso", None),
}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_every_measured_call_is_decided_with_its_evidence(kind):
    st = showcase()
    (inc,) = [i for i in st.engine.desk.incidents.values() if i.kind == kind and i.var_outcome]
    final, var_outcome, value = EXPECTED[kind]
    assert (inc.final, inc.var_outcome) == (final, var_outcome)
    if value is not None:
        assert inc.facts["value_m"] == pytest.approx(value)
        after = st.engine.replays[inc.incident_id]["after"]
        assert after.measurement.value_m == pytest.approx(value)
        assert f"{abs(value) * 100:.0f} cm" in after.status["fan"]
    during = st.engine.replays[inc.incident_id].get("during")
    if during is not None:
        assert during.measurement is None and during.lines is None and during.facts == {}


def test_dogso_factors_make_the_case():
    st = showcase()
    (inc,) = [i for i in st.engine.desk.incidents.values() if i.kind == "dogso"]
    f = inc.facts
    assert f["distance_to_goal_m"] > 16.5  # outside the area: a free kick, so DOGSO is the question
    assert f["defenders_between"] == 0 and f["in_control"] is True
    assert measure.obvious_chance({k: f[k] for k in f})
    assert inc.reason_codes == ["subjective_judgement"]


def test_ruled_out_goals_come_off_the_score():
    st = showcase()
    goals = [e for e in st.events if e.type == "goal"]
    taken_off = [i for i in st.engine.desk.incidents.values() if i.goal_event and i.final == "no_goal"]
    assert sum(st.engine.score.values()) == len(goals) - len(taken_off)
