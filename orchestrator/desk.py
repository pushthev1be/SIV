"""The SIV agent desk: a Microsoft Agent Framework workflow, one run per VAR call.

    uv run python -m orchestrator.desk var_showcase          # writes web/preview/data/desk_var_showcase.json
    uv run python -m orchestrator.desk real_japan_spain --plant inc-m10-01

    Decision Desk ──> Verifier ──fan-out──> Fan · Analyst · Español · Ref Buddy ──fan-in──> Publisher
     (agent, MCP)      (code)                 (agents; each checked by code, then the Editor agent)

- The Decision Desk agent reads the call over MCP (get_incident, get_replay, get_law_summary) and writes the
  explanation. It never sees the engine's own wording: replay status texts are stripped from what it reads.
- The Verifier is plain code (orchestrator/validator.py): every number and ID must be in what the agent read.
  A failed draft goes back once with the reasons; a second failure falls back to the engine's text.
- Four persona agents run concurrently on the verified text, each for its audience, adding no facts. Each
  draft is verified again by code, then by the Editor agent, which checks the meaning didn't change (code
  can't tell "in play" from "out"). One retry with the reason, then the engine's text is the fallback.
- `--plant` corrupts the Decision Desk's first draft for one call (a wrong number), to show the Verifier
  catching it. The trace records it as planted.

Every step lands in a trace (agent, tools called, time, verdict, retries, fallbacks). The output is cached
as JSON, so the published demo makes no model calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never

import uvicorn
from agent_framework import Executor, MCPStreamableHTTPTool, WorkflowBuilder, WorkflowContext, handler
from agent_framework.foundry import FoundryChatClient

from config import credential, setting
from engine.laws import law_summary
from mcp_server.server import build_server
from mcp_server.store import MatchStore
from orchestrator.validator import LAW_REF_RE, validate_text

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "preview" / "data"
AUDIENCES = ("fan", "analyst", "es", "officials")

DESK = """You are the Decision Desk for SIV (Super Intelligence Viewer). You explain a VAR call to football fans.
- Get every fact from your tools: get_incident, get_replay and get_law_summary (topic = the incident kind,
  or "red_card", "penalty", "offside"). Call them before you write. Never invent a number.
- Use the players' names from the tools' `names`, never their IDs.
- Say what the law says in one plain sentence, then what the evidence shows, with the numbers exactly as
  the tools give them (you may convert metres to centimetres or inches), then the decision.
- Three sentences at most. Plain English, no headings, no bullet points. Never write a field name
  (value_m, margin_m, var_outcome...) or a negative distance: say what it means in words ("11 cm behind",
  "on the line"). Write distances under a metre in centimetres. Times only as match clocks (83:43).

How to read the facts:
- `on_field` is the referee's call before the review; `final` is the decision after it; `var_outcome` says
  what the review found.
- `margin_m` (offside) is how far the attacker's nearest playable body part was beyond (+) or behind (-) the
  second-last defender when the ball was played. Beyond means offside; level or behind means onside.
- `value_m` is a signed distance: positive means what the check asks about happened: the whole ball was out
  (ball_out), the whole ball crossed the goal line (goal_line), the contact started outside the area
  (penalty_location), the keeper was off his line (keeper_encroachment), or the challenger never reached the
  ball (ball_contact). Negative means it didn't: e.g. a ball_out value of -0.11 means part of the ball was
  still on the line, so it was in play.
- For a penalty review, `touched_ball` false with `contact_in_area` true means the challenger never played
  the ball and made contact inside the area: that evidence points to a penalty. If no penalty was given,
  say the measurement doesn't explain that decision.
- `data_resolution_m` is how finely the data places the ball (real matches: about 10 cm). If the value is
  within that of the line, the data can't settle the call: say the data puts the ball on the line, too
  close to call from this data, and that the officials decided from the camera images.
