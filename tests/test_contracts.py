import pytest
from pydantic import ValidationError

from contracts import Incident, MatchEvent, OverlayCue, ReplayPackage
from contracts.generate import build_schema


def positions() -> dict[str, list[float]]:
    home = {f"NBR-{n}": [10.0 + n * 4, 34.0] for n in range(1, 12)}
    away = {f"ASH-{n}": [12.0 + n * 4, 30.0] for n in range(1, 12)}
    return home | away


def event(**over) -> dict:
    base = {
        "event_id": "m01-e2104",
        "match_id": "m01",
        "period": 2,
        "clock": 4679.4,
        "type": "pass",
        "team": "NBR",
        "player": "NBR-8",
        "receiver": "NBR-9",
        "outcome": "complete",
        "start": [60.0, 40.0],
        "end": [84.91, 26.0],
        "ball_speed_kmh": 79.2,
        "positions": positions(),
    }
    return base | over


def incident(**over) -> dict:
    base = {
        "incident_id": "inc-m01-03",
        "kind": "offside",
        "goal_event": "m01-e2106",
        "state": "decided",
        "on_field": "goal",
        "var_outcome": "offside",
        "final": "no_goal",
        "reason_codes": ["tight_margin"],
        "opened_at": 4685.0,
        "decided_at": 4720.0,
        "facts": {"margin_m": 0.31, "kick_clock": 4679.4, "attacker": "NBR-9", "defender": "ASH-4"},
    }
    return base | over


def cue(**over) -> dict:
    base = {
        "cue_id": "cue-0481",
        "at": 4720.0,
        "ttl_s": 8,
        "kind": "var_decision",
        "audience": {
            "persona": "casual",
            "club": "NBR",
            "focus": "NBR-9",
            "lang": "es",
            "pin": "sprint_speed",
        },
        "layers": ["scorebug", "var_badge", "offside_lines", "band"],
        "band": {"tag": "No gol", "text": "Fuera de juego · Okafor 31 cm adelantado en el pase"},
        "graphics": {"offside_lines": {"attacker_x": 84.91, "defender_x": 84.6}},
        "sources": ["m01-e2104", "m01-e2106", "inc-m01-03"],
        "origin": "agent",
        "validated": True,
        "approval": "auto",
    }
    return base | over


# ── Match event ──────────────────────────────────────────────────────────────


def test_event_round_trips():
    e = MatchEvent.model_validate(event())
    assert MatchEvent.model_validate_json(e.model_dump_json()) == e


def test_event_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        MatchEvent.model_validate(event(speed_kmh=30))  # speeds are derived, never stated


def test_event_needs_every_on_pitch_position():
    p = positions()
    p.pop("ASH-11")  # after a red card: 21 players is fine
    MatchEvent.model_validate(event(positions=p))
    with pytest.raises(ValidationError, match="every on-pitch position"):
        MatchEvent.model_validate(event(positions={"NBR-9": [84.91, 26.0]}))


def test_evidence_fields_appear_only_where_a_check_can_use_them():
    frame = {"clock": 4679.4, "body": {"NBR-9": {"foot_l": [85.21, 25.9], "arm": [85.36, 26.25]}}}
    track = [[4679.25, 59.99, 40.0]]
    MatchEvent.model_validate(event(frame=frame, ball_track=track))
    MatchEvent.model_validate(event(type="shot", frame=frame, ball_track=track))  # goal-line, penalty kicks
    MatchEvent.model_validate(event(type="foul", frame=frame, contact=[88.4, 30.0]))  # penalty or free kick
    with pytest.raises(ValidationError, match="needs a body frame"):
        MatchEvent.model_validate(event(ball_track=track))
    with pytest.raises(ValidationError, match="only on passes and shots"):
        MatchEvent.model_validate(event(type="foul", frame=frame, ball_track=track))
    with pytest.raises(ValidationError, match="contact appears only"):
        MatchEvent.model_validate(event(contact=[88.4, 30.0]))


