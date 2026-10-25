"""The MCP server: read-only tools over match state, for the agents.

    uv run python -m mcp_server --scenario golden --until 2H78:45 --port 8811
    -> streamable HTTP at http://127.0.0.1:8811/mcp

Every tool result carries `sources` (event and incident IDs) so the grounding validator can check
agent text against exactly what the agent read. No tool can change match state.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server.laws import LAWS, law_summary
from mcp_server.store import MatchStore

INSTRUCTIONS = (
    "Read-only match state for SIV. Use get_open_check or get_incident for VAR calls, "
    "get_replay for who was involved and the evidence, get_law_summary for which law applies. "
    "While a check is in progress, incident facts are withheld: describe what is being checked, "
    "never the outcome."
)


def build_server(store: MatchStore) -> FastMCP:
    mcp = FastMCP("var-narrative", instructions=INSTRUCTIONS)

    @mcp.tool()
    def get_open_check() -> dict[str, Any]:
        """The VAR check currently in progress, if any. Its facts stay hidden until the decision."""
        return store.open_check() or {"open_check": None, "now": store.now(), "sources": []}

    @mcp.tool()
    def get_incident(incident_id: str) -> dict[str, Any]:
        """One incident (a call): on-field call, state, reason codes, the players involved and, once
        decided, the VAR outcome, final decision and measured facts."""
        return store.incident(incident_id) or {"error": f"no incident {incident_id}", "sources": []}

    @mcp.tool()
    def get_incidents(state: str | None = None) -> dict[str, Any]:
        """All incidents so far, optionally filtered by state: open, reviewing, decided, restarted."""
        rows = store.incidents(state)
        return {"incidents": rows, "sources": [s for r in rows for s in r["sources"]]}

    @mcp.tool()
    def get_replay(incident_id: str) -> dict[str, Any]:
        """The call replay summary: the key moment, players involved with their speed at that moment,
        status text for each audience and, after the decision only, the measurement."""
        return store.replay(incident_id) or {"error": f"no replay for {incident_id}", "sources": []}

    @mcp.tool()
    def get_recent_events(seconds: float = 30.0, types: list[str] | None = None) -> dict[str, Any]:
        """On-ball events from the last `seconds` of match time, optionally filtered by type."""
        return store.recent_events(min(max(seconds, 1.0), 600.0), types)

    @mcp.tool()
    def get_rolling_metrics() -> dict[str, Any]:
        """Score, match clock and five-minute rolling possession, control, pressure and chaos."""
        return store.rolling_metrics()

    @mcp.tool()
    def get_player_stats(player_id: str) -> dict[str, Any]:
        """A player's distance, top speed, action counts and the calls they were part of."""
        return store.player_stats(player_id) or {"error": f"no player {player_id}", "sources": []}

    @mcp.tool()
    def get_law_summary(topic: str) -> dict[str, Any]:
        """A plain-language summary of the law behind a call: offside, penalty, red_card, handball or
        var_protocol."""
        found = law_summary(topic)
        return found or {"error": f"unknown topic {topic}", "topics": sorted(LAWS), "sources": []}

    return mcp
