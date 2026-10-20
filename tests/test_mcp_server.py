import asyncio
import functools
import json
import socket
import threading
import time

import pytest
import uvicorn

from mcp_server.server import build_server
from mcp_server.store import MatchStore
from orchestrator.validator import validate_text


@functools.cache
def golden_full() -> MatchStore:
    return MatchStore.from_scenario("golden")


def offside_iid(store: MatchStore) -> str:
    return next(i.incident_id for i in store.engine.desk.incidents.values() if i.var_outcome == "offside")


@functools.cache
def during_store() -> tuple[MatchStore, str]:
    """The golden match paused while the 0.31 m offside check is open."""
    full = golden_full()
    inc = full.engine.desk.incidents[offside_iid(full)]
    store = MatchStore.from_scenario("golden", (2, inc.opened_at + 10))
    return store, inc.incident_id


def call(store: MatchStore, tool: str, **args):
    content, _ = asyncio.run(build_server(store).call_tool(tool, args))
    return json.loads(content[0].text)


def test_every_tool_is_read_only_and_cites_sources():
    store = golden_full()
    iid = offside_iid(store)
    for tool, args in [
        ("get_incident", {"incident_id": iid}),
        ("get_incidents", {}),
        ("get_replay", {"incident_id": iid}),
        ("get_recent_events", {"seconds": 120}),
        ("get_rolling_metrics", {}),
        ("get_player_stats", {"player_id": "NBR-9"}),
        ("get_open_check", {}),
    ]:
        assert "sources" in call(store, tool, **args), tool


def test_during_the_check_the_tools_withhold_the_outcome():
    store, iid = during_store()
    check = call(store, "get_open_check")
    assert check["incident_id"] == iid and check["state"] == "reviewing"
    assert check["facts"] == {} and check["final"] is None and check["var_outcome"] is None
    replay = call(store, "get_replay", incident_id=iid)
    assert replay["phase"] == "during" and "measurement" not in replay
    assert {r["role"] for r in check["involved"]} == {"attacker", "defender", "passer"}


def test_after_the_decision_the_measurement_is_available():
    store = golden_full()
    iid = offside_iid(store)
    inc = call(store, "get_incident", incident_id=iid)
    assert inc["final"] == "no_goal" and inc["facts"]["margin_m"] == 0.31
    replay = call(store, "get_replay", incident_id=iid)
    assert replay["phase"] == "after" and replay["measurement"]["margin_m"] == 0.31
    assert replay["names"]["NBR-9"] == "Okafor"


def test_engine_templates_pass_the_validator():
    """Our own deterministic text must be grounded; agents are held to the same bar."""
    store, iid = during_store()
    packet = {
        "incident": call(store, "get_incident", incident_id=iid),
        "replay": call(store, "get_replay", incident_id=iid),
    }
    for audience, text in packet["replay"]["status"].items():
        assert validate_text(text, packet, "reviewing") == [], audience
    full = golden_full()
    for inc in full.engine.desk.incidents.values():
        if inc.state not in ("decided", "restarted") or inc.incident_id not in full.engine.replays:
            continue
        packet = {"incident": call(full, "get_incident", incident_id=inc.incident_id),
                  "replay": call(full, "get_replay", incident_id=inc.incident_id)}  # fmt: skip
        for audience, text in packet["replay"]["status"].items():
            assert validate_text(text, packet, inc.state) == [], (inc.incident_id, audience, text)


def test_recent_events_skip_tracking_and_name_players():
    out = call(golden_full(), "get_recent_events", seconds=300)
    assert out["events"] and all(e["type"] != "tracking" for e in out["events"])
    assert all(pid in out["names"] for e in out["events"] for pid in (e["player"], e["receiver"]) if pid)


@pytest.mark.parametrize("topic", ["offside", "Law 11", "penalty", "red card", "var"])
def test_law_summaries(topic):
    out = call(golden_full(), "get_law_summary", topic=topic)
    assert out["summary"] and out["law"]


def test_unknown_lookups_return_errors_not_exceptions():
    store = golden_full()
    assert "error" in call(store, "get_incident", incident_id="inc-m01-99")
    assert "error" in call(store, "get_law_summary", topic="throw-ins")


def test_agent_framework_client_over_http():
    """The real transport: Agent Framework's MCP client lists and calls the tools over streamable HTTP."""
    from agent_framework import MCPStreamableHTTPTool

    store, iid = during_store()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(build_server(store).streamable_http_app(), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)

    async def go():
        async with MCPStreamableHTTPTool(name="var-narrative", url=f"http://127.0.0.1:{port}/mcp") as tool:
            names = {f.name for f in tool.functions}
            fn = next(f for f in tool.functions if f.name == "get_incident")
            result = await fn.invoke(incident_id=iid)
            return names, result

    try:
        names, result = asyncio.run(go())
    finally:
        server.should_exit = True
    assert {"get_incident", "get_open_check", "get_replay", "get_law_summary"} <= names
    assert iid in str(result[0].text if hasattr(result[0], "text") else result[0])
