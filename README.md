# SIV: Super Intelligence Viewer

![SIV, Super Intelligence Viewer](web/preview/assets/siv-wallpaper.png)

**Live demo: [green-stone-07a59ad0f.5.azurestaticapps.net](https://green-stone-07a59ad0f.5.azurestaticapps.net)**
(opens on a real VAR call, Japan v Spain 2022; pick any match from the menu; the AI desk tab shows the agents'
explanations and their trace) · **[Evaluation](https://green-stone-07a59ad0f.5.azurestaticapps.net/eval.html)**

**VAR calls are the moments fans understand least.** A goal goes in, the screen says "Check in
progress", and a minute later it is gone, with nothing anyone in the stadium or at home can follow.

SIV makes those calls understandable. While a check runs, it shows what is being checked,
under which law, and why it is taking time. The moment the officials decide, and never before, it
shows the evidence: a replay of the build-up, the line, the margin in centimetres and inches, and the
players involved, explained for casual fans, analysts, the stadium screen and the officials' own
briefing.

Two rules keep it trustworthy:

- **Every number traces to the match data.** Speeds, distances and margins are measured from
  positions, never stated, and every on-screen claim cites the events it came from.
- **Nothing about a decision airs before the decision.** No margin, measurement or verdict leaves the
  engine while a check is open. Tests enforce it.

Built for the Microsoft Premier League Hackathon (Oct 2026). Two kinds of match:

- **Real calls** from StatsBomb open data (Japan v Spain and Wales v Iran, World Cup 2022), rebuilt from
  event data and 360 freeze frames, with attribution. At about 10 cm resolution, SIV says when a call is
  too close for the data to settle rather than inventing a margin.
- **Simulated matches** between fictional clubs (Northbridge, Ashvale), where the truth is known, so
  every measurement and every flag can be marked right or wrong.

## One match, three audiences

A **Viewing as** switch in the header changes what the whole viewer says, from the same engine facts:

| Mode | The same offside call reads | Opens on |
|---|---|---|
| Casual fan | "Brennan was beyond Ruiz when the pass was played" | Calls list; replays and AI desk in fan wording |
| Analyst | "Offside · Brennan 4 cm (1.6 in) beyond Ruiz · Law 11" | Ref Buddy (law and evidence); analyst wording |
| Player-follow | "Following Brennan · …", and the player's own passes, tackles and sprints as captions | The player card and an on-pitch callout |

Link straight to a mode with `?v=fan`, `?v=analyst` or `?v=player`.

## The agent desk

Each decided VAR call goes through a Microsoft Agent Framework workflow on Foundry (gpt-5-mini):

```
Decision Desk agent ──► Verifier (code) ──► fan out ──► Persona agent × 4: Fan · Analyst · Español · Ref Buddy
   reads the call over MCP     fails closed               each checked by the Verifier, then by an Editor
                                                          agent for meaning ──► fan in ──► Publisher
```

- **Grounded:** the Decision Desk reads the call through read-only MCP tools (`mcp_server/`). Every number
  and player in any text must appear in that call's facts, laws are limited to the call's own law, and
  nothing about the decision may appear before the officials decide (`orchestrator/validator.py`).
- **Fails closed:** a text the Verifier rejects is retried once with the reasons; if it fails again, the
  engine's deterministic text airs instead. No unchecked claim reaches a viewer.
- **Traced:** the AI desk tab shows every step per call: tools read, verdicts, retries, Editor send-backs,
  timings. The demo serves cached runs (`web/preview/data/desk_*.json`), so viewing costs no model calls.
  Re-run with `uv run python -m orchestrator.desk <match>`.

## Evaluation

[Evaluation page](https://green-stone-07a59ad0f.5.azurestaticapps.net/eval.html) ·
`uv run python scripts/evaluate.py` · [docs/evaluation.md](docs/evaluation.md)

- 24 of 24 wrong VAR decisions flagged in 30 simulated matches, 0 of 48 correct ones flagged.
- The Verifier caught 971 of 979 planted errors (99.2%) and rejected 0 of 200 clean texts.
- 0 of 16 replays shown during a check gave anything away.
- Agent desk: 4 of 4 planted wrong numbers caught; 3 of 52 audience versions fell back to engine text.

The simulated measurement error is 0.0 cm because the engine measures positions the script placed: it
proves the pipeline loses nothing, not real-world accuracy.

## See it

**A VAR check, live.** The broadcast says only what is being checked. On the right, Ref Buddy briefs
the officials: the law, the evidence at the kick frame, and what it supports. Then the decision lands
with the lines and a replay one tap away.

![Live VAR check: neutral caption for fans while Ref Buddy briefs the officials, then the decision](docs/media/live-var-check.gif)

**Every call explained: the rule, the evidence, the decision.** Slow motion into the moment of the
pass, then a freeze and zoom onto the line: the attacker's front foot against the defender's, arms
ignored (Law 11), measured to the centimetre from a 100 fps ball track. The fan explanation states the
rule and shows how the evidence leads to the call. Loops until you close it.

![Offside replay: rule and evidence explained, freeze and zoom showing a 12.2 in (31 cm) margin](docs/media/offside-replay.gif)

**Every VAR call that can be measured, measured.** Offside is one of six. Each is simulated, measured
from positions and the ball track, and explained with the rule and the evidence:

| Call | What is measured | Law |
|---|---|---|
| Offside | front playable part against the second-last defender | 11 |
| Ball out of play before a goal | how far the whole ball was over the goal line or touchline | 9 |
| Goal-line | whether the whole ball crossed, by how much | 10 |
| Penalty or free kick | where contact started against the edge of the area (the line counts as the area) | 12, 14 |
| Keeper off the line at a penalty | the keeper's nearest foot against the goal line at the kick | 14 |
| Denying an obvious goal-scoring opportunity | distance, direction, defenders between, control | 12 |

![Measured calls: ball out, penalty or free kick, goal-line, keeper off the line, DOGSO](docs/media/measured-calls.gif)

**Who was involved, and how fast.** A toggleable HUD follows the players in the call, with speeds
derived from tracking positions, never stated by the feed.

![HUD callouts with speed and role for the attacker, defender and passer](docs/media/hud-callouts.gif)

**A calm timeline.** Goals, VAR checks, penalties and red cards sit above the bar; cards and flags are
ticks on it. The Calls list grows as the match plays, so nothing is spoiled.

![Timeline with event icons and the Calls list filling in as the match plays](docs/media/calls-timeline.gif)

**Tap any player.** Live speed, distance to the ball, whether they are in the play, their match so
far, and every call they were part of.

![Player card: live callout on the pitch and the player's stats, calls and recent actions](docs/media/player-card.gif)

<sub>Clips are recorded from the preview with `uv run python scripts/record_gifs.py`, so they always
match the code.</sub>

<sub>These clips predate the SIV name, real matches and the agent desk; the live demo is current.</sub>

## Layout

| Path | What |
|---|---|
| `contracts/` | Pydantic models for the JSON contracts; generates `web/src/contracts.ts` |
| `sim/` | Seeded match simulator with play scripts per formation, and the Event Hubs producer |
| `real/` | StatsBomb open data to SIV match feeds (real VAR calls) |
| `engine/` | Stats, measurements (offside, ball out, goal line, box edge, keeper line, DOGSO), decisions, replays |
| `mcp_server/` | Read-only MCP tools over match state and law summaries |
| `orchestrator/` | Agent Framework workflow (`desk.py`) and the grounding Verifier (`validator.py`) |
| `agents/` | Smoke tests for Foundry and MCP |
| `scripts/` | Evaluation, GIF recording, play-script docs |
| `web/preview/` | The viewer, AI desk and evaluation page (static, deployed to Static Web Apps) |
| `infra/` | Bicep for Foundry, Event Hubs and the Static Web App |

## Run locally

```sh
uv sync
uv run pytest
uv run python -m sim golden -o golden.jsonl   # simulate a match: golden | red_card | late_comeback
uv run python -m sim.realism                  # check every scenario looks like football
uv run python -m contracts.generate && npm --prefix web run gen:contracts
```

Watch a match in the browser (simulator + engine, no Azure needed):

```sh
uv run python -m engine.export                       # writes web/preview/data/*.json
uv run python -m http.server 8765 -d web/preview     # then open http://localhost:8765
```

The simulator is seeded, so a scenario produces the same match every time. It emits an event feed
(passes, shots, tackles, fouls, cards, VAR) and a 2 Hz tracking feed of all player positions; speeds,
distances and offside margins are never stated, only derived.

## Deploy to Azure

```sh
az login
azd env new siv-dev --location eastus2
azd env set AZURE_PRINCIPAL_ID $(az ad signed-in-user show --query id -o tsv)
azd up                                   # Foundry project + gpt-5-mini, Event Hubs Basic
uv run python -m agents.smoke            # the model answers through Agent Framework
uv run python -m sim.producer golden --speed 60   # stream a match into Event Hubs
uv run python -m agents.mcp_smoke         # a Foundry agent reads state over MCP; its text is validated
```

Every call uses Entra ID; there are no keys or connection strings anywhere.

## Microsoft technologies

Microsoft Foundry (gpt-5-mini), Microsoft Agent Framework (workflows, Foundry agents, MCP tools),
Azure Event Hubs, Azure Static Web Apps, Bicep with the Azure Developer CLI, Entra ID, and GitHub Actions.

## Data

Real-match clips use [StatsBomb open data](https://github.com/statsbomb/open-data) under its user
agreement; the data is downloaded at build time and not redistributed in this repo. Law summaries are
paraphrases, not IFAB text.

## License

MIT
