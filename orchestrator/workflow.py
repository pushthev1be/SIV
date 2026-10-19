"""VAR Narrative orchestrator as a Microsoft Agent Framework workflow (sketch).

Graph, one run per aired moment:

    route ──(switch)──> explainer ─────┐
          │                            ├─> validate ──> personas fan-out ──> fan-in ──> gate ──> cue
          └────────────> decision_desk ┘     (retry once, then template)                 (HITL hold)

Agents only turn facts into words. Every fact comes from the engine through the MCP
server, and nothing leaves `gate` without passing `validate`.

API checked against Microsoft Learn (Oct 2026): WorkflowBuilder(start_executor=...),
add_edge, add_switch_case_edge_group + Case/Default, add_fan_in_edges, ctx.request_info
+ @response_handler, FoundryChatClient.as_agent, MCPStreamableHTTPTool.
`add_fan_out_edges` is assumed to mirror add_fan_in_edges; confirm it on day one
against the installed agent-framework version.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Never

from agent_framework import (
    Case,
    Default,
    Executor,
    MCPStreamableHTTPTool,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import DefaultAzureCredential

from orchestrator.templates import template_text  # deterministic fallbacks
from orchestrator.validator import validate_text  # returns list of violations

AGENT_TIMEOUT_S = 2.0
AUDIENCES = ("casual", "analyst", "ref_nerd", "es")


# ── Messages passed along edges ──────────────────────────────────────────────


@dataclass
class Moment:
    moment_id: str
    kind: str  # "speed" | "pressure" | "goal" | "card" | "var_check" | "var_decision" ...
    fact_packet: dict  # the only facts an agent may cite
    sources: list[str]
    incident_state: str | None = None  # "open" | "reviewing" | "decided" | None


@dataclass
class Draft:
    moment: Moment
    text: str
    origin: str  # "agent" | "template"
    attempts: int = 1


@dataclass
class Validated:
    moment: Moment
    text: str
    origin: str


@dataclass
class Variant:
    moment: Moment
    audience: str
    text: str
    origin: str


@dataclass
class CueBundle:
    moment: Moment
    variants: dict[str, Variant] = field(default_factory=dict)


@dataclass
class HoldRequest:
    """Sent to the producer console; the reply is "approve" or "kill"."""

    bundle: CueBundle
    reason: str


# ── Executors ────────────────────────────────────────────────────────────────


class Route(Executor):
    """Entry point: send VAR incidents to the Decision Desk, everything else to the Explainer."""

    def __init__(self) -> None:
        super().__init__(id="route")

    @handler
    async def run(self, moment: Moment, ctx: WorkflowContext[Moment]) -> None:
        await ctx.send_message(moment)


class Writer(Executor):
    """Wrap one agent: call it with the fact packet; on timeout, fall back to the template."""

    def __init__(self, id: str, agent) -> None:
        super().__init__(id=id)
        self._agent = agent

    @handler
    async def run(self, moment: Moment, ctx: WorkflowContext[Draft]) -> None:
        await ctx.send_message(await self._write(moment, violations=None, attempts=1))

    async def _write(self, moment: Moment, violations: list[str] | None, attempts: int) -> Draft:
        prompt = _prompt(moment, violations)
        try:
            result = await asyncio.wait_for(self._agent.run(prompt), AGENT_TIMEOUT_S)
            return Draft(moment, result.text, "agent", attempts)
        except (TimeoutError, Exception):  # noqa: BLE001 – the overlay must never go blank
            return Draft(moment, template_text(moment.kind, moment.fact_packet), "template", attempts)


class Validate(Executor):
    """Deterministic grounding check. Miss once: one retry naming the violation. Miss twice: template."""

    def __init__(self, writers: dict[str, Writer]) -> None:
        super().__init__(id="validate")
        self._writers = writers

    @handler
    async def run(self, draft: Draft, ctx: WorkflowContext[Validated]) -> None:
        violations = validate_text(draft.text, draft.moment.fact_packet, draft.moment.incident_state)
        if violations and draft.origin == "agent" and draft.attempts == 1:
            writer = self._writers[_writer_for(draft.moment)]
            draft = await writer._write(draft.moment, violations, attempts=2)
            violations = validate_text(draft.text, draft.moment.fact_packet, draft.moment.incident_state)
        if violations:
            draft = Draft(
                draft.moment, template_text(draft.moment.kind, draft.moment.fact_packet), "template"
            )
        await ctx.send_message(Validated(draft.moment, draft.text, draft.origin))


class Persona(Executor):
    """One per audience; these run concurrently in the fan-out. Output is re-validated."""

    def __init__(self, audience: str, agent) -> None:
        super().__init__(id=f"persona_{audience}")
        self._audience = audience
        self._agent = agent

    @handler
    async def run(self, base: Validated, ctx: WorkflowContext[Variant]) -> None:
        text, origin = base.text, base.origin  # fallback: untranslated base text
        if base.origin == "agent":
            try:
                result = await asyncio.wait_for(
                    self._agent.run(f"Audience: {self._audience}\nRewrite, adding no facts:\n{base.text}"),
                    AGENT_TIMEOUT_S,
                )
                if not validate_text(result.text, base.moment.fact_packet, base.moment.incident_state):
                    text, origin = result.text, "agent"
            except (TimeoutError, Exception):  # noqa: BLE001
                pass
        await ctx.send_message(Variant(base.moment, self._audience, text, origin))


class Gate(Executor):
    """Fan-in point plus producer console. Auto-airs normal cues; holds sensitive ones for a human."""

    def __init__(self) -> None:
        super().__init__(id="gate")

    @handler
    async def run(self, variants: list[Variant], ctx: WorkflowContext[Never, CueBundle]) -> None:
        bundle = CueBundle(variants[0].moment, {v.audience: v for v in variants})
        if bundle.moment.kind in {"var_decision", "card"}:
            await ctx.request_info(request_data=HoldRequest(bundle, "decision cue"), response_type=str)
            return
        await ctx.yield_output(bundle)

    @response_handler
    async def on_console(
        self, original_request: HoldRequest, response: str, ctx: WorkflowContext[Never, CueBundle]
    ) -> None:
        if response == "approve":
            await ctx.yield_output(original_request.bundle)
        # "kill": nothing airs; the console API writes the audit log entry


# ── Wiring ───────────────────────────────────────────────────────────────────


def build_workflow(client: FoundryChatClient, strong_model: str, mcp: MCPStreamableHTTPTool):
    explainer = client.as_agent(
        name="Explainer",
        instructions=_load_prompt("explainer"),
        tools=[mcp],
        # model override per agent: strong model for explainer / decision desk
    )
    decision_desk = client.as_agent(
        name="DecisionDesk", instructions=_load_prompt("decision_desk"), tools=[mcp]
    )
    persona_agent = client.as_agent(name="Persona", instructions=_load_prompt("persona"))

    route = Route()
    writers = {
        "explainer": Writer("explainer", explainer),
        "decision_desk": Writer("decision_desk", decision_desk),
    }
    validate = Validate(writers)
    personas = [Persona(a, persona_agent) for a in AUDIENCES]
    gate = Gate()

    return (
        WorkflowBuilder(start_executor=route)
        .add_switch_case_edge_group(
            route,
            [
                Case(condition=lambda m: m.kind.startswith("var_"), target=writers["decision_desk"]),
                Default(target=writers["explainer"]),
            ],
        )
        .add_edge(writers["explainer"], validate)
        .add_edge(writers["decision_desk"], validate)
        .add_fan_out_edges(validate, personas)
        .add_fan_in_edges(personas, gate)
        .build()
    )


async def air_moment(workflow, moment: Moment, console) -> CueBundle | None:
    """Run one moment. Workflow runs keep state between calls, so build one workflow per moment
    (cheap) or reset it. Held cues wait on the producer console, so other moments keep flowing."""
    pending: dict[str, str] = {}
    stream = workflow.run(moment, stream=True)
    while True:
        bundle = None
        async for event in stream:
            if event.type == "request_info":
                pending[event.request_id] = await console.decide(event.data)  # "approve" | "kill"
            elif event.type == "output":
                bundle = event.data
        if not pending:
            return bundle
        stream, pending = workflow.run(stream=True, responses=pending), {}


async def main() -> None:
    async with DefaultAzureCredential() as credential:
        client = FoundryChatClient(
            project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            model=os.environ["FOUNDRY_MODEL_SMALL"],
            credential=credential,
        )
        async with MCPStreamableHTTPTool(name="var-narrative", url=os.environ["MCP_URL"]) as mcp:
            # TODO(Oct 22): subscribe to engine output; for each detected moment m:
            #   await air_moment(build_workflow(client, strong_model, mcp), m, console)
            raise NotImplementedError((client, mcp))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _writer_for(moment: Moment) -> str:
    return "decision_desk" if moment.kind.startswith("var_") else "explainer"


def _prompt(moment: Moment, violations: list[str] | None) -> str:
    fix = (
        f"\nYour last draft was rejected: {'; '.join(violations)}. Use only the facts given."
        if violations
        else ""
    )
    head = f"Moment {moment.moment_id} ({moment.kind})."
    return f"{head} Facts: {moment.fact_packet}. Cite: {moment.sources}.{fix}"


def _load_prompt(name: str) -> str:
    with open(
        os.path.join(os.path.dirname(__file__), "..", "agents", "prompts", f"{name}.md"), encoding="utf-8"
    ) as f:
        return f.read()
