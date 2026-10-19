"""Stream a simulated match into Event Hubs in scaled real time.

    uv run python -m sim.producer golden                # real time (about 95 minutes)
    uv run python -m sim.producer golden --speed 60     # a minute of match per second

All events share the match ID as partition key, so the engine reads them in order.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from azure.eventhub import EventData
from azure.eventhub.aio import EventHubProducerClient
from azure.eventhub.exceptions import EventDataSendError

from config import credential, setting
from contracts import MatchEvent
from sim.scenario import load_scenario, scenario_names
from sim.simulator import Simulator

HALF_TIME_PAUSE_S = 2.0  # wall-clock pause between periods, whatever the speed


def schedule(events: list[MatchEvent], speed: float) -> list[tuple[float, MatchEvent]]:
    """Wall-clock offset (seconds from start) at which each event should be sent."""
    out, wall, prev = [], 0.0, None
    for e in events:
        if prev is not None:
            wall += HALF_TIME_PAUSE_S if e.period != prev.period else max(0.0, e.clock - prev.clock) / speed
        out.append((wall, e))
        prev = e
    return out


async def stream(events: list[MatchEvent], speed: float, *, quiet: bool = False) -> int:
    match_id = events[0].match_id
    plan = schedule(events, speed)
    sent = 0
    async with credential() as cred:
        producer = EventHubProducerClient(
            fully_qualified_namespace=setting("EVENTHUB_NAMESPACE"),
            eventhub_name=setting("EVENTHUB_NAME"),
            credential=cred,
        )
        async with producer:
            start = time.monotonic()
            i = 0
            while i < len(plan):
                due, _ = plan[i]
                delay = due - (time.monotonic() - start)
                if delay > 0:
                    await asyncio.sleep(delay)
                # Everything already due goes out in one batch.
                now = time.monotonic() - start
                batch = await producer.create_batch(partition_key=match_id)
                while i < len(plan) and plan[i][0] <= now:
                    data = EventData(plan[i][1].model_dump_json(exclude_none=True))
                    data.properties = {"type": plan[i][1].type, "match_id": match_id}
                    try:
                        batch.add(data)
                    except ValueError:  # batch full: send it and start another
                        await producer.send_batch(batch)
                        batch = await producer.create_batch(partition_key=match_id)
                        batch.add(data)
                    i += 1
                    sent += 1
                try:
                    await producer.send_batch(batch)
                except EventDataSendError:
                    raise
                if not quiet and plan[i - 1][1].type != "tracking":
                    e = plan[i - 1][1]
                    print(f"{e.period}H {e.clock / 60:5.1f}'  sent {sent:6d}  last {e.type}")
    return sent


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sim.producer")
    ap.add_argument("scenario", choices=scenario_names())
    ap.add_argument("--speed", type=float, default=1.0, help="match seconds per wall second")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    events = Simulator(load_scenario(args.scenario)).run()
    t0 = time.perf_counter()
    n = asyncio.run(stream(events, args.speed, quiet=args.quiet))
    print(f"sent {n} events from {args.scenario} in {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
