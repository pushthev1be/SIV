"""Plain-language summaries of the laws behind VAR calls.

Written for this project as paraphrases, so agents can say which law applies without quoting
the official text. They describe; they never judge a particular decision.
"""

LAWS: dict[str, dict[str, str]] = {
    "offside": {
        "law": "Law 11",
        "summary": (
            "A player is in an offside position if any part of their head, body or feet is in the "
            "opponents' half and nearer to the opponents' goal line than both the ball and the "
            "second-last opponent. Hands and arms do not count, for attackers or defenders. Being in "
            "that position is not an offence on its own: it is penalised only if the player becomes "
            "involved in play. The position is judged at the moment a team-mate plays the ball."
        ),
        "how_checked": (
            "A factual check: find the frame where the ball is played, then compare the attacker's "
            "most forward playable part with the second-last defender's deepest playable part."
        ),
    },
    "penalty": {
        "law": "Laws 12 and 14",
        "summary": (
            "If a defender commits a foul that would give a direct free kick, such as a trip, a push "
            "or a careless, reckless or excessive challenge, inside their own penalty area, the "
            "attacking team gets a penalty kick from the penalty mark, 11 m from goal."
        ),
        "how_checked": (
            "A judgement check: whether the contact was a foul is the referee's call. VAR can "
            "recommend a review at the pitchside monitor if the on-field call looks clearly wrong."
        ),
    },
    "red_card": {
        "law": "Law 12",
        "summary": (
            "A challenge that endangers an opponent's safety, or uses excessive force or brutality, "
            "is serious foul play and a sending-off offence. Denying an obvious goal-scoring "
            "opportunity can also be a red card."
        ),
        "how_checked": (
            "A judgement check: the speed, intensity and point of contact of the challenge are "
            "weighed. The referee can uphold the red card or change it after review."
        ),
    },
    "handball": {
        "law": "Law 12",
        "summary": (
            "It is a handball offence to deliberately touch the ball with the hand or arm, or to make "
            "the body unnaturally bigger with them. The boundary of the arm is the bottom of the armpit."
        ),
        "how_checked": "Mostly a judgement check on arm position and movement.",
    },
    "var_protocol": {
        "law": "VAR protocol",
        "summary": (
            "VAR can only intervene on four kinds of decision: goals, penalties, direct red cards and "
            "mistaken identity. It recommends a change only for a clear and obvious error or a "
            "serious missed incident. Factual questions such as an offside position are settled by "
            "the evidence; judgement questions go to the referee, often at the pitchside monitor. "
            "The referee always makes the final decision."
        ),
        "how_checked": "Checks run in the background; a review pauses the game until the decision.",
    },
}

ALIASES = {
    "law 11": "offside", "onside": "offside", "penalty kick": "penalty", "foul": "penalty",
    "serious foul play": "red_card", "red card": "red_card", "sending off": "red_card",
    "hand ball": "handball", "var": "var_protocol", "review": "var_protocol",
}  # fmt: skip


def law_summary(topic: str) -> dict[str, str] | None:
    key = topic.strip().lower().replace("-", " ")
    key = ALIASES.get(key, key.replace(" ", "_"))
    if key in LAWS:
        return {"topic": key, **LAWS[key]}
    return None


# One sentence per call, for explanations: the rule as a fan needs it.
RULE_SENTENCE: dict[str, str] = {
    "offside": (
        "Under Law 11, a player is offside if any part of their head, body or feet is beyond the "
        "second-last defender when a team-mate plays the ball; arms don't count"
    ),
    "penalty": "Under Laws 12 and 14, a foul by a defender inside their own penalty area is a penalty",
    "red_card": (
        "Under Law 12, a challenge that endangers an opponent's safety is serious foul play and a red card; "
        "a reckless one is a yellow"
    ),
    "foul": "Under Law 12, a reckless challenge is a foul and a yellow card",
    "handball": "Under Law 12, deliberately handling the ball or making the body unnaturally bigger is handball",
}
FACTUAL_KINDS = {"offside"}  # settled by measurement; the rest are judgement calls
