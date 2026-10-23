# Real matches: VAR calls replayed from StatsBomb open data

The simulated matches show every kind of call. These clips are the real thing: the actual move before a real
VAR call, from [StatsBomb open data](https://github.com/statsbomb/open-data), run through the same engine,
explainers and Ref Buddy as the simulated matches.

```
uv run python -m real.clips        # downloads the match data on first use, writes web/preview/data/real_*.json
```

Then pick the match from the menu in the preview, for example "Japan v Spain 2022 · real".

## Data and attribution

Data provided by StatsBomb under the terms of its
[open data licence](https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf). The files aren't committed to
this repository: `real/statsbomb.py` fetches them into `data/statsbomb/`, which is git-ignored.

## What the data has, and what it can't show

| | In the data | What we do |
|---|---|---|
| Passes, carries, shots | Every touch with a location and timestamp | Played back in real time; the ball moves along the real path |
| Player positions | "360" freeze frames: every player the broadcast camera saw, at most touches | Linked frame to frame into tracks and interpolated between frames |
| Who each player is | Only the player on the ball and the keepers | Players are named once they touch the ball; everyone else is shown without a shirt number |
| Precision | Locations tagged to about 10 cm | The engine won't quote a margin the data can't resolve, and says so |
| VAR | Not in the data | The check spans the stoppage between the goal and the restart; no check duration is quoted |

Players off camera aren't shown. That's why a clip has 15 to 18 players, not 22.

## Clips

### Japan 2-1 Spain, FIFA World Cup 2022, 1 December 2022 (`real_japan_spain`)

**The call:** Kaoru Mitoma cut the ball back from the byline and Ao Tanaka scored (51'). The referee disallowed it
for the ball being out of play. After a long review, VAR used the goal-line camera images to rule that part of the
ball was still over the line, so it was in play, and the goal was given. FIFA later said other images circulating
online were misleading.

**What VAR Narrative shows:** the real move (Ito, Tanaka, Doan's cross to the far post, Mitoma's cutback), frozen on
Mitoma's touch. The explainer states Law 9, then says plainly that the event data puts the ball on the goal line but
only to about 10 cm, which is too coarse for a call decided by millimetres, so the camera images decided it. Ref Buddy
gives the same advice. That's the honest answer, and it's why the call confused people: a ball on the line is in
play.

Sources: [ESPN: why Japan's winning goal was awarded](https://africa.espn.com/football/story/_/id/37634475/why-japans-winning-goal-vs-spain-was-awarded-var),
[The42: FIFA on the decision](https://www.the42.ie/japan-goal-spain-ball-over-line-fifa-var-decision-5936495-Dec2022).

## Next clips

Other real VAR calls in the same data (2022 World Cup, Euro 2020 and Euro 2024 all have 360 frames). Each would be
checked against reports before it's added:

- **Germany 2-0 Denmark, Euro 2024:** Joachim Andersen's goal ruled out for offside by a toe, then a handball
  penalty against him.
- **England 2-1 Denmark, Euro 2020 semi-final:** the extra-time penalty for Raheem Sterling that VAR let stand.
