"""The agent desk's workflow, with stand-in agents: the real Agent Framework graph and the real Verifier."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from orchestrator.desk import AUDIENCES, Call, RecordingStore, Trace, graph, new_laws, wrong_terms

PACKET = {
    "incident_id": "inc-m01-09",
    "kind": "offside",
    "final": "no_goal",
    "facts": {"margin_m": 0.31},
    "law": {"topic": "offside", "law": "Law 11"},
    "names": {"NBR-9": "Okafor", "ASH-4": "Achterberg"},
}
GOOD = "Under Law 11, Okafor was 31 cm beyond Achterberg when the ball was played, so the goal is ruled out."
ENGINE = {a: f"engine {a} text" for a in ("fan", "analyst", "officials", "stadium")}


@dataclass
class Reply:
    text: str
    messages: list = field(default_factory=list)


class Scripted:
    """An agent that answers from a list (the last answer repeats) and remembers what it was asked."""

    def __init__(self, *answers: str) -> None:
        self.answers, self.asked = list(answers), []

    async def run(self, prompt: str) -> Reply:
        self.asked.append(prompt)
        return Reply(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])


class FakeStore(RecordingStore):
    def __init__(self) -> None:  # no match needed: the packet is what the agent "read"
        self.packet = [PACKET]


def run(desk, persona, plant=False, editor=None):
    trace = Trace()
    wf = graph(desk, persona, editor or Scripted("SAME"), FakeStore(), trace)
    result = asyncio.run(wf.run(Call("inc-m01-09", "offside", "decided", ENGINE, plant)))
    return result.get_outputs()[0], trace.steps


def test_a_grounded_draft_is_published_for_every_audience():
    bundle, steps = run(Scripted(GOOD), Scripted(GOOD))
    assert set(bundle) == set(AUDIENCES)
    assert all(v["origin"] == "agent" and v["text"] == GOOD for v in bundle.values())
    assert [s["passed"] for s in steps if s["step"] == "verify"] == [True] * 5
    assert [s["passed"] for s in steps if s["step"] == "edit"] == [True] * 4
    assert steps[-1]["step"] == "publish"


def test_a_planted_number_is_caught_and_the_desk_rewrites():
    desk = Scripted(GOOD)
    bundle, steps = run(desk, Scripted(GOOD), plant=True)
    verdicts = [s for s in steps if s["step"] == "verify" and s["target"] == "Decision Desk"]
    assert [v["passed"] for v in verdicts] == [False, True]  # the planted draft fails, the rewrite passes
    assert "38 cm" in verdicts[0]["reasons"][0]
    assert "rejected by the fact checker" in desk.asked[1]  # the retry names the reason
    assert bundle["fan"]["origin"] == "agent"


def test_the_editor_sends_back_a_rewrite_that_flips_the_meaning():
    flipped = "Under Law 11, Okafor was 31 cm beyond Achterberg, but the goal stands."
    editor = Scripted("DIFFERENT: the rewrite says the goal stands; the verified text says it is ruled out.", "SAME")
    persona = Scripted(flipped, GOOD)
    trace = Trace()
    wf = graph(Scripted(GOOD), persona, editor, FakeStore(), trace)
    call = Call("inc-m01-09", "offside", "decided", ENGINE)
    bundle = asyncio.run(wf.run(call)).get_outputs()[0]
    edits = [s for s in trace.steps if s["step"] == "edit"]
    assert edits[0]["passed"] is False and "goal stands" in edits[0]["reasons"][0]
    assert any("the meaning changed" in p for p in persona.asked)  # the retry carries the editor's reason
    assert any(v["text"] == GOOD and v["attempts"] == 2 for v in bundle.values())


def test_a_persona_that_keeps_inventing_falls_back_to_the_engine():
    bundle, steps = run(Scripted(GOOD), Scripted("He was 45 cm off, under Law 16."))
    assert all(v["origin"] == "engine" for v in bundle.values())
    assert bundle["es"]["text"] == ENGINE["fan"]  # no Spanish engine text: the fan text airs
    reasons = [r for s in steps if s["step"] == "verify" and not s["passed"] for r in s["reasons"]]
    assert any("wrong_law" in r for r in reasons) and any("45 cm" in r for r in reasons)


def test_spanish_football_words_mean_what_they_say():
    # "fuera de juego" is offside: on a ball out of play it tells the fan the wrong call
    assert wrong_terms("es", "ball_out", "El balón estaba fuera de juego, así que no hay gol.")
    assert wrong_terms("es", "ball_out", "El balón salió del terreno de juego por 4 cm.") == []
    assert wrong_terms("es", "offside", "Okafor estaba en fuera de juego por 31 cm.") == []
    assert wrong_terms("es", "goal_line", "Según la Ley 9, el balón entero debe cruzar la línea.")
    assert wrong_terms("es", "goal_line", "Según la Regla 9, el balón entero debe cruzar la línea.") == []
    assert wrong_terms("fan", "ball_out", "fuera de juego") == []  # only the Spanish audience


def test_a_rewrite_cites_no_law_but_the_calls_own():
    # The offside summary mentions Law 9 in passing, so the Verifier alone let "Regla 9" through on an offside call
    assert new_laws(GOOD, "Según la Regla 11, Okafor estaba 31 cm por delante de Achterberg.", "offside") == []
    assert new_laws(GOOD, "La Regla 9 y la Regla 11 dicen que Okafor estaba en fuera de juego.", "offside")
    # The law for the kind of call is fine even if the explanation didn't name it: a red card is Law 12
    assert new_laws("The challenge endangered his safety.", "Según la Regla 12, pone en peligro.", "red_card") == []
    assert new_laws("The keeper stepped off his line early.", "Under Law 16 he must stay on it.", "keeper_encroachment")
