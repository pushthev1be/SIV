"""StatsBomb open data -> MatchEvent feed, for a short clip of a real match around a VAR call.

Data: https://github.com/statsbomb/open-data (free with attribution; not committed, see `fetch`).

What the data has and what we do with it:
- Events (passes, carries, shots, recoveries) with locations in a 120 x 80 yard frame, each from the
  acting team's point of view (it always attacks toward x = 120). We rotate the other team's events
  into one absolute frame and scale to 105 x 68 m.
- 360 freeze frames: every visible player at the moment of most events, by team, but anonymous apart
  from the player on the ball and the keepers. We link detections frame to frame into tracks (nearest
  plausible match, bridging short gaps), and name a track when it carries the ball or is a keeper.
  Everyone else stays anonymous: numbers 60+ in their team code, shown without a shirt number.
- Positions between frames are interpolated; a player is shown only between their first and last sighting.
- Precision: event locations are tagged to about 10 cm. Checks decided by millimetres can't be settled
  from this data, and the engine says so (`EVIDENCE_RESOLUTION_M`).
"""

from __future__ import annotations

import json
import math
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from contracts import MatchEvent
from contracts.common import PITCH_LENGTH_M as L
from contracts.common import PITCH_WIDTH_M as W
from contracts.event import BodyFrame

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "statsbomb"
BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
SB_L, SB_W = 120.0, 80.0
EVIDENCE_RESOLUTION_M = 0.1  # how finely the event data places the ball and players
TRACK_HZ = 4  # tracking samples per second in the feed
MAX_SPEED_MS = 9.5  # a detection further than this allows is a different player
LINK_SLACK_M = 2.5  # tagging noise allowed on top of running speed
GAP_S = 4.0  # a track that drops out of view for up to this long can be picked up again
ANON_FROM = 60


