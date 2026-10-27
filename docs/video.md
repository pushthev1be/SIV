# Demo video: shot list (target 1:45, hard limit under 2:00)

Record the live demo at 1920×1080 in a clean browser window. Use no music, or only music you made or that
is licensed for this. Use no broadcast footage: everything on screen is SIV. The voice-over below runs at
about 150 words a minute, so each line fits its slot.

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:10 | The SIV wallpaper, then the bumper | "A goal goes in. The screen says 'Check in progress.' A minute later it's gone, and nobody can tell you why. SIV fixes that." |
| 0:10–0:30 | `?s=real_japan_spain&v=fan`. Play to the check: the bumper, then the VAR badge. The band says only what is being checked. | "This is Japan against Spain, 2022, rebuilt from StatsBomb data. While the check runs, SIV says what's being checked and under which law. Never the answer early." |
| 0:30–0:50 | The decision lands; open the replay: build-up, freeze, zoom onto the ball and the line, the Law 9 illustration | "When the officials decide, the evidence plays: the build-up, the deciding frame, the ball against the line. And where the data is too coarse to settle a millimetre call, SIV says so instead of inventing a margin." |
| 0:50–1:10 | Switch Casual fan, then Analyst, then Player-follow on `var_showcase`, holding each caption for 3 s | "One match, three audiences, from the same facts. A casual fan gets plain words. An analyst gets the margin and the law. Follow one player and their moments become the story." |
| 1:10–1:30 | AI desk tab: open one call's trace (Decision Desk read tools, Verifier, four personas, Editor, published); open a stress-test call where a planted number is rejected | "Behind it, a Microsoft Agent Framework workflow on Foundry. A Decision Desk agent reads the call over MCP. Code verifies every number and fails closed. Four persona agents write in parallel, an Editor checks meaning, and a planted wrong number never airs." |
| 1:30–1:42 | Evaluation page: scroll the cards | "In 30 simulated matches it flagged all 24 wrong VAR decisions, and none of the 48 correct ones. The fact checker caught 99 percent of planted errors." |
| 1:42–1:45 | Wallpaper with the live URL | "SIV. Every call, explained." |

## Before recording

- Use the deployed site, not localhost, so the URL on screen is the one the judges will open.
- Close the replay's HUD callouts that overlap the band if they clutter the frame.
- Speed: 10× for simulated matches. Real clips play at 1× by default.
- Check the AI desk has a stress-test call in the match you show (`--plant first` runs are cached in
  `desk_<match>.json`). If not, show the Verifier-rejection row from any retry instead, and change the
  line to match.
