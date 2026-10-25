"""Evaluate SIV and publish the numbers: docs/evaluation.md and web/preview/data/eval.json.

    uv run python scripts/evaluate.py            # about five minutes; no model calls
    uv run python scripts/evaluate.py --seeds 3  # quicker

1. Measurement accuracy. In a simulated match the truth is known: the script sets every margin. Across many
   seeds, the engine's measurement is compared with it, and with the call it implies.
2. Catching VAR errors. Across seeds of the real-error scenario, how many wrong decisions are flagged, and
   how many correct calls (in every scenario) are flagged by mistake.
3. Fact-checker red team. Every explanation the engine writes, with errors planted in it (a changed number,
   a wrong law, an unknown player, a field name, a signed distance, an invented statistic): how many the
   Verifier catches, and how many clean texts it wrongly rejects.
4. The no-spoiler rule. Every replay shown while a check is open, checked for a measurement or a verdict.
5. The agent desk. From the cached workflow traces (orchestrator/desk.py): first-pass rate, recoveries,
   fallbacks, planted errors caught, Editor catches, time per call.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import time
from collections import Counter
from pathlib import Path

from engine.engine import Engine
from engine.laws import law_summary
from mcp_server.store import MatchStore
from orchestrator.validator import LAW_REF_RE, validate_text
from sim.scenario import load_scenario, scenario_names
from sim.simulator import Simulator

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "preview" / "data"
FLAG = "The measurement alone does not explain this decision"
# Script kind -> (incident kind, the script's true value, the engine's measured value)
TRUTH = {
    "offside_goal": ("offside", lambda s: s.margin_m, lambda i: i.facts.get("margin_m")),
    "ball_out_goal": ("ball_out", lambda s: s.over_m, lambda i: i.facts.get("value_m")),
    "goal_line_clearance": ("goal_line", lambda s: s.over_m, lambda i: i.facts.get("value_m")),
    "box_edge_foul": ("penalty_location", lambda s: s.outside_m, lambda i: i.facts.get("value_m")),
    "penalty_retake": ("keeper_encroachment", lambda s: s.off_m, lambda i: i.facts.get("value_m")),
}
TOPIC = {"penalty_location": "penalty_location", "keeper_encroachment": "keeper_encroachment", "foul": "red_card"}


def matched(engine: Engine, script, used: set[str]):
    """The incident a script produced: the first unused one of its kind opened around its time."""
    kind = TRUTH[script.kind][0] if script.kind in TRUTH else "penalty"
    for iid, inc in engine.desk.incidents.items():
        if iid not in used and inc.kind == kind and script.at - 90 <= inc.opened_at <= script.at + 240:
            if inc.var_outcome is not None or kind != "offside":
                used.add(iid)
                return iid, inc
    return None, None


def is_error(script) -> bool | None:
    """Whether the scripted decision is wrong, by its own truth (None: a judgement the engine can't measure)."""
    if script.kind == "offside_goal":
        return (script.margin_m > 0) != (script.decision == "no_goal")
    if script.kind == "penalty_review":
        return not script.played_ball and not script.awarded
    if script.kind in TRUTH:
        return False  # the scripted decision follows the measurement
    return None


def measurement_and_errors(seeds: int) -> dict:
    errors_abs: list[float] = []
    by_kind: dict[str, list[float]] = {}
    direction_wrong = calls = 0
    flags = Counter()
    for name in scenario_names():
        base = load_scenario(name)
        for k in range(seeds):
            sc = base.model_copy(update={"seed": base.seed + 7919 * k})
            eng = Engine(sc.match_id)
            for e in Simulator(sc).run():
                eng.ingest(e)
            used: set[str] = set()
            for script in sorted(sc.script, key=lambda s: s.at):
                if script.kind not in TRUTH and script.kind != "penalty_review":
                    continue
                iid, inc = matched(eng, script, used)
                if inc is None or inc.final is None:
                    flags["missing"] += 1
                    continue
                calls += 1
                if script.kind in TRUTH:
                    truth, measured = TRUTH[script.kind][1](script), TRUTH[script.kind][2](inc)
                    err = abs(measured - truth)
                    errors_abs.append(err)
                    by_kind.setdefault(script.kind, []).append(err)
                    direction_wrong += (measured > 0) != (truth > 0)
                after = eng.replays.get(iid, {}).get("after")
                flagged = after is not None and FLAG in after.status["fan"]
                wrong = is_error(script)
                if wrong is None:
                    continue
                flags[("tp" if flagged else "fn") if wrong else ("fp" if flagged else "tn")] += 1
    return {
        "matches": len(scenario_names()) * seeds,
        "calls": calls,
        "mean_error_cm": round(st.mean(errors_abs) * 100, 2),
        "max_error_cm": round(max(errors_abs) * 100, 2),
        "by_kind_max_cm": {k: round(max(v) * 100, 2) for k, v in by_kind.items()},
        "direction_wrong": direction_wrong,
        "measured": len(errors_abs),
        "errors_flagged": flags["tp"],
        "errors_missed": flags["fn"],
        "false_flags": flags["fp"],
        "correct_unflagged": flags["tn"],
        "missing": flags["missing"],
    }


# ── Red team ─────────────────────────────────────────────────────────────────


def plants(text: str, packet) -> list[tuple[str, str]]:
    """Corrupted versions of a text, each with one error a reader could be misled by."""
    out = []
    nums = [
        m
        for m in re.finditer(
            r"(?<![\w.:+-])(\d+(?:\.\d+)?)(?![\w:'])", LAW_REF_RE.sub(lambda m: "#" * len(m.group(0)), text)
        )
    ]
    if nums:
        m = nums[len(nums) // 2]
        bumped = f"{float(m.group(1)) * 1.37 + 3:.0f}"
        out.append(("changed_number", text[: m.start(1)] + bumped + text[m.end(1) :]))
    law = LAW_REF_RE.search(text)
    if law:
        out.append(("wrong_law", text[: law.start()] + "Law 17" + text[law.end() :]))
    out.append(("unknown_player", text + " NBR-99 was also involved."))
    out.append(("field_name", text + " (var_outcome: upheld)"))
    out.append(("signed_distance", text + " The margin was -0.42 m."))
    out.append(("invented_stat", text + " He was running at 41.3 km/h."))
    return out


def red_team() -> dict:
    caught, planted, clean, clean_bad = Counter(), Counter(), 0, []
    for name in [*scenario_names(), "real_japan_spain", "real_wales_iran"]:
        store = MatchStore.from_real(name) if name.startswith("real_") else MatchStore.from_scenario(name)
        for iid, phases in store.engine.replays.items():
            inc = store.engine.desk.incidents[iid]
            after = phases.get("after")
            if after is None or inc.final is None:
                continue
            law = law_summary(TOPIC.get(inc.kind, inc.kind))
            packet = [store.incident(iid), store.replay(iid), law or {}]
            for audience, text in after.status.items():
                clean += 1
                if validate_text(text, packet, inc.state):
                    clean_bad.append((name, iid, audience))
                for kind, bad in plants(text, packet):
                    planted[kind] += 1
                    caught[kind] += bool(validate_text(bad, packet, inc.state))
    return {
        "texts": clean,
        "clean_rejected": len(clean_bad),
        "planted": sum(planted.values()),
        "caught": sum(caught.values()),
        "by_kind": {k: {"planted": planted[k], "caught": caught[k]} for k in planted},
    }


def spoilers() -> dict:
    """Every during-check replay: no measurement in the package, and no verdict or margin in its words."""
    packages = reveals = 0
    for name in scenario_names():
        store = MatchStore.from_scenario(name)
        for phases in store.engine.replays.values():
            pkg = phases.get("during")
            if pkg is None:
                continue
            packages += 1
            reveals += pkg.lines is not None or pkg.measurement is not None
            for text in pkg.status.values():
                reveals += any(
                    v.kind in ("premature_reveal", "check_complete") for v in validate_text(text, {}, "reviewing")
                )
    return {"during_packages": packages, "reveals": reveals}


def desk() -> dict:
    calls = first_pass = recovered = fallbacks = planted = planted_caught = edits = variants = 0
    times = []
    for path in sorted(DATA.glob("desk_*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        for c in d["calls"].values():
            calls += 1
            times.append(c["total_ms"] / 1000)
            steps = c["trace"]
            desk_checks = [s for s in steps if s["step"] == "verify" and s["target"] == "Decision Desk"]
            if c.get("planted"):
                planted += 1
                planted_caught += bool(desk_checks) and not desk_checks[0]["passed"]
            elif desk_checks and desk_checks[0]["passed"]:
                first_pass += 1
            fallbacks += sum(1 for s in steps if s["step"] == "fallback")
            edits += sum(1 for s in steps if s["step"] == "edit" and not s["passed"])
            for v in c["variants"].values():
                variants += 1
                recovered += v["origin"] == "agent" and v["attempts"] > 1
    unplanted = calls - planted
    return {
        "calls": calls,
        "model": "gpt-5-mini",
        "desk_first_pass": first_pass,
        "desk_unplanted": unplanted,
        "planted": planted,
        "planted_caught": planted_caught,
        "variants": variants,
        "recovered_on_retry": recovered,
        "fallbacks": fallbacks,
        "editor_catches": edits,
        "median_s": round(st.median(times), 1) if times else None,
        "max_s": round(max(times), 1) if times else None,
    }


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def write_markdown(r: dict) -> str:
    m, t, s, d = r["measurement"], r["red_team"], r["spoilers"], r["desk"]
    rows = "\n".join(
        f"| {k.replace('_', ' ')} | {v['planted']} | {v['caught']} | {pct(v['caught'], v['planted'])} |"
        for k, v in t["by_kind"].items()
    )
    kinds = ", ".join(f"{k.replace('_', ' ')} {v} cm" for k, v in m["by_kind_max_cm"].items())
    return f"""# Evaluation

Generated by `scripts/evaluate.py` ({r["generated"]}). Everything here is reproducible: the simulated matches
are seeded, and the agent numbers come from the cached workflow traces in `web/preview/data/desk_*.json`.

## 1. Measurement accuracy

In a simulated match the truth is known: the script sets every margin. Across **{m["matches"]} matches**
({len(scenario_names())} scenarios, several seeds each) the engine measured **{m["measured"]} calls**
(offsides, ball out, goal line, contact at the edge of the area, keeper off his line).

| | |
|---|---|
| Mean error | {m["mean_error_cm"]} cm |
| Largest error | {m["max_error_cm"]} cm ({kinds}) |
| Calls where the measurement pointed the wrong way | {m["direction_wrong"]} of {m["measured"]} |

The errors are rounding: positions are stored to the centimetre or millimetre. Read this as a check that the
measuring code is exact and loses nothing between the feed and the screen, not as real-world accuracy: in a
simulation the engine measures the very positions the script placed. On real data the precision is the data's
own (about 10 cm for event data), and the system says so (see real-matches.md).

## 2. Catching the VAR's mistakes

Across the same matches, every scripted call whose decision is wrong by its own measurement (an offside
given against a player who was onside, a goal allowed though he was off, a penalty refused when the
challenger never touched the ball) should be flagged, and no correct call should be.

| | |
|---|---|
| Wrong decisions flagged | {m["errors_flagged"]} of {m["errors_flagged"] + m["errors_missed"]} ({
        pct(m["errors_flagged"], m["errors_flagged"] + m["errors_missed"])
    }) |
| Correct decisions flagged by mistake | {m["false_flags"]} of {m["false_flags"] + m["correct_unflagged"]} |

Judgement calls with nothing to measure (how severe a challenge was) are left out: the engine can only pass
those on, and says so.

## 3. Fact-checker red team

Every explanation the engine writes, for every audience, in every match (including the two real ones), with
one error planted at a time. The Verifier is plain code (`orchestrator/validator.py`); nothing it rejects can
air.

| Planted error | Planted | Caught | Rate |
|---|---|---|---|
{rows}
| **All** | **{t["planted"]}** | **{t["caught"]}** | **{pct(t["caught"], t["planted"])}** |

Clean texts wrongly rejected: **{t["clean_rejected"]} of {t["texts"]}**.

What it misses, honestly: the Verifier checks that every number is *in the facts*, not that it is attached to
the *right* fact. Every changed number it let through ({
        t["by_kind"].get("changed_number", {}).get("planted", 0)
        - t["by_kind"].get("changed_number", {}).get("caught", 0)
    } here) was swapped for a number that genuinely appears
elsewhere in the same call's facts, such as another player's speed or a clock time. Catching those needs
each claim bound to its fact, which is what the Editor agent's meaning check partly covers for the agents.

## 4. No spoilers while a check is open

{s["during_packages"]} replays shown during a check, across every simulated match: **{s["reveals"]}** carried a
measurement, a margin or a verdict.

## 5. The agent desk

Microsoft Agent Framework workflow on Foundry ({d["model"]}): Decision Desk agent (reads the call over MCP),
Verifier (code), four persona agents in parallel, each checked by code and then by the Editor agent.
{d["calls"]} VAR calls in four matches, cached for the demo.

| | |
|---|---|
| Decision Desk drafts passing the Verifier first time | {d["desk_first_pass"]} of {d["desk_unplanted"]} |
| Planted wrong numbers caught (one per match) | {d["planted_caught"]} of {d["planted"]} |
| Audience versions rescued by a retry | {d["recovered_on_retry"]} of {d["variants"]} |
| Rewrites the Editor sent back because the meaning changed | {d["editor_catches"]} |
| Fallbacks to the engine's own text | {d["fallbacks"]} |
| Time per call, whole workflow | median {d["median_s"]} s, longest {d["max_s"]} s |

A fallback is the system working: when an agent can't produce a grounded text in two tries, the engine's
deterministic explanation airs instead, so a fan never sees an unchecked claim.
"""


def main() -> None:
    ap = argparse.ArgumentParser(prog="scripts/evaluate.py")
    ap.add_argument("--seeds", type=int, default=6)
    args = ap.parse_args()
    t0 = time.perf_counter()
    report = {
        "generated": time.strftime("%Y-%m-%d"),
        "measurement": measurement_and_errors(args.seeds),
        "red_team": red_team(),
        "spoilers": spoilers(),
        "desk": desk(),
    }
    (DATA / "eval.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (ROOT / "docs" / "evaluation.md").write_text(write_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=1))
    print(f"done in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
