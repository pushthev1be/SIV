"""Every simulated VAR call follows its play script (sim/plays.py), and open play stays sensible."""

from __future__ import annotations

import functools

import pytest

from sim.match import wide_open
from sim.plays import PLAYS
from sim.scenario import load_scenario, scenario_names
from sim.simulator import Simulator

L, W = 105.0, 68.0


@functools.cache
def run(name: str):
    sim = Simulator(load_scenario(name))
    return sim, sim.run()


@pytest.mark.parametrize("name", scenario_names())
def test_every_scripted_call_follows_its_play(name):
    sim, events = run(name)
    expected = [s.kind for s in sorted(load_scenario(name).script, key=lambda s: s.at) if s.kind in PLAYS]
    assert [p["kind"] for p in sim.play_log] == expected  # each call's play ran once, in order
    for p in sim.play_log:
        play = PLAYS[p["kind"]]
        assert [role for *_, role in p["passes"]] == [s.to for s in play.steps], p["kind"]
        # The feed carries those passes, completed, in that order, while the play ran.
        feed = iter(
            (e.player, e.receiver)
            for e in events
            if e.type == "pass" and e.outcome == "complete" and p["start"] <= e.clock <= p["end"] + 0.5
        )
        for passer, receiver, role in p["passes"]:
            assert (passer, receiver) in feed, (p["kind"], role)


@pytest.mark.parametrize("name", scenario_names())
def test_nobody_passes_backwards_when_wide_open(name):
    """Inside 18 m, nobody within 3 m and a clear line to goal: a player shoots, he doesn't pass it back."""
    sc = load_scenario(name)
    _, events = run(name)
    bad = []
    for e in events:
        if e.type != "pass" or not e.start or not e.end or not e.positions:
            continue
        home_dir = -1 if e.period == 1 else 1
        d = home_dir if e.team == sc.home else -home_dir
        goal = (L if d == 1 else 0.0, W / 2)
        opponents = [xy for pid, xy in e.positions.items() if pid[:3] != e.team and not pid.endswith("-1")]
        if wide_open(e.start, opponents, goal) and (e.end[0] - e.start[0]) * d < -3.0:
            bad.append((e.event_id, e.player, e.start, e.end))
    assert bad == []
