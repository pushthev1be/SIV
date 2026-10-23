"""Real VAR errors (docs/var-errors.md): every measured error must be flagged, never defended."""

from __future__ import annotations

import pytest

from engine.engine import Engine
from sim.scenario import load_scenario
from sim.simulator import Simulator

FLAG = "The measurement alone does not explain this decision"


@pytest.fixture(scope="module")
def engine() -> Engine:
    sc = load_scenario("var_errors")
    eng = Engine(sc.match_id)
    for e in Simulator(sc).run():
        eng.ingest(e)
    return eng


def reviewed_offsides(engine: Engine) -> list:
    return [i for i in engine.desk.incidents.values() if i.kind == "offside" and i.var_outcome is not None]


def test_all_three_offside_errors_are_flagged(engine: Engine) -> None:
    offs = reviewed_offsides(engine)
    assert len(offs) == 3
    for inc in offs:
        fan = engine.replays[inc.incident_id]["after"].status["fan"]
        assert FLAG in fan, inc.incident_id
        assert "but the measurement says" in fan, inc.incident_id
        assert not fan.startswith("Why"), inc.incident_id  # never opens by justifying the call


def test_measurement_points_against_each_decision(engine: Engine) -> None:
    for inc in reviewed_offsides(engine):
        margin = inc.facts["margin_m"]
        assert (margin > 0) == (inc.final == "goal"), (inc.incident_id, margin, inc.final)


def test_missed_ball_penalty_is_flagged(engine: Engine) -> None:
    """Onana on Kalajdzic: the challenger never reached the ball, contact was in the area, no penalty."""
    (inc,) = [i for i in engine.desk.incidents.values() if i.kind == "penalty"]
    pkg = engine.replays[inc.incident_id]["after"]
    assert inc.final == "no_penalty"
    assert pkg.measurement is not None and pkg.measurement.kind == "ball_contact"
    assert pkg.measurement.factors["touched_ball"] is False and pkg.measurement.factors["contact_in_area"] is True
    assert pkg.measurement.value_m > 0.3
    assert pkg.status["fan"].startswith("No penalty, but the measurement says foul.")
    assert FLAG in pkg.status["fan"]


def test_red_card_is_still_a_judgement(engine: Engine) -> None:
    """Lewis-Skelly: nothing measured yet, so the explainer can only pass on the verdict."""
    (inc,) = [i for i in engine.desk.incidents.values() if i.kind == "red_card"]
    assert engine.replays[inc.incident_id]["after"].measurement is None


def test_correct_calls_are_not_flagged(engine: Engine) -> None:
    for iid, phases in engine.replays.items():
        inc = engine.desk.incidents[iid]
        if inc.kind == "offside" and inc.var_outcome is None and "after" in phases:  # the natural flag, no review
            assert FLAG not in phases["after"].status["fan"], iid
