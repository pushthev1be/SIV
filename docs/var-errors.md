# Real VAR errors: the `var_errors` scenario

`sim/scenarios/var_errors.json` (match `m05`) replays five Premier League calls where VAR was used and the
decision was still wrong, as later admitted by PGMOL or overturned on appeal. The clubs and players stay
invented (Northbridge and Ashvale, see `sim/teams.py`); this file maps each scripted incident to the real one.
Margins are illustrative: none of these incidents has a published, measured margin.

```
uv run python -m sim var_errors
```

| at (s) | Script | Real incident | What went wrong | What VAR Narrative shows |
|---|---|---|---|---|
| 600 | `offside_goal`, NBR-11, margin −0.30 m, `no_goal` | Luis Díaz goal, Tottenham 2-1 Liverpool, 30 Sep 2023 | VAR Darren England saw Díaz was onside but thought the on-field call was "goal" and said "check complete". Play restarted, so it couldn't be fixed. PGMOL called it a "significant human error". | Measures him 30 cm onside and flags that the measurement doesn't explain the decision |
| 1500 | `penalty_review`, ASH-9 vs NBR-4, `awarded: false` | Kalajdžić and Onana, Man Utd 1-0 Wolves, 14 Aug 2023 | Onana missed the ball and wiped out Kalajdžić in stoppage time. VAR Michael Salisbury didn't recommend a review. Howard Webb said VAR should have intervened, and the officials weren't selected for the next round. | Repeats the officials' "not enough for a foul" verdict; it can't disagree |
| 2300 | `offside_goal`, ASH-3, margin −0.20 m, `no_goal` | Pervis Estupiñán goal, Crystal Palace 1-1 Brighton, 11 Feb 2023 | VAR John Brooks drew the line from James Tomkins, not the deeper Marc Guéhi. PGMOL apologised and Brooks was stood down for two games. | Measures from the true second-last defender (20 cm onside) and flags the decision |
| 3500 | `red_card`, NBR-3, VAR `upheld` | Myles Lewis-Skelly red card, Wolves 0-1 Arsenal, 25 Jan 2025 | Michael Oliver sent him off for a challenge on Matt Doherty and VAR Darren England backed it. An FA commission upheld Arsenal's wrongful-dismissal claim and cancelled the ban. | Repeats the officials' "endangered safety" verdict; it can't disagree |
| 4600 | `offside_goal`, NBR-6, margin +0.15 m, `goal_stands` | Ivan Toney equaliser, Arsenal 1-1 Brentford, 11 Feb 2023 | VAR Lee Mason checked Ethan Pinnock but never drew lines for Christian Nørgaard, who was offside. PGMOL admitted the error the same weekend as the Brighton one. | Measures 15 cm offside and flags that the decision stands against it |

## Simplifications

- **Toney:** in the real match, Nørgaard was offside and headed the ball on for Toney to score. `offside_goal`
  has one attacker, so the offside player (NBR-6) scores himself.
- **Kalajdžić:** the real foul was by the goalkeeper. A defender (NBR-4) commits it here, because
  `penalty_review` runs the fouler as an outfield chaser.
- **Díaz:** the real error was miscommunication, not a measurement. The scenario uses a 60 s check to stand
  in for the delay, and the officials' notes are where the "thought it was a goal" explanation would go.

The offside cases are where VAR Narrative proves its worth: the line measurement contradicts the decision, and the
explainer says so instead of defending it. For the penalty and the red card, nothing is measured, so the
explainer passes on the officials' verdict. Flagging those would need a contact or severity measurement that
the engine doesn't have yet.

## Sources

- [ESPN: VAR audio and explanation of the Díaz error](https://global.espn.com/football/story/_/id/38557319/var-audio-explanation-diaz-offside-error-released-pgmol)
- [ESPN: The VAR review, Onana escapes a penalty vs Wolves](https://global.espn.com/football/story/_/id/38195110/the-var-review-onana-escapes-injury-penalty-vs-wolves)
- [90min: Webb explains the Onana VAR mistake](https://www.90min.com/posts/pgmol-chief-offers-explanation-andre-onana-var-mistake-wolves)
- [Brighton & Hove Albion: PGMOL statement on the disallowed goal at Palace](https://www.brightonandhovealbion.com/media-article/PGMOL-statement-on-disallowed-goal-at-Palace)
- [Sky Sports: John Brooks replaced after the VAR error](https://www.skysports.com/football/news/11095/12810079/referee-john-brooks-replaced-for-liverpool-vs-everton-after-var-error-which-denied-brighton-goal-at-crystal-palace)
- [ESPN: PGMOL admits VAR offside errors at Brentford v Arsenal and Brighton v Palace](https://www.espn.com/soccer/english-premier-league/story/4875921/pgmol-admits-var-offside-errors-for-two-goals-brentford-vs-arsenal-brighton-vs-crystal-palace)
- [Express & Star: Lewis-Skelly red card overturned on appeal](https://www.expressandstar.com/uk-sports/2025/01/28/myles-lewis-skelly-red-card-at-wolves-overturned-after-successful-arsenal-appeal/)
