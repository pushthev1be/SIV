"""Real VAR errors (docs/var-errors.md): the measured offside calls must be flagged, never defended."""

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


def test_correct_calls_are_not_flagged(engine: Engine) -> None:
    for iid, phases in engine.replays.items():
        inc = engine.desk.incidents[iid]
        if inc.kind == "offside" and inc.var_outcome is None and "after" in phases:  # the natural flag, no review
            assert FLAG not in phases["after"].status["fan"], iid
