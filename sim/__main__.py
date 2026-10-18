"""Write a scenario's events as JSON Lines.

uv run python -m sim golden                 # to stdout
uv run python -m sim golden -o golden.jsonl
"""

import argparse
import sys

from sim.scenario import load_scenario, scenario_names
from sim.simulator import Simulator


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sim")
    ap.add_argument("scenario", choices=scenario_names())
    ap.add_argument("-o", "--out", help="output .jsonl path (default stdout)")
    args = ap.parse_args()
    events = Simulator(load_scenario(args.scenario)).run()
    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    with out if args.out else open(1, "w", closefd=False) as f:
        for e in events:
            f.write(e.model_dump_json(exclude_none=True) + "\n")


if __name__ == "__main__":
    main()
