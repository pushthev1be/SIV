# SIV: Super Intelligence Viewer

**Category:** Best Multi-Agent System
**Live demo:** https://green-stone-07a59ad0f.5.azurestaticapps.net (no sign-in)
**Evaluation:** https://green-stone-07a59ad0f.5.azurestaticapps.net/eval.html
**Code:** https://github.com/pushthev1be/SIV

## In one line

VAR calls are the moments fans understand least. SIV explains every call: what is being checked, under
which law, and the measured evidence. Nothing is shown until the officials decide, and the explanation is
written for a casual fan, an analyst or someone following one player.

## The problem

A goal goes in, the screen says "Check in progress", and a minute later the goal is gone. Fans in the
stadium and at home get no reason and no evidence. When VAR gets a call wrong, nobody can tell.

## What it does

- **Replays every call with the evidence.** You see the build-up, the frame that decides it, the
  measured line or point (offside, ball out, goal line, edge of the area, keeper off the line, DOGSO), and
  the margin in centimetres and inches.
- **Says nothing early.** While a check is open, viewers learn what is being checked and under which
  law, never a margin or a verdict. Tests enforce this, and the evaluation counts any leak.
- **Flags wrong decisions.** A decision that its own measurement contradicts is flagged as wrong.
- **One match, three audiences.** A "Viewing as" switch (Casual fan, Analyst, Player-follow) changes the
  captions, the replay text and the agent explanations, all from the same engine facts.
- **Works on real calls.** Japan v Spain and Wales v Iran (World Cup 2022) are rebuilt from StatsBomb open
  data. At about 10 cm resolution, SIV says when a call is too close for the data to settle rather than
  inventing a margin.

## 1. Technological implementation

- **Measured, never stated.** The simulator emits only events and a 2 Hz position feed. Speeds,
  distances and margins are derived by the engine (`engine/measure.py`, `engine/offside.py`), and every
  on-screen claim cites its source events.
- **Synthetic data that follows football.** The seeded simulator runs play scripts for each formation
  role (4-3-3 against 4-4-2: build-up, overlaps, marking, goal-side pressing). Conformance tests check
  that each VAR scenario follows its script, and a realism check bounds speeds and distances.
- **Real data path.** A StatsBomb converter links anonymous 360 detections into player tracks and gives
  the real clips the same engine, replays and agent desk as the simulated matches.
- **Practices.** Pydantic contracts generate the TypeScript types, and CI checks for drift. There are 160
  tests, ruff, Bicep infrastructure and Entra ID everywhere, with no keys or connection strings in the
  repo.

## 2. Agentic design and innovation

A Microsoft Agent Framework workflow (`orchestrator/desk.py`) on Foundry (gpt-5-mini):

1. A **Decision Desk** agent reads the call through read-only **MCP** tools (incident, events, law
   summary) and drafts the explanation.
2. A **Verifier** (code, not a model) checks the draft. It fails closed: every number and player must be
   in that call's facts, only the call's own law may be cited, and nothing may give the decision away
   early.
3. The workflow **fans out** to four persona executors in parallel, each running the Persona agent for
   one audience: Fan, Analyst, Español and Ref Buddy (the officials' briefing).
4. Each version is checked by the Verifier, then by an **Editor** agent that sends a rewrite back if its
   meaning changed.
5. A version that fails twice falls back to the engine's deterministic text. The workflow **fans in** to a
   Publisher.

The AI desk tab shows each call's full trace: tools read, verdicts, retries, Editor send-backs and
timings. A stress-test mode plants wrong numbers to show the Verifier catching them.

What's new is the boundary: the models write, but code decides what can air, and it fails closed.

## 3. Real-world impact

- Broadcasters and stadium screens could explain a call in the time it takes to make it, without
  speculating.
- Fans get reasons and evidence instead of "Check in progress".
- Officials get Ref Buddy: the law, the evidence and what it supports, as a briefing that is never
  broadcast.
- **Trust in numbers.** Over 30 simulated matches, SIV flagged 24 of 24 wrong decisions and 0 of 48
  correct ones. The Verifier caught 971 of 979 planted errors (99.2%) and rejected 0 of 200 clean texts.
  0 of 16 replays shown during a check gave anything away.
- **Honest limits.** In simulation the measurement error is 0 cm because the engine measures positions
  the script placed; that proves the pipeline, not real-world accuracy. Production would need optical
  tracking at broadcast frame rate.
- **Deployment.** The demo runs on Azure Static Web Apps with cached agent runs, so viewing costs nothing.
  The live path (Event Hubs producer, MCP server, Foundry agents) is in the repo.

## 4. User experience and presentation

- A broadcast-style viewer with a score bug, a calm timeline of calls and a Calls list that fills in as
  the match plays, so nothing is spoiled.
- A clickable key: any row opens that call's replay.
- Replays use a follow camera, slow motion into the key frame, then a freeze and zoom onto the line, with
  the HUD showing who is involved and how fast.
- The SIV bumper plays when a check opens. The Player card shows live speed, distance covered and every
  call a player was part of.
- An evaluation page anyone can open.

## 5. Category fit: Best Multi-Agent System

The workflow uses three Foundry agents (Decision Desk, Persona and Editor) across eight executors: the
Decision Desk, the Verifier, four parallel personas and the Publisher, with the Editor called inside each
persona. They are orchestrated with Agent Framework edges (sequential, fan out and fan in), grounded
through MCP, and guarded by a code Verifier that fails closed with retry and fallback. The trace of every
handoff is visible in the product.

## Built with

Microsoft Foundry, Microsoft Agent Framework, MCP, Azure Event Hubs, Azure Static Web Apps, Bicep and the
Azure Developer CLI, Entra ID, GitHub Actions. Data: StatsBomb open data, with attribution.
