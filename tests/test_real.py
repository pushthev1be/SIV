"""Real match clips from StatsBomb open data. The data is downloaded on first use; skipped offline."""

from __future__ import annotations

import functools

import pytest

from mcp_server.store import MatchStore
from real.clips import CLIPS, build
from tests.test_mcp_server import check_templates


@functools.cache
def japan_spain():
    try:
        return build("real_japan_spain")
    except OSError as err:  # no network and no cached data
        pytest.skip(f"StatsBomb data unavailable: {err}")


def test_clip_is_a_valid_feed_of_the_real_move():
    events, *_ = japan_spain()
    on_ball = [e for e in events if e.type in ("pass", "carry", "shot")]
    assert all(len(e.positions) <= 22 for e in events)
    # Ito, Tanaka, Doan, Mitoma, Tanaka: the move as StatsBomb logged it
    assert [e.player for e in on_ball if e.type == "pass"][-3:] == ["JPN-17", "JPN-8", "JPN-9"]
    assert on_ball[-1].type == "shot" and on_ball[-1].player == "JPN-17" and on_ball[-1].outcome == "ruled_out"
    cutback = next(e for e in on_ball if e.player == "JPN-9")
    assert cutback.start[0] == 105.0  # StatsBomb tags Mitoma's touch on the goal line itself
    assert [e.type for e in events if e.type.startswith("var")] == ["var_check", "var_decision"]


def test_engine_gives_the_goal_and_says_the_data_cannot_settle_it():
    events, engine, *_ = japan_spain()
    for e in events:
        engine.ingest(e)
    (inc,) = [i for i in engine.desk.incidents.values() if i.kind == "ball_out"]
    assert inc.on_field == "no_goal" and inc.final == "goal" and inc.var_outcome == "in_play"
    assert "check_s" not in inc.facts  # VAR timings are not in the data, so none is quoted
    fan = engine.replays[inc.incident_id]["after"].status["fan"]
    assert "too coarse" in fan and "goal is given" in fan
    assert engine.score == {"JPN": 2, "ESP": 1}


def test_real_clip_text_passes_the_validator():
    japan_spain()  # skips when offline
    check_templates(MatchStore.from_real("real_japan_spain"))


def test_every_clip_names_its_source_match():
    for clip in CLIPS.values():
        assert clip.sb_match and clip.match_code.startswith("m1")