def test_event_id_must_match_match_id():
    with pytest.raises(ValidationError, match="prefixed"):
        MatchEvent.model_validate(event(event_id="m02-e1"))


# ── Incident ─────────────────────────────────────────────────────────────────


def test_decided_incident_unlocks_facts():
    inc = Incident.model_validate(incident())
    assert inc.overlay_facts()["margin_m"] == 0.31


def test_reviewing_incident_hides_facts():
    inc = Incident.model_validate(incident(state="reviewing", final=None, decided_at=None))
    assert inc.overlay_facts() == {}


@pytest.mark.parametrize("state", ["open", "reviewing"])
def test_no_final_decision_before_decided(state):
    with pytest.raises(ValidationError, match="empty until the decision"):
        Incident.model_validate(incident(state=state))


def test_decided_needs_final():
    with pytest.raises(ValidationError, match="needs final"):
        Incident.model_validate(incident(final=None))


# ── Overlay cue ──────────────────────────────────────────────────────────────


def test_cue_from_spec_validates():
    OverlayCue.model_validate(cue())


def test_unvalidated_cue_never_airs():
    with pytest.raises(ValidationError, match="never airs"):
        OverlayCue.model_validate(cue(validated=False))


def test_band_layer_needs_band():
    with pytest.raises(ValidationError, match="together"):
        OverlayCue.model_validate(cue(band=None))


# ── Replay package ───────────────────────────────────────────────────────────


def replay_pkg(**over) -> dict:
    p = {"NBR-9": [84.0, 26.0]}
    base = {
        "incident_id": "inc-m01-03",
        "kind": "offside",
        "phase": "after",
        "period": 2,
        "key_t": 4679.4,
        "frames": [{"t": 4678.0, "positions": p}, {"t": 4681.0, "positions": p}],
        "ball": [{"t": 4678.0, "x": 60, "y": 40}, {"t": 4681.0, "x": 84, "y": 26}],
        "moments": [{"t": 4679.4, "label": "Kick point", "reveal": "always"}],
        "slow": {"start": 4679.0, "end": 4680.0, "rate": 0.25},
        "focus": [{"player": "NBR-9", "role": "attacker"}],
        "speeds": {"NBR-9": [None, 27.5]},
        "lines": {
            "attack_dir": 1, "attacker": "NBR-9", "defender": "ASH-4", "attacker_x": 84.93,
            "defender_x": 84.62, "attacker_part": "foot_l", "defender_part": "foot_l", "margin_m": 0.31,
            "body": {"NBR-9": {"foot_l": [84.93, 26.1]}},
        },
        "status": {"stadium": "Check complete: no goal", "officials": "Offside, 0.31 m"},
        "sources": ["m01-e2104", "inc-m01-03"],
    }  # fmt: skip
    return base | over


def test_replay_frames_in_order_and_slow_window_inside():
    ReplayPackage.model_validate(replay_pkg())
    with pytest.raises(ValidationError, match="inside the replay"):
        ReplayPackage.model_validate(replay_pkg(slow={"start": 4600.0, "end": 4680.0, "rate": 0.25}))


def test_during_replay_cannot_carry_the_measurement():
    with pytest.raises(ValidationError, match="N4"):
        ReplayPackage.model_validate(replay_pkg(phase="during"))
    with pytest.raises(ValidationError, match="N4"):
        moments = [{"t": 4679.4, "label": "31 cm ahead", "reveal": "after"}]
        ReplayPackage.model_validate(replay_pkg(phase="during", lines=None, moments=moments))
    ReplayPackage.model_validate(replay_pkg(phase="during", lines=None))


def test_replay_speeds_align_with_frames():
    with pytest.raises(ValidationError, match="align"):
        ReplayPackage.model_validate(replay_pkg(speeds={"NBR-9": [27.5]}))


# ── Schema generation ────────────────────────────────────────────────────────


def test_schema_exports_every_root_model():
    schema = build_schema()
    assert set(schema["properties"]) == {"MatchEvent", "Incident", "OverlayCue", "ReplayPackage"}
    assert all(name in schema["$defs"] for name in schema["properties"])
