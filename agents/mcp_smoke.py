"""Exit check: a Foundry agent reads match state over MCP, and the validator checks what it writes.

    uv run python -m agents.mcp_smoke

Runs the Decision Desk twice on the golden match's offside call: while the check is open, and after
the decision. Every tool result the agent receives is recorded as its fact packet; the agent's text
is validated against exactly that. Then a planted wrong number shows the validator rejecting it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import threading
import time

import uvicorn
from agent_framework import MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient

from config import credential, setting
from mcp_server.server import build_server
from mcp_server.store import MatchStore
from orchestrator.validator import validate_text

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)

INSTRUCTIONS = """You are the Decision Desk for VAR Narrative.
You explain VAR checks and calls to football fans.
Rules:
- Get every fact from your tools (get_open_check, get_incident, get_replay, get_law_summary).
  Never invent a number.
- Use player names from the tools' `names`, not IDs.
- While a check is in progress, say what is being checked and under which law, and why it can take time.
  Never state or hint at the outcome, a margin or any measurement.
- After the decision, state the decision and the key evidence, with the numbers exactly as the tools
  give them (you may convert metres to centimetres or inches).
- Two short sentences, plain English, no headings."""


class RecordingStore(MatchStore):
    """Remembers every tool result, so the validator checks against exactly what the agent read."""

    def __init__(self, inner: MatchStore) -> None:  # wrap an existing store
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
        return self._rec(super().replay(incident_id))


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


async def ask(store: RecordingStore, prompt: str) -> tuple[str, float, list[str]]:
    server, port = serve(store)
    try:
        async with (
            credential() as cred,
            MCPStreamableHTTPTool(name="var-narrative", url=f"http://127.0.0.1:{port}/mcp") as mcp,
        ):
            client = FoundryChatClient(
                project_endpoint=setting("FOUNDRY_PROJECT_ENDPOINT"),
                model=setting("FOUNDRY_MODEL"),
                credential=cred,
            )
            agent = client.as_agent(
                name="DecisionDesk",
                instructions=INSTRUCTIONS,
                tools=[mcp],
                default_options={"reasoning": {"effort": "low"}},
            )
            t0 = time.perf_counter()
            result = await agent.run(prompt)
            calls = [c.name for m in result.messages for c in m.contents if getattr(c, "type", "") == "function_call"]
            return result.text, time.perf_counter() - t0, calls
    finally:
        server.should_exit = True


def report(label: str, text: str, secs: float, calls: list[str], store: RecordingStore, state: str) -> None:
    v = validate_text(text, store.packet, state)
    print(f"\n== {label} ({secs:.1f}s; tools: {', '.join(calls) or 'none'})\n{text}")
    print("validator:", "PASS" if not v else "; ".join(map(str, v)))


async def main() -> None:
    full = MatchStore.from_scenario("golden")
    inc = next(i for i in full.engine.desk.incidents.values() if i.var_outcome == "offside")

    during = RecordingStore(MatchStore.from_scenario("golden", (2, inc.opened_at + 10)))
    text, secs, calls = await ask(during, "A VAR check has just started. Explain it to a casual fan.")
    report("During the check", text, secs, calls, during, "reviewing")

    after = RecordingStore(full)
    text, secs, calls = await ask(after, f"Explain the decision on {inc.incident_id} to a casual fan.")
    report("After the decision", text, secs, calls, after, "decided")

    planted = re.sub(r"\b31\b", "33", text, count=1) if re.search(r"\b31\b", text) else text + " The gap was 33 cm."
    v = validate_text(planted, after.packet, "decided")
    print(f"\n== Planted wrong number\n{planted}\nvalidator:", "; ".join(map(str, v)) or "PASS (unexpected)")


if __name__ == "__main__":
    asyncio.run(main())