- Compare the evidence with `final`. If they point the same way, explain why the decision is right. If they
  don't, say plainly that the measurement doesn't explain the decision. Never defend a call the evidence
  doesn't support, and never judge the officials as people."""

PERSONA = {
    "fan": "Rewrite it for a casual fan watching on their phone: two short sentences, friendly, no jargon.",
    "analyst": "Rewrite it for a TV analyst: precise, lead with the measurement and the law, two sentences.",
    "es": "Rewrite it in Spanish for a Spanish-speaking fan: two short sentences, natural Spanish football "
    "language. Keep every number and name exactly as given. The Laws of the Game are the Reglas: a law the "
    "text cites keeps its number (Law N is Regla N, never Ley N), and cite no other. 'Fuera de juego' means "
    "offside and only offside: a ball out of play 'salió' or went 'fuera del terreno de juego'.",
    "officials": "Rewrite it as Ref Buddy, an assistant briefing the referee: the law, the evidence, and "
    "what the evidence supports, in two sentences. End by saying the decision is the referee's.",
}
PERSONA_RULES = """You rewrite a verified VAR explanation for one audience.
- Keep its meaning exactly: what the law says, what the evidence shows, what was decided, and whether the
  evidence supports the decision.
- Add no facts: every number, law and name must already be in the text. Don't change a number (you may
  leave one out). No player IDs, no field names, no headings, no bullet points.
- Write it as your own words to the audience: never mention the explanation, a rewrite or a fact check."""
EDITOR = """You are the desk editor. You compare a rewrite with the verified explanation it came from.
Answer SAME if the rewrite says the same decision, the same evidence direction (beyond/behind, in/out, on the
line, touched/never touched the ball) and the same verdict on whether the evidence supports the decision.
Otherwise answer DIFFERENT followed by one short sentence saying what changed. Wording, length, language and
leaving details out don't matter. Reply with SAME, or DIFFERENT: <reason>, nothing else."""
ENGINE_TEXT = {"fan": "fan", "analyst": "analyst", "officials": "officials", "es": "fan"}  # fallbacks
OFFSIDE_ES = re.compile(r"\bfuera de juego\b", re.IGNORECASE)
LEY_ES = re.compile(r"\bLey(?:es)?\s+\d", re.IGNORECASE)


def wrong_terms(audience: str, kind: str, text: str) -> list[str]:
    """Football words that mean something else: the Editor reads past them, so code checks. In Spanish
    'fuera de juego' is offside, so a ball out of play said that way tells the fan the wrong call."""
    if audience != "es":
        return []
    out = []
    if kind != "offside" and OFFSIDE_ES.search(text):
        out.append("'fuera de juego' means offside: for a ball out of play say 'salió' or 'fuera del terreno de juego'")
    if LEY_ES.search(text):
        out.append("the Laws of the Game are 'Reglas' in Spanish: write 'Regla N', not 'Ley N'")
    return out


def new_laws(base: str, text: str, kind: str) -> list[str]:
    """A law the rewrite cites that is neither in the verified text nor the law for this kind of call. The
    Verifier can't see it when the law summaries mention that law in passing ("Regla 9" pasted into an offside
    call, whose summary names Law 9)."""

    def laws(s: str) -> set[int]:
        return {int(n) for m in LAW_REF_RE.finditer(s) for n in re.findall(r"\d+", m.group(0))}

    own = (law_summary(kind) or {}).get("law", "")
    extra = sorted(laws(text) - laws(base) - laws(own))
    return [f"cites Law {', '.join(map(str, extra))}, which isn't the law for this call"] if extra else []


# ── Messages ─────────────────────────────────────────────────────────────────


@dataclass
class Call:
    incident_id: str
    kind: str
    state: str
    engine: dict[str, str]  # the engine's own text per audience: the fallback
    plant: bool = False


@dataclass
class Draft:
    call: Call
    text: str
    attempts: int


@dataclass
class Verified:
    call: Call
    text: str
    origin: str  # "agent" | "engine"


