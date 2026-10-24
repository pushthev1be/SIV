# Play scripts

Every simulated VAR call is set up by one of these moves. The simulator follows them pass by pass
(`sim/simulator.py`, `_play`), and `tests/test_plays.py` checks that every scripted call in every match did:
the same passes, in the same order, between the same roles. It also checks, across every match, that
nobody passes backwards when wide open in front of goal. Generated from `sim/plays.py` by
`scripts/plays_doc.py`.

## Formations

| Team | Roles (shirt number) |
|---|---|
| Northbridge, 4-3-3 | GK 1, LB 2, RB 3, LCB 4, RCB 5, DM 6, LCM 8, RCM 10, LW 7, RW 11, ST 9 |
| Ashvale, 4-4-2 | GK 1, LB 2, RB 3, LCB 4, RCB 5, LM 7, RM 11, LCM 6, RCM 8, ST 9, ST2 10 |

Roles in the plays are relative to the flank the move attacks down: FB is that side's full-back, W its
winger or wide midfielder, CM its central midfielder (CM2 the other), CB its centre-back. KEY is the player
the call is about. Defenders aren't scripted: they hold their shape, press the ball from the goal side
near their box, and mark attackers there.

## Wide overload and byline cut-back (`ball_out_goal`)

Build through the centre-back and the pivot, switch to the flank, the full-back overlaps outside the winger, who takes it to the byline and cuts it back for the centre-forward arriving at the spot.

1. The team wins the ball and plays it to the CB.
2. Pass to CM, 65 m from goal, in the half-space (into midfield).
3. Pass to FB, 47 m from goal, on the touchline (out to the full-back). KEY runs to 29 m from goal, on the touchline.
4. Pass to KEY, 27 m from goal, on the touchline (inside to the winger as the full-back overlaps). FB runs to 23 m from goal, on the touchline; ST runs to 15 m from goal, central.

## Between the lines and a one-two at the edge (`box_edge_foul`)

A pass into the striker dropping between the lines, set back to the midfielder, who plays it first time into the striker's run: a wall pass that takes him at the edge of the area.

1. The team wins the ball and plays it to the CB.
2. Pass to CM, 60 m from goal, central (into midfield).
3. Pass to KEY, 33 m from goal, central (into the striker between the lines). CM runs to 37 m from goal, in the half-space.
4. Pass to CM, 35 m from goal, in the half-space (set back). KEY runs to 25 m from goal, central.
5. Pass to KEY, 25 m from goal, central (first time into his run).

## Third-man run into the channel (`goal_line_clearance`)

Full-back to winger, winger inside to the midfielder, who finds the third man running in behind into the inside channel; he shoots across goal to the far post.

1. The team wins the ball and plays it to the CB.
2. Pass to FB, 60 m from goal, on the touchline (out to the full-back).
3. Pass to W, 39 m from goal, on the touchline (up the line to the winger). KEY runs to 25 m from goal, in the half-space.
4. Pass to CM, 41 m from goal, in the half-space (inside, the third man is running). KEY runs to 20 m from goal, in the half-space.
5. Pass to KEY, 20 m from goal, in the half-space (into the channel).

## Counter-attack through the middle (`penalty_review`)

The ball is won in midfield and played straight into the striker, who lays it off; the midfielder slides the attacker through into the area.

1. The team wins the ball and plays it to the CM.
2. Pass to ST, 35 m from goal, central (straight into the striker). KEY runs to 31 m from goal, in the half-space.
3. Pass to CM, 39 m from goal, central (lay-off). KEY runs to 25 m from goal, in the half-space.
4. Pass to KEY, 25 m from goal, in the half-space (through into the run).

## Counter-attack through the middle (`penalty_retake`)

The ball is won in midfield and played straight into the striker, who lays it off; the midfielder slides the taker-to-be through into the area.

1. The team wins the ball and plays it to the CM.
2. Pass to ST, 35 m from goal, central (straight into the striker). KEY runs to 31 m from goal, in the half-space.
3. Pass to CM, 39 m from goal, central (lay-off). KEY runs to 23 m from goal, central.
4. Pass to KEY, 23 m from goal, central (through into the run).

## Ball over the top behind a high line (`dogso_foul`)

With the defence pushed up to halfway, the centre-back looks up and plays it long over the top into the striker's run in behind; he's clean through with one defender chasing.

1. The team wins the ball and plays it to the CM.
2. Pass to CB, 75 m from goal, in the half-space (back to the centre-back).
3. Pass to KEY, 39 m from goal, central (long, over the top, into his run).

## Build-up to the playmaker (`offside_goal`)

Centre-back to the pivot, pivot to the playmaker in the half-space, who turns and looks for the striker's run against the defensive line.

1. The team wins the ball and plays it to the CB.
2. Pass to CM2, 65 m from goal, central (into midfield).
3. Pass to KEY, 45 m from goal, central (into the playmaker).
