"""Run the MCP server over a scenario replayed up to a moment.

uv run python -m mcp_server --scenario golden --until 2H78:45 --port 8811
"""

import argparse
import re

import uvicorn

from mcp_server.server import build_server
from mcp_server.store import MatchStore
from sim.scenario import scenario_names


def parse_until(text: str | None) -> tuple[int, float] | None:
    """'2H78:45' -> (2, 4725.0). Minutes are match minutes (the second half starts at 45:00)."""
    if not text:
        return None
    m = re.fullmatch(r"([12])H(\d{1,3}):([0-5]\d)", text)
    if not m:
        raise SystemExit("--until looks like 2H78:45")
    return int(m.group(1)), int(m.group(2)) * 60 + int(m.group(3)) + 0.0


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m mcp_server")
    ap.add_argument("--scenario", default="golden", choices=scenario_names())
    ap.add_argument("--until", help="replay the match up to this moment, e.g. 2H78:45 (default: full match)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8811)
    args = ap.parse_args()
    store = MatchStore.from_scenario(args.scenario, parse_until(args.until))
    print(f"{args.scenario} at {store.now()['clock_text']}: {len(store.engine.desk.incidents)} incidents")
    uvicorn.run(
        build_server(store).streamable_http_app(), host=args.host, port=args.port, log_level="warning"
    )


if __name__ == "__main__":
    main()
