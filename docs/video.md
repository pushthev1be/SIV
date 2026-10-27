# Demo video (1:56)

`uv run python scripts/record_video.py` records it from the live demo and writes `build/video/siv-demo.mp4`
(1920×1080, 30 fps, captions burned in) and `build/video/siv-demo.srt` for upload forms that take captions.
Every frame is SIV's own page, driven through its `window.__siv` hooks: no broadcast footage and no music.
The narration is ElevenLabs ("Daniel"), one cached clip per line, so changing a line re-voices only that line.

A card in the empty left column names the judging criterion each scene answers.

| Time | Criterion | Screen | Voice-over |
|---|---|---|---|
| 0:00–0:11 | The problem | Goal, "Check in progress", No goal; then the SIV bumper | "A goal goes in. The screen says: check in progress. A minute later, it's gone, and nobody can tell you why. SIV fixes that." |
| 0:11–0:26 | 4 User experience | Japan v Spain 2022 (StatsBomb), Casual fan. Mitoma's cross, the bumper, the VAR steps; the band says only what is being checked | "This is Japan against Spain at the 2022 World Cup, rebuilt from StatsBomb open data. While the check runs, SIV tells fans what is being checked, and under which law. Never the answer early." |
| 0:26–0:42 | 3 Real-world impact | The decision; the replay with the build-up, the deciding frame and the 10 cm caveat | "When the officials decide, the evidence plays: the build-up, the deciding frame, the ball against the line. Here the data is only good to ten centimetres, so SIV says so, instead of inventing a margin." |
| 0:42–0:58 | 1 Technological implementation | Every VAR call, Analyst: Brennan's offside replay, slow motion into the freeze, 4 cm | "Every number is measured from player positions, never stated. A seeded simulator, running real tactical play scripts, gives matches where the truth is known. Here, Brennan is four centimetres offside." |
| 0:58–1:10 | 4 User experience | The same call as Casual fan, Analyst, then Player-follow | "One match, three audiences, from the same facts. Plain words for a casual fan. The margin and the law for an analyst. Or follow a single player." |
| 1:10–1:34 | 2 Agentic design, then 5 Best Multi-Agent System | AI desk for Japan v Spain: the trace rows light up in turn (MCP reads, the planted number rejected twice, the fallback, four personas passing); the Fan, Analyst, Español and Ref Buddy tabs | "Behind it is a Microsoft Agent Framework workflow on Foundry. A Decision Desk agent reads the call over MCP. A code Verifier checks every number, and fails closed: here, a planted wrong number is rejected twice, and never airs. Four persona agents then write in parallel, and an Editor agent checks the meaning." |
| 1:34–1:50 | 3 Real-world impact, then 1 | Evaluation page: the four headline numbers, then the VAR-mistakes and red-team tables | "And it's measured. Across thirty simulated matches, SIV flagged all twenty-four wrong VAR decisions, and none of the forty-eight correct ones. The Verifier caught ninety-nine percent of planted errors." |
| 1:50–1:56 | | The SIV bumper, held on the referee framing the VAR screen; the live URL and the repo | "SIV. Every call, explained." |

## Re-recording

- `--only <scene>` records one scene to `build/video/scene_<name>.mp4`, for checking timings.
- `--site http://127.0.0.1:8765/` records a local build instead of the deployed demo.
- The script refuses a cut of two minutes or more; shorten a line if it does.
- Set `ELEVENLABS_API_KEY` to voice a changed line.
