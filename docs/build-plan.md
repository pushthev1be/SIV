# Touchline: revised build plan (Oct 18 to 27)

The goal is one vertical slice that proves the trust thesis end to end, then layers on top.
Each day ends with its exit check passing in CI. If a day slips, cut from Tier 3, then Tier 2.
Never move the noon Oct 26 feature freeze.

## Today (Oct 18), before anything else

- [ ] **Register** before Oct 20, 12:00 pm PT (Oct 20, 3:00 pm Indianapolis).
- [ ] **Check repo provenance**: `git log --reverse --format="%ad %s" | head`. The rules require the project to be "newly created after the Registration Period begins" (Sep 29). If any simulator, offside or replay code predates Sep 29, rewrite it or disclose it in the README.
- [ ] **Pick one name**: Touchline or Oracle. Use it in the repo, pitch, video and fan app.
- [ ] **Check Foundry model quota** on the student subscription, and request more now if needed.
- [ ] **Install the Azure CLIs**: `winget install Microsoft.AzureCLI` and `winget install Microsoft.Azd`, then `az login` and `azd auth login`.

Building from scratch (no simulator, offside engine or replay code exists yet), so the
simulator, offside engine and replay player are folded into Oct 18 to 22 below. If Oct 20 slips,
cut the late-comeback scenario and use straight-line player motion first.

## Scope tiers

### Tier 1: must ship (the demo fails without it)

| Area | Item | Req |
|---|---|---|
| Ingest | Simulator into Event Hubs, golden match plus the red-card scenario, seeded | F1, F2 |
| Interpret | Stats: speed, pass difficulty, rolling control and pressure | F3 |
| Interpret | Offside engine (exists) plus decision state machine | F4, F5 |
| Agents | MCP server with `get_recent_events`, `get_moment`, `get_incident`, `get_rolling_metrics`, `get_law_summary` | — |
| Agents | Detector, Explainer, Decision Desk as an **Agent Framework workflow** | F6, F7 |
| Trust | Grounding validator with one retry then template; premature-reveal block | N3, N4, N5 |
| Render | Cue contract, fan app overlays (scorebug, VAR badge, lines, band), offside replay | F8, F9 |
| Demo | Cached demo mode as the default link | N7 |
| Proof | Eval harness: p95 latency, grounding pass rate, fallback rate, premature reveals | N1 to N5 |
| Ship | README, architecture diagram, `azd up`, video | — |

### Tier 2: should ship (what the judges score; build Oct 24)

- Casual vs. Analyst modes as different overlay sets (F11)
- Persona agent fanned out in parallel per audience (adds the concurrent pattern)
- Spanish (F13), including the referee announcement subtitle
- Favorite-club wording (F12, club only)
- Producer console: hold and kill, plus the audit log (F10). This is the human-in-the-loop step.
- One OpenTelemetry trace from event to cue, screenshotted for the video

### Tier 3: cut first

- Stadium view (reuse the replay player's "stadium" status text instead)
- Player focus and metric pin (F12, beyond club)
- Recap agent: ship the template bullet list from the moment log, and add the agent only if time is left (F14)
- `get_similar_incidents`, `get_player_stats`
- Late-comeback scenario
- Voiced commentary
- Officials' consistency ledger

## Days

| Day | Build | Exit check (CI) |
|---|---|---|
| **Oct 18** | ✅ Repo, `contracts/` with TS generation, CI. Then **simulator v1**: seeded 90-minute match, all 22 positions per event, golden match plus red-card scenario | Contracts generate `web/src/contracts.ts` (done); determinism test; realism script (pass completion, distance, top speed in range) |
| **Oct 19** | ✅ (done Oct 18) Foundry deployments (small and strong models), budget alerts at $25/$50/$75, Event Hubs producer, `azd` skeleton. **Simulator v2**: offside-prone passes carry body frames and a 100 fps ball track | One model answers through `FoundryChatClient`; the sim publishes a full match to the hub |
| **Oct 20** | ✅ (done Oct 18) **Offside engine** (Law 11 playable parts, kick point from the ball track), stats engine, decision state machine | Offside unit tests with fixed frames; state machine tests; no "check complete" without a final decision |
| **Oct 21** | MCP server (FastMCP, streamable HTTP) plus grounding validator | An agent calls `get_incident` over MCP; the validator rejects a planted wrong number |
| **Oct 22** | Workflow: detector to explainer/Decision Desk to validator to cue (Tier 1 path only). **Replay builder plus player** (SVG) for the offside call | One incident flows end to end locally; the offside replay plays |
| **Oct 23** | Full match through the workflow, templates on failure, output cache by fact-packet hash | Full match; 0 unvalidated cues aired; a rerun makes 0 model calls |
| **Oct 24** | **Tier 2**: personas fan-out, Casual/Analyst, Spanish, club, console hold/kill | Snapshot tests per mode; console holds and kills a cue |
| **Oct 25** | Eval harness plus report; latency tuning (deterministic overlays first, text second) | `evals/report.md`: all four non-functional targets met |
| **Oct 26** | **Freeze at 12:00.** Deploy from a clean clone; cached demo on a phone; README | `azd up` from a clean clone works; demo link loads without login |
| **Oct 27** | Record and upload the video, write the pitch, submit **by 3 pm PT** | Submission fields double-checked |

## Video (target 1:50, with a hard cap at 2:00)

| Time | Shot | Proves |
|---|---|---|
| 0:00 to 0:10 | VAR check with only "Check in progress", which then becomes Touchline's version | The problem, and that you solve it |
| 0:10 to 0:40 | Live match: speed tag, pass difficulty, pressure explainer, the decision with lines | The pipeline works end to end |
| 0:40 to 0:55 | **Kill the explainer live; the template takes over; the overlay never blanks** | Failure recovery (your most memorable shot, so it goes early) |
| 0:55 to 1:15 | Same decision: Northbridge fan, Ashvale fan, Spanish, Analyst | Personalization changes the experience |
| 1:15 to 1:30 | Offside replay with the officials' view | Every call gets explained |
| 1:30 to 1:45 | Workflow graph (Agent Framework visualization), one trace, the eval report | Agentic design, Azure, measured quality |
| 1:45 to 1:50 | "The decision always stays with the officials." | Close |

## Pitch skeleton

> Touchline turns a synthetic match feed into on-screen explanations fans can trust. A
> multi-agent workflow built with Microsoft Agent Framework and Foundry explains each moment and
> VAR call, but a deterministic validator checks every number against the event log, and nothing
> about a decision airs before the officials make it. Built on Azure Event Hubs, Container Apps,
> Cosmos DB, Web PubSub and an MCP server, with GitHub Copilot agent mode and `azd`.
