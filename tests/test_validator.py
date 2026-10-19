import pytest

from orchestrator.validator import validate_text

# The fact packet the Decision Desk would get for the golden match's offside call, once decided.
DECIDED = {
    "incident": {
        "incident_id": "inc-m01-09", "kind": "offside", "state": "decided", "final": "no_goal",
        "goal_event": "m01-e11433",
        "facts": {"margin_m": 0.31, "kick_clock": 4681.0, "attacker": "NBR-9", "defender": "ASH-4",
                  "check_s": 35.0, "attacker_part": "foot_l"},
    },
    "speeds_kmh": {"NBR-9": 17.8, "ASH-4": 9.0},
    "score": {"NBR": 2, "ASH": 2},
    "pass": {"event_id": "m01-e11422", "difficulty": 0.77, "length_m": 30.8},
    "sources": ["m01-e11422", "m01-e11433", "inc-m01-09"],
}  # fmt: skip
# During the check the facts are locked; the packet still says who scored and who is involved.
REVIEWING = {
    "incident": {**DECIDED["incident"], "state": "reviewing", "final": None, "facts": {}},
    "goal": {"event_id": "m01-e11433", "player": "NBR-9"},
    "focus": ["NBR-9", "ASH-4", "NBR-8"],
    "sources": ["m01-e11433", "inc-m01-09"],
}


def kinds(text, packet=DECIDED, state=None):
    return [v.kind for v in validate_text(text, packet, state)]


@pytest.mark.parametrize(
    "text",
    [
        "Offside: NBR-9 was 31 cm (12.2 in) ahead of ASH-4 when the pass was played.",
        "The margin was 0.31 m, and the check took 35 seconds.",
        "NBR-9 was running at 17.8 km/h (11.1 mph); ASH-4 stepped up at 9 km/h.",
        "A 30.8 m pass rated 77/100 for difficulty, 77% in the analyst view.",
        "The pass at 78:01 is the moment that matters (m01-e11422).",
        "It stays 2-2 after the goal is ruled out.",
        "That margin is about 1.0 ft.",
    ],
)
def test_grounded_text_passes(text):
    assert validate_text(text, DECIDED, "decided") == []


@pytest.mark.parametrize(
    "text, token",
    [
        ("NBR-9 was 32 cm ahead of ASH-4.", "32 cm"),  # planted wrong number
        ("NBR-9 was 13.5 in ahead.", "13.5 in"),  # wrong conversion
        ("NBR-9 hit 19 km/h on the run.", "19 km/h"),
        ("The check took 50 seconds.", "50 seconds"),
        ("The pass at 80:00 started it.", "80:00"),
        ("It is now 3-2.", "3-2"),
    ],
)
def test_planted_wrong_numbers_are_rejected(text, token):
    v = validate_text(text, DECIDED, "decided")
    assert [x.kind for x in v] == ["unknown_number"] and v[0].token == token


def test_law_references_are_not_match_numbers():
    assert validate_text("Under Law 11, and Laws 12 and 14 for penalties.", DECIDED, "decided") == []
    assert kinds("Under Law 11 the margin was 33 cm.") == ["unknown_number"]


def test_unknown_ids_are_rejected():
    assert kinds("See m01-e99999 and NBR-11.") == ["unknown_id", "unknown_id"]


@pytest.mark.parametrize(
    "text",
    [
        "The officials are checking a possible offside on NBR-9 at 78:01.",
        "VAR is looking at whether NBR-9 was beyond the second-last defender when the pass was played.",
        "Check in progress: possible offside.",
        "The officials are checking whether NBR-9 was offside when NBR-8 played the pass.",
        "VAR wants to know if NBR-9 is offside at the moment of the pass.",
    ],
)
def test_neutral_text_during_a_check_passes(text):
    packet = {**REVIEWING, "clock": 4681.0}
    assert validate_text(text, packet, "reviewing") == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("NBR-9 is offside by 31 cm.", ["premature_reveal", "premature_reveal"]),
        ("No goal: the attacker was offside.", ["premature_reveal", "premature_reveal"]),
        ("Looks like the goal stands, he was onside.", ["premature_reveal", "premature_reveal"]),
        ("Check complete.", ["check_complete"]),
        ("The red card is upheld.", ["premature_reveal"]),
        ("Penalty awarded after the review.", ["premature_reveal"]),
        ("They checked whether he was offside. He was offside.", ["premature_reveal"]),  # the answer counts
    ],
)
def test_nothing_is_revealed_before_the_decision(text, expected):
    assert sorted(kinds(text, REVIEWING, "reviewing")) == sorted(expected)


def test_the_same_words_are_fine_once_decided():
    assert validate_text("Check complete: no goal, offside.", DECIDED, "decided") == []