@dataclass
class Variant:
    audience: str
    text: str
    origin: str
    attempts: int
    ms: int


@dataclass
class Trace:
    steps: list[dict[str, Any]] = field(default_factory=list)

    def add(self, **step: Any) -> None:
        self.steps.append({"t_ms": int((time.perf_counter() - self.t0) * 1000), **step})

    def __post_init__(self) -> None:
        self.t0 = time.perf_counter()


# ── The store the agents read, and what they read from it ────────────────────


class RecordingStore(MatchStore):
    """Remembers every tool result (the Verifier checks against exactly what the agent read) and hides the
    engine's own wording from the agent."""

    def __init__(self, inner: MatchStore) -> None:
        self.__dict__.update(inner.__dict__)
        self.packet: list[dict] = []

    def _rec(self, out):
        if out is not None:
            self.packet.append(out)
        return out

    def incident(self, incident_id):
        return self._rec(super().incident(incident_id))

    def open_check(self):
        return self._rec(super().open_check())

    def replay(self, incident_id):
        out = super().replay(incident_id)
        if out is not None:
            out = {k: v for k, v in out.items() if k != "status"}  # the agent writes its own words
        return self._rec(out)


def serve(store: MatchStore) -> tuple[uvicorn.Server, int]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(build_server(store).streamable_http_app(), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, port


def _tool_calls(result) -> list[dict[str, Any]]:
    calls = []
    for msg in result.messages:
        for c in msg.contents:
            if getattr(c, "type", "") == "function_call":
                args = getattr(c, "arguments", None)
                calls.append({"tool": c.name, "args": args if isinstance(args, dict) else str(args or "")[:120]})
    return calls


def _plant(text: str) -> str:
    """Change a measured number in the draft (one with a unit, never a law's number), or add one: a fabricated
    statistic for the Verifier to catch."""
    m = re.search(r"(?<![Ll]aw )(?<![Ll]aws )\b(\d+(?:\.\d+)?)(?=\s*(?:cm|m\b|in\b|km/h|%|metres|centimetres))", text)
    if m:
        wrong = f"{float(m.group(1)) + 7:g}"
        return text[: m.start(1)] + wrong + text[m.end(1) :]
    return text + " The margin was 41 cm."


# ── Executors ────────────────────────────────────────────────────────────────


class DecisionDesk(Executor):
    """Agent: reads the call over MCP and writes the explanation. Also handles the Verifier's one retry."""

    def __init__(self, agent, store: RecordingStore, trace: Trace) -> None:
        super().__init__(id="decision_desk")
        self.agent, self.store, self.trace = agent, store, trace

    async def write(self, call: Call, reasons: list[str] | None = None) -> tuple[str, list[dict], int]:
        ask = f"Explain the decision on {call.incident_id} to a casual fan."
        if reasons:
            ask += f" Your last draft was rejected by the fact checker: {'; '.join(reasons)}. Fix it."
        t0 = time.perf_counter()
        result = await self.agent.run(ask)
        return result.text.strip(), _tool_calls(result), int((time.perf_counter() - t0) * 1000)

    @handler
    async def run(self, call: Call, ctx: WorkflowContext[Draft]) -> None:
        text, calls, ms = await self.write(call)
        self.trace.add(step="decision_desk", agent="Decision Desk", tools=calls, ms=ms, text=text)
        if call.plant:
            planted = _plant(text)
            self.trace.add(step="planted", note="a wrong number planted in the draft, to test the Verifier",
                           text=planted)  # fmt: skip
            text = planted
        await ctx.send_message(Draft(call, text, 1))


class Verifier(Executor):
    """Code, not a model: every number and ID must be in what the agent read. One retry, then the engine."""

    def __init__(self, desk: DecisionDesk, trace: Trace) -> None:
        super().__init__(id="verifier")
        self.desk, self.trace = desk, trace

    @handler
    async def run(self, draft: Draft, ctx: WorkflowContext[Verified]) -> None:
        call = draft.call
        bad = [str(v) for v in validate_text(draft.text, self.desk.store.packet, call.state)]
        self.trace.add(step="verify", target="Decision Desk", attempt=draft.attempts, passed=not bad, reasons=bad)
        text, origin = draft.text, "agent"
        if bad:
            text, calls, ms = await self.desk.write(call, bad)
            self.trace.add(step="decision_desk", agent="Decision Desk", retry=True, tools=calls, ms=ms, text=text)
            bad = [str(v) for v in validate_text(text, self.desk.store.packet, call.state)]
            self.trace.add(step="verify", target="Decision Desk", attempt=2, passed=not bad, reasons=bad)
            if bad:
                text, origin = call.engine["fan"], "engine"
                self.trace.add(step="fallback", target="Decision Desk", note="the engine's own explanation airs")
        await ctx.send_message(Verified(call, text, origin))


class Persona(Executor):
    """Agent: rewrites the verified explanation for one audience. Runs concurrently with the others."""

    def __init__(self, audience: str, agent, editor, store: RecordingStore, trace: Trace) -> None:
        super().__init__(id=f"persona_{audience}")
        self.audience, self.agent, self.editor, self.store, self.trace = audience, agent, editor, store, trace

    @handler
    async def run(self, base: Verified, ctx: WorkflowContext[Variant]) -> None:
        call, t0 = base.call, time.perf_counter()
        # Only the verified text: personas re-word it, they don't re-read the raw facts (and can't misread them).
        ask = f"{PERSONA[self.audience]}\n\nVerified explanation:\n{base.text}"
        text, attempts, origin, bad = "", 0, "agent", ["not run"]
        for attempts in (1, 2):
            result = await self.agent.run(ask if attempts == 1 else f"{ask}\n\nRejected: {'; '.join(bad)}. Fix it.")
            text = result.text.strip()
            # Code first: every number, law and ID must be in what the desk read.
            bad = [str(v) for v in validate_text(text, self.store.packet, call.state)]
            bad += wrong_terms(self.audience, call.kind, text) + new_laws(base.text, text, call.kind)
            self.trace.add(step="verify", target=self.audience, attempt=attempts, passed=not bad, reasons=bad)
            if bad:
                continue
            # Then the Editor agent: does it still say the same thing? Code can't tell "in" from "out".
            verdict = (await self.editor.run(f"Verified explanation:\n{base.text}\n\nRewrite:\n{text}")).text.strip()
            same = verdict.upper().startswith("SAME")
            self.trace.add(step="edit", target=self.audience, attempt=attempts, passed=same,
                           reasons=[] if same else [verdict])  # fmt: skip
            if same:
                break
            bad = [f"the meaning changed: {verdict}"]
        if bad:
            text, origin = call.engine[ENGINE_TEXT[self.audience]], "engine"
            self.trace.add(step="fallback", target=self.audience, note="the engine's own text airs")
        ms = int((time.perf_counter() - t0) * 1000)
        self.trace.add(step="persona", agent=self.audience, ms=ms, origin=origin)
        await ctx.send_message(Variant(self.audience, text, origin, attempts, ms))


class Publisher(Executor):
    """Fan-in: one bundle per call, every audience, with the base text and where each came from."""

    def __init__(self, trace: Trace) -> None:
        super().__init__(id="publisher")
        self.trace = trace

    @handler
    async def run(self, variants: list[Variant], ctx: WorkflowContext[Never, dict]) -> None:
        self.trace.add(step="publish", audiences=[v.audience for v in variants])
        await ctx.yield_output({v.audience: v.__dict__ for v in variants})


# ── Running it ───────────────────────────────────────────────────────────────


def build(client: FoundryChatClient, mcp: MCPStreamableHTTPTool, store: RecordingStore, trace: Trace):
    desk_agent = client.as_agent(name="DecisionDesk", instructions=DESK, tools=[mcp],
                                 default_options={"reasoning": {"effort": "low"}})  # fmt: skip
    persona_agent = client.as_agent(name="Persona", instructions=PERSONA_RULES,
                                    default_options={"reasoning": {"effort": "minimal"}})  # fmt: skip
    editor_agent = client.as_agent(name="Editor", instructions=EDITOR,
                                   default_options={"reasoning": {"effort": "low"}})  # fmt: skip
    return graph(desk_agent, persona_agent, editor_agent, store, trace)


def graph(desk_agent, persona_agent, editor_agent, store: RecordingStore, trace: Trace):
    """The workflow itself, for any three agents (tests pass stand-ins)."""
    desk = DecisionDesk(desk_agent, store, trace)
    verifier = Verifier(desk, trace)
    personas = [Persona(a, persona_agent, editor_agent, store, trace) for a in AUDIENCES]
    publisher = Publisher(trace)
    return (
        WorkflowBuilder(start_executor=desk, name="var-desk")
        .add_edge(desk, verifier)
        .add_fan_out_edges(verifier, personas)
        .add_fan_in_edges(personas, publisher)
        .build()
    )


def var_calls(store: MatchStore) -> list[Call]:
    """Every call VAR looked at, once decided, with the engine's own text for each audience."""
    out = []
    for iid, inc in store.engine.desk.incidents.items():
        after = store.engine.replays.get(iid, {}).get("after")
        if inc.var_outcome is None or after is None:
            continue
        out.append(Call(iid, inc.kind, inc.state, dict(after.status)))
    return out


def load(name: str) -> MatchStore:
    return MatchStore.from_real(name) if name.startswith("real_") else MatchStore.from_scenario(name)


async def run_match(name: str, plant: set[str]) -> Path:
    full = load(name)
    calls = var_calls(full)
    results: dict[str, Any] = {}
    async with credential() as cred:
        client = FoundryChatClient(project_endpoint=setting("FOUNDRY_PROJECT_ENDPOINT"),
                                   model=setting("FOUNDRY_MODEL"), credential=cred)  # fmt: skip
        for n, call in enumerate(calls):
            call.plant = call.incident_id in plant or ("first" in plant and n == 0)
            store = RecordingStore(full)
            trace = Trace()
            server, port = serve(store)
            try:
                async with MCPStreamableHTTPTool(name="var-narrative", url=f"http://127.0.0.1:{port}/mcp") as mcp:
                    workflow = build(client, mcp, store, trace)
                    t0 = time.perf_counter()
                    run = await workflow.run(call)
                    bundle = run.get_outputs()[0]
            finally:
                server.should_exit = True
            total = int((time.perf_counter() - t0) * 1000)
            results[call.incident_id] = {"kind": call.kind, "variants": bundle, "trace": trace.steps,
                                         "planted": call.plant, "total_ms": total}  # fmt: skip
            passed = sum(1 for s in trace.steps if s["step"] == "verify" and s["passed"])
            checks = sum(1 for s in trace.steps if s["step"] == "verify")
            print(f"{call.incident_id} {call.kind}: {total / 1000:.1f}s, {passed}/{checks} checks passed"
                  f"{', planted' if call.plant else ''}")  # fmt: skip
    out = OUT_DIR / f"desk_{name}.json"
    out.write_text(json.dumps({"match": name, "model": setting("FOUNDRY_MODEL"), "calls": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")  # fmt: skip
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m orchestrator.desk")
    ap.add_argument("matches", nargs="+")
    ap.add_argument("--plant", nargs="*", default=[],
                    help="incident ids (or 'first') whose first draft gets a wrong number")  # fmt: skip
    args = ap.parse_args()
    for name in args.matches:
        print(f"wrote {asyncio.run(run_match(name, set(args.plant)))}")


if __name__ == "__main__":
    main()