def fetch(match_id: int) -> None:
    """Download a match's events, 360 frames and lineups into data/statsbomb (git-ignored)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for kind in ("events", "three-sixty", "lineups"):
        out = DATA_DIR / f"{kind}_{match_id}.json"
        if not out.exists():
            urllib.request.urlretrieve(f"{BASE_URL}/{kind}/{match_id}.json", out)  # noqa: S310 (fixed https host)


def _load(kind: str, match_id: int):
    path = DATA_DIR / f"{kind}_{match_id}.json"
    if not path.exists():
        fetch(match_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _secs(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@dataclass
class Clip:
    """A real incident: which match, which stretch of play, and what the officials decided."""

    name: str
    sb_match: int
    match_code: str  # our match id, m10+
    period: int
    start: str  # period clock, "mm:ss"
    teams: dict[str, str]  # StatsBomb team name -> our three-letter code
    colours: dict[str, str]
    home: str  # our code for the team shown as triangles
    var_check: str  # our VAR check outcome, e.g. "ball_out"
    var_team: str  # the team whose goal or attack is being checked
    var_player_sb: str  # StatsBomb name of the player the check is about
    decision: str  # our var_decision outcome
    awarded_goal_sb: str | None = None  # scorer of a goal VAR gives after it was disallowed on the field
    after_restart_s: float = 10.0
    score_before: dict[str, int] = field(default_factory=dict)


@dataclass
class _Track:
    team: str
    pts: list[tuple[float, float, float]] = field(default_factory=list)  # (t, x, y)
    votes: Counter = field(default_factory=Counter)
    pid: str | None = None

    @property
    def last(self) -> tuple[float, float, float]:
        return self.pts[-1]


class Converter:
    def __init__(self, clip: Clip) -> None:
        self.clip = clip
        self.events = _load("events", clip.sb_match)
        self.frames = {f["event_uuid"]: f for f in _load("three-sixty", clip.sb_match)}
        self.lineups = _load("lineups", clip.sb_match)
        self.code = clip.teams
        self.pid: dict[int, str] = {}  # StatsBomb player id -> our player id
        self.names: dict[str, str] = {}
        self.roles: dict[str, str] = {}
        self.keeper: dict[str, str] = {}
        for team in self.lineups:
            code = self.code[team["team_name"]]
            for p in team["lineup"]:
                pid = f"{code}-{p['jersey_number']}"
                self.pid[p["player_id"]] = pid
                self.names[pid] = p.get("player_nickname") or p["player_name"]
                pos = p["positions"][0]["position"] if p["positions"] else ""
                role = "GK" if "Goalkeeper" in pos else "DF" if "Back" in pos else "FW" if (
                    "Forward" in pos or "Wing" in pos and "Back" not in pos) else "MF"  # fmt: skip
                self.roles[pid] = role
                if role == "GK" and p["positions"] and p["positions"][0].get("start_reason") == "Starting XI":
                    self.keeper[code] = pid
        # The team attacking toward x = 105 in our frame for this period: the clip's home side.
        self.right = clip.home

    # ── geometry ──
    def _xy(self, loc, code: str) -> tuple[float, float]:
        x, y = loc[0] * L / SB_L, loc[1] * W / SB_W
        return (round(x, 2), round(y, 2)) if code == self.right else (round(L - x, 2), round(W - y, 2))

    def _clock(self, e) -> float:
        return round((0.0 if self.clip.period == 1 else 2700.0) + _secs(e["timestamp"]), 2)

    # ── clip ──
    def _window(self) -> tuple[list[dict], dict]:
        period = [e for e in self.events if e["period"] == self.clip.period]
        t0 = 60 * int(self.clip.start.split(":")[0]) + float(self.clip.start.split(":")[1])
        goal = next(e for e in period if _secs(e["timestamp"]) >= t0 and e["type"]["name"] == "Shot"
                    and e["shot"]["outcome"]["name"] == "Goal")  # fmt: skip

        def kickoff(e) -> bool:
            return e.get("play_pattern", {}).get("name") == "From Kick Off" and e["type"]["name"] == "Pass"

        restart = next(e for e in period if _secs(e["timestamp"]) > _secs(goal["timestamp"]) + 5 and kickoff(e))
        t_end = _secs(restart["timestamp"]) + self.clip.after_restart_s
        play = [e for e in period if t0 <= _secs(e["timestamp"]) <= _secs(goal["timestamp"])]
        after = [e for e in period if _secs(restart["timestamp"]) <= _secs(e["timestamp"]) <= t_end]
        return play + after, {"goal": goal, "restart": restart}

    def _tracks(self, evs: list[dict]) -> list[_Track]:
        tracks: list[_Track] = []
        last_t = -1e9
        for e in evs:
            f = self.frames.get(e["id"])
            if not f or "team" not in e:
                continue
            t, code = self._clock(e), self.code[e["team"]["name"]]
            if t - last_t < 0.05:  # a receipt and the carry after it share one frame: link it once
                continue
            last_t = t
            other = next(c for c in self.code.values() if c != code)
            dets = []
            for d in f["freeze_frame"]:
                team = code if d["teammate"] else other
                ident = None
                if d.get("actor") and e.get("player"):
                    ident = self.pid.get(e["player"]["id"])
                elif d.get("keeper"):
                    ident = self.keeper.get(team)
                dets.append((team, self._xy(d["location"], code), ident))
            # Greedy linking per team: closest plausible pairs first.
            pairs = []
            for i, (team, (x, y), _) in enumerate(dets):
                for k, tr in enumerate(tracks):
                    lt, lx, ly = tr.last
                    dt = t - lt
                    if tr.team != team or dt <= 0 or dt > GAP_S:
                        continue
                    dd = math.hypot(x - lx, y - ly)
                    if dd <= LINK_SLACK_M + MAX_SPEED_MS * dt:
                        pairs.append((dd, i, k))
            used_d, used_t = set(), set()
            for _dd, i, k in sorted(pairs):
                if i in used_d or k in used_t:
                    continue
                ident, trk = dets[i][2], tracks[k]
                if ident and trk.votes and trk.votes.most_common(1)[0][0] != ident:
                    continue  # this track belongs to someone else
                if ident and not trk.votes and len(trk.pts) >= 2:
                    continue  # an established unnamed track is someone we never identified, not this player
                used_d.add(i)
                used_t.add(k)
                trk.pts.append((t, *dets[i][1]))
                if ident:
                    trk.votes[ident] += 1
            for i, (team, xy, ident) in enumerate(dets):
                if i not in used_d:
                    tr = _Track(team, [(t, *xy)])
                    if ident:
                        tr.votes[ident] += 1
                    tracks.append(tr)
        # Names: a track takes the identity it was seen with most. Fragments of one player share it.
        anon = Counter()
        claimed: dict[str, list[_Track]] = {}
        for tr in tracks:
            if tr.votes:
                claimed.setdefault(tr.votes.most_common(1)[0][0], []).append(tr)
        for pid, trs in claimed.items():
            trs.sort(key=lambda tr: tr.pts[0][0])
            end = -1e9
            for tr in trs:
                if tr.pts[0][0] > end:  # fragments in sequence: same player
                    tr.pid, end = pid, tr.pts[-1][0]
        for tr in tracks:
            if tr.pid is None:
                anon[tr.team] += 1
                tr.pid = f"{tr.team}-{ANON_FROM + anon[tr.team]}"
        return [tr for tr in tracks if len(tr.pts) >= 2 or tr.pid in claimed]

    @staticmethod
    def _positions(tracks: list[_Track], t: float) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for tr in tracks:
            p = tr.pts
            if not p[0][0] - 0.3 <= t <= p[-1][0] + 0.3:
                continue
            for a, b in zip(p, p[1:], strict=False):
                if a[0] <= t <= b[0]:
                    k = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0
                    out[tr.pid] = (round(a[1] + (b[1] - a[1]) * k, 2), round(a[2] + (b[2] - a[2]) * k, 2))
                    break
            else:
                q = p[0] if t < p[0][0] else p[-1]
                out[tr.pid] = (q[1], q[2])
        return out

    def convert(self) -> tuple[list[MatchEvent], dict]:
        evs, marks = self._window()
        tracks = self._tracks(evs)
        mc = self.clip.match_code
        out: list[MatchEvent] = []

        def emit(type_: str, clock: float, positions: dict | None = None, **kw) -> MatchEvent:
            ev = MatchEvent(event_id=f"{mc}-e{len(out) + 1}", match_id=mc, period=self.clip.period, clock=clock,
                            type=type_, positions=positions or {}, **kw)  # fmt: skip
            out.append(ev)
            return ev

        # Ball path from event locations: passes and carries move it from start to end over their duration.
        ball: list[tuple[float, float, float]] = []
        for e in evs:
            if "location" not in e or "team" not in e:
                continue
            code, t = self.code[e["team"]["name"]], self._clock(e)
            ball.append((t, *self._xy(e["location"], code)))
            end = (e.get("pass") or e.get("carry") or {}).get("end_location")
            if end:
                ball.append((round(t + max(0.2, e.get("duration") or 0.5), 2), *self._xy(end, code)))
        ball.sort()

        def ball_at(t: float) -> tuple[float, float]:
            for a, b in zip(ball, ball[1:], strict=False):
                if a[0] <= t <= b[0]:
                    k = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0
                    return (round(a[1] + (b[1] - a[1]) * k, 2), round(a[2] + (b[2] - a[2]) * k, 2))
            q = ball[0] if t < ball[0][0] else ball[-1]
            return (q[1], q[2])

        goal_t = self._clock(marks["goal"])
        restart_t = self._clock(marks["restart"])
        on_ball = []
        for e in evs:
            kind = {"Pass": "pass", "Carry": "carry", "Shot": "shot", "Ball Recovery": "interception",
                    "Interception": "interception"}.get(e["type"]["name"])  # fmt: skip
            if kind and e.get("player"):
                on_ball.append((self._clock(e), kind, e))

        # Tracking samples while the ball is in play, merged in time order with the on-ball events.
        stamps = []
        spans = [(min(t for t, _, _ in on_ball if t <= goal_t), goal_t + 1.0),
                 (restart_t - 0.5, restart_t + self.clip.after_restart_s)]  # fmt: skip
        for a, b in spans:
            t = math.ceil(a * TRACK_HZ) / TRACK_HZ
            while t <= b:
                stamps.append((round(t, 2), "tracking", None))
                t += 1 / TRACK_HZ
        timeline = sorted(stamps + on_ball, key=lambda r: (r[0], r[1] != "tracking"))

        var_done = False
        for t, kind, e in timeline:
            if not var_done and t >= restart_t:
                self._var(emit, goal_t, restart_t, tracks)
                var_done = True
            pos = self._positions(tracks, t)
            if len(pos) < 14:
                continue  # the camera saw too little here to stand in for a full snapshot
            if kind == "tracking":
                emit("tracking", t, pos, start=ball_at(t))
                continue
            code = self.code[e["team"]["name"]]
            pid = self.pid.get(e["player"]["id"])
            pos.setdefault(pid, self._xy(e["location"], code))
            start = self._xy(e["location"], code)
            kw: dict = {"team": code, "player": pid, "start": start}
            if kind == "pass":
                p = e["pass"]
                end = self._xy(p["end_location"], code)
                rec = p.get("recipient")
                kw |= {"end": end, "receiver": self.pid.get(rec["id"]) if rec else None,
                       "outcome": "incomplete" if p.get("outcome") else "complete",
                       "ball_speed_kmh": self._speed(start, end, e.get("duration"))}  # fmt: skip
                if self._is_checked_touch(e):
                    kw |= self._evidence(e, t, pid, start, ball)
            elif kind == "carry":
                kw["end"] = self._xy(e["carry"]["end_location"], code)
            elif kind == "shot":
                s = e["shot"]
                end = self._xy(s["end_location"][:2], code)
                scored = s["outcome"]["name"] == "Goal"
                ruled_out = scored and self.clip.awarded_goal_sb == e["player"]["name"]  # disallowed on the field
                kw |= {"end": end, "outcome": "ruled_out" if ruled_out else "goal" if scored else "saved",
                       "ball_speed_kmh": self._speed(start, end, e.get("duration"), lo=50.0)}  # fmt: skip
            emit(kind, t, pos, **kw)
        return out, {"names": self.names, "roles": self.roles, "goal_t": goal_t, "restart_t": restart_t}

    def _is_checked_touch(self, e) -> bool:
        return e.get("player", {}).get("name") == self.clip.var_player_sb

    def _evidence(self, e, t: float, pid: str, start, ball) -> dict:
        """The touch VAR checked: its frame (just the player) and the ball's path into it, from event data."""
        before = [b for b in ball if t - 1.5 <= b[0] <= t]
        track, prev = [], None
        for b in before + [(t, *start)]:  # resample the event-data path at 100 Hz
            if prev is not None and b[0] > prev[0]:
                n = max(1, round((b[0] - prev[0]) * 100))
                for i in range(1, n + 1):
                    k = i / n
                    at = [prev[j] + (b[j] - prev[j]) * k for j in range(3)]
                    track.append((round(at[0], 2), round(at[1], 3), round(at[2], 3)))
            prev = b
        return {"frame": BodyFrame(clock=t, body={pid: {"torso": start}}), "ball_track": track or [(t, *start)]}

    @staticmethod
    def _speed(a, b, duration, lo: float = 25.0) -> float:
        d = math.dist(a, b)
        v = d / max(duration or 0.5, 0.3) * 3.6
        return round(min(max(v, lo), 115.0), 1)

    def _var(self, emit, goal_t: float, restart_t: float, tracks) -> None:
        """VAR is not in the event data: the check spans the stoppage between the goal and the restart."""
        c = self.clip
        player = next(pid for sb, pid in ((p["player_name"], self.pid[p["player_id"]]) for t in self.lineups
                                          for p in t["lineup"]) if sb == c.var_player_sb)  # fmt: skip
        emit("var_check", round(goal_t + 1.5, 2), team=c.var_team, player=player, outcome=c.var_check)
        emit("var_decision", round(restart_t - 1.0, 2), team=c.var_team, player=player, outcome=c.decision)
        if c.awarded_goal_sb:
            scorer = next(pid for sb, pid in ((p["player_name"], self.pid[p["player_id"]]) for t in self.lineups
                                              for p in t["lineup"]) if sb == c.awarded_goal_sb)  # fmt: skip
            pos = self._positions(tracks, goal_t)
            emit("goal", round(restart_t - 0.8, 2), pos if len(pos) >= 14 else None, team=c.var_team, player=scorer)
