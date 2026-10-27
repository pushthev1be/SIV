"""Record the submission video: under 2:00, 1080p, narrated, captioned, one scene per judging criterion.

    uv run python scripts/record_video.py                 # writes build/video/siv-demo.mp4 and .srt
    uv run python scripts/record_video.py --site http://127.0.0.1:8765/

Each scene drives the deployed demo through the page's window.__siv hooks and captures it with the
Chrome DevTools screencast, so the text stays sharp. Narration comes from ElevenLabs (cached per line in
build/video; set ELEVENLABS_API_KEY unless a proxy adds it). A card in the empty left column names the
judging criterion each scene answers, and the narration is burned in as captions.
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "video"
SITE = "https://green-stone-07a59ad0f.5.azurestaticapps.net/"
VIEWPORT = {"width": 1440, "height": 810}  # x4/3 = 1920x1080
FPS = 30
GAP = 0.35  # seconds of picture after each line
VOICE = "onwK4e9ZLuTAKqWW03F9"  # ElevenLabs "Daniel", steady broadcaster
FONTS = (
    "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700"
    "&family=IBM+Plex+Mono:wght@500&family=IBM+Plex+Sans:wght@400;500;600&family=Unbounded:wght@500;800&display=swap"
)

CRITERIA = {
    1: "Technological implementation",
    2: "Agentic design & innovation",
    3: "Real-world impact",
    4: "User experience & presentation",
    5: "Category: Best Multi-Agent System",
}

FIND = "const V = window.__siv, c = V.calls.find(c => c.iid === '{iid}');"
JS_OFFSIDE = FIND.format(iid="inc-m04-13")  # Brennan, 4 cm, goal ruled out after a 55 s check
JS_JPN = FIND.format(iid="inc-m10-01")  # Mitoma's cross: ball out, goal given
SKIP = "document.getElementById('bumper').click()"  # the bumper plays once, in the replay scene


def bumper_js(scale: float = 1.0, top: str = "50%", freeze: float = 0) -> str:
    """Play the site's own SIV bumper full screen; optionally hold it on the referee framing the VAR screen."""
    hold = f"setTimeout(() => b.getAnimations({{ subtree: true }}).forEach(a => a.pause()), {freeze * 1000:.0f});"
    return f"""const b = document.getElementById('bumper'), root = b.querySelector('.siv-root');
      Object.assign(b.style, {{ position: 'fixed', inset: '0', zIndex: 99998, borderRadius: '0' }});
      const k = Math.min(innerWidth / 1280, innerHeight / 720) * {scale};
      root.style.top = '{top}'; root.style.transform = `scale(${{k}}) translate(-640px, -360px)`;
      b.style.setProperty('--dur', '5s'); b.onclick = null;
      b.classList.remove('on'); void b.offsetWidth; b.classList.add('on'); {hold if freeze else ''}"""


def overlay_js(html: str, css: str) -> str:
    return f"""const st = document.createElement('style'); st.textContent = {json.dumps(css)}; document.head.append(st);
      const ov = document.createElement('div'); ov.className = 'vid-ov'; ov.innerHTML = {json.dumps(html)};
      document.body.append(ov);"""

INTRO_CSS = """
  .vid-ov { position: fixed; inset: 0; z-index: 99999; color: #ececec; transition: opacity .3s;
    background: radial-gradient(ellipse at 50% 40%, #1e3b2a 0%, #121212 70%); }
  .vid-ov.off { opacity: 0 }
  .beat { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 22px; opacity: 0; animation: show var(--d) linear var(--s) both; }
  .b1 { --s: 0s; --d: 3.0s } .b2 { --s: 3.0s; --d: 3.4s } .b3 { --s: 6.4s; --d: 2.3s }
  @keyframes show { 0% { opacity: 0; transform: scale(.98) } 6% { opacity: 1; transform: none }
    94% { opacity: 1 } 100% { opacity: 0 } }
  .tag { font: 700 30px 'Barlow Condensed'; letter-spacing: .3em; padding: 6px 18px; border: 2px solid #ececec;
    border-radius: 6px; display: flex; align-items: center; gap: 12px; }
  .tag.var { border-color: #F2B33D; color: #F2B33D } .tag.red { border-color: #e53935; color: #e53935 }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: #F2B33D; animation: pulse 1s infinite }
  @keyframes pulse { 50% { opacity: .2 } }
  .big { font: 700 96px 'Barlow Condensed'; letter-spacing: .04em }
  .why { font: 400 26px 'IBM Plex Sans'; color: #8a8a8a }
"""
INTRO_HTML = """
  <div class="beat b1"><div class="tag">GOAL</div><div class="big">NBR 1 &ndash; 0 ASH</div></div>
  <div class="beat b2"><div class="tag var"><span class="dot"></span>VAR</div>
    <div class="big">CHECK IN PROGRESS</div></div>
  <div class="beat b3"><div class="tag red">NO GOAL</div><div class="big">NBR 0 &ndash; 0 ASH</div>
    <div class="why">Why? No reason. No evidence.</div></div>"""
# The beats, then the SIV bumper wipes in on "SIV fixes that."
INTRO_JS = overlay_js(INTRO_HTML, INTRO_CSS) + f"""
      setTimeout(() => {{ {bumper_js()} }}, 8600);
      setTimeout(() => ov.classList.add('off'), 8700);"""

OUTRO_CSS = """
  .vid-ov { position: fixed; left: 0; right: 0; bottom: 92px; z-index: 99999; text-align: center;
    opacity: 0; animation: up .6s ease-out 1.4s both; }
  @keyframes up { from { opacity: 0; transform: translateY(8px) } to { opacity: 1; transform: none } }
  .vid-ov .l { font: 500 20px/1.7 'IBM Plex Mono', monospace; color: #12324a; }
  .vid-ov .b { margin-top: 10px; font: 500 14px 'IBM Plex Sans'; color: #1e5a8a; letter-spacing: .04em; }
"""
OUTRO_HTML = """<div class="l">green-stone-07a59ad0f.5.azurestaticapps.net<br>github.com/pushthev1be/SIV</div>
  <div class="b">Microsoft Foundry &middot; Agent Framework &middot; MCP &middot; Azure Event Hubs
    &middot; Static Web Apps</div>"""
# Held on the referee framing the VAR screen, with the links under it
OUTRO_JS = bumper_js(scale=0.78, top="43%", freeze=3.3) + overlay_js(OUTRO_HTML, OUTRO_CSS)

# (name, narration, page, setup js, cards as [(at seconds, criterion, note)])
SCENES = [
    ("intro",
     "A goal goes in. The screen says: check in progress. A minute later, it's gone, "
     "and nobody can tell you why. SIV fixes that.",
     "var_showcase&v=fan", INTRO_JS, []),
    ("check",
     "This is Japan against Spain at the 2022 World Cup, rebuilt from StatsBomb open data. "
     "While the check runs, SIV tells fans what is being checked, and under which law. Never the answer early.",
     "real_japan_spain&v=fan",
     f"{JS_JPN} V.showPane('calls'); V.seek(c.opened - 4); V.play(true, 1);",
     [(0, 4, "A live VAR check, from real match data"), (8.5, 4, "Nothing about the decision airs early")]),
    ("replay",
     "When the officials decide, the evidence plays: the build-up, the deciding frame, the ball against "
     "the line. Here the data is only good to ten centimetres, so SIV says so, instead of inventing a margin.",
     "real_japan_spain&v=fan",
     f"{JS_JPN} V.showPane('calls'); V.seek(c.decided + 0.1); V.openReplay(c.iid);",
     [(0, 3, "Fans get the reason and the evidence"), (9, 3, "Honest about what the data can settle")]),
    ("measure",
     "Every number is measured from player positions, never stated. A seeded simulator, running real "
     "tactical play scripts, gives matches where the truth is known. Here, Brennan is four centimetres offside.",
     "var_showcase&v=analyst",
     f"{JS_OFFSIDE} V.showPane('buddy'); V.seek(c.decided + 0.2); V.openReplay(c.iid); "
     f"{SKIP}; V.R.rt = V.R.pkg.key_t - 6;",
     [(0, 1, "Measured from positions, never stated"), (6, 1, "Simulated matches where the truth is known")]),
    ("modes",
     "One match, three audiences, from the same facts. Plain words for a casual fan. "
     "The margin and the law for an analyst. Or follow a single player.",
     "var_showcase&v=fan",
     f"""{JS_OFFSIDE} V.showPane('calls'); V.seek(c.decided + 0.2); V.openReplay(c.iid);
         {SKIP}; V.R.rt = V.R.pkg.key_t - 1;
         setTimeout(() => V.setMode('fan'), 3000);
         setTimeout(() => V.setMode('analyst'), 5600);
         setTimeout(() => V.setMode('player'), 8600);""",
     [(0, 4, "Viewing as: one switch, same facts"), (3, 4, "Viewing as: Casual fan"),
      (5.6, 4, "Viewing as: Analyst"), (8.6, 4, "Viewing as: Player-follow")]),
    ("agents",
     "Behind it is a Microsoft Agent Framework workflow on Foundry. A Decision Desk agent reads the call "
     "over MCP. A code Verifier checks every number, and fails closed: here, a planted wrong number is "
     "rejected twice, and never airs. Four persona agents then write in parallel, and an Editor agent "
     "checks the meaning.",
     "real_japan_spain&v=fan",
     f"""{JS_JPN} V.seek(c.decided + 0.1); V.showPane('desk');
         const mark = (re, on) => document.querySelectorAll('#desk li').forEach(li => {{
           if (re.test(li.textContent)) li.classList.toggle('vid-hl', on); }});
         setTimeout(() => mark(/Decision Desk read/, true), 3600);
         setTimeout(() => {{ mark(/Decision Desk read/, false); mark(/planted|rejected|Fallback/, true); }}, 6400);
         setTimeout(() => {{ mark(/planted|rejected|Fallback/, false);
                             mark(/passed|parallel|Published/, true); }}, 15500);
         const tab = (t) => [...document.querySelectorAll('#desk button')]
           .find(b => b.textContent.trim() === t)?.click();
         setTimeout(() => tab('Analyst'), 16500); setTimeout(() => tab('Español'), 18500);
         setTimeout(() => tab('Ref Buddy'), 20500);""",
     [(0, 2, "Microsoft Agent Framework on Foundry"), (6.4, 2, "Code verifies every claim, fails closed"),
      (15.5, 5, "Three agents, eight executors, fan-out / fan-in")]),
    ("eval",
     "And it's measured. Across thirty simulated matches, SIV flagged all twenty-four wrong VAR decisions, "
     "and none of the forty-eight correct ones. The Verifier caught ninety-nine percent of planted errors.",
     "eval.html",
     """setTimeout(() => window.scrollTo({ top: 640, behavior: 'smooth' }), 9500);""",
     [(0, 3, "Trust you can check: 24 of 24 wrong calls flagged"), (9.5, 1, "Reproducible: scripts/evaluate.py")]),
    ("outro", "SIV. Every call, explained.", "var_showcase&v=fan", OUTRO_JS, []),
]

CARD_CSS = """
  .vid-card { position: fixed; left: 16px; top: 560px; width: 176px; z-index: 99999; padding: 12px 13px 13px;
    background: #1a1a1a; border: 1px solid #2e2e2e; border-left: 3px solid #F2B33D; border-radius: 8px;
    font-family: 'IBM Plex Sans', sans-serif; color: #ececec; box-shadow: 0 8px 24px rgba(0,0,0,.35);
    transition: opacity .35s, transform .35s; }
  .vid-card.off { opacity: 0; transform: translateY(6px); }
  .vid-card .k { font: 500 9.5px 'IBM Plex Mono', monospace; letter-spacing: .14em; color: #8a8a8a; }
  .vid-card .n { font: 700 34px/1 'Barlow Condensed', sans-serif; color: #F2B33D; margin: 6px 0 2px; }
  .vid-card .t { font: 700 19px/1.1 'Barlow Condensed', sans-serif; letter-spacing: .02em; text-transform: uppercase; }
  .vid-card .w { font-size: 12.5px; line-height: 1.35; color: #bdbdbd; margin-top: 8px; }
  #desk li.vid-hl { background: rgba(242,179,61,.14); box-shadow: inset 3px 0 0 #F2B33D; border-radius: 4px;
    transition: background .3s; }
"""

def chromium_path() -> str | None:
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))
    return found[-1] if found else None


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout)


def narrate(name: str, text: str) -> Path:
    """One ElevenLabs clip per line, cached by its text."""
    mp3, key = OUT / f"vo_{name}.mp3", OUT / f"vo_{name}.txt"
    if mp3.exists() and key.exists() and key.read_text() == text:
        return mp3
    body = {
        "text": text, "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.15, "speed": 1.07},
    }
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=mp3_44100_128",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")},
    )
    with urllib.request.urlopen(req) as r:
        mp3.write_bytes(r.read())
    key.write_text(text)
    return mp3


def card_js(cards: list[tuple[float, int, str]]) -> str:
    """Show the judging-criterion card in the left column, changing at the given times."""
    steps = json.dumps([[at * 1000, n, CRITERIA[n], note] for at, n, note in cards])
    return f"""(() => {{
      const st = document.createElement('style'); st.textContent = {json.dumps(CARD_CSS)}; document.head.append(st);
      const el = document.createElement('div'); el.className = 'vid-card off'; document.body.append(el);
      const show = ([, n, t, w]) => {{
        el.innerHTML = `<div class="k">JUDGING CRITERION</div><div class="n">${{n}}</div>`
          + `<div class="t">${{t}}</div><div class="w">${{w}}</div>`;
        el.classList.remove('off'); }};
      {steps}.forEach((s, i) => setTimeout(() => show(s), s[0] + (i ? 0 : 250)));
    }})()"""


def record(browser, site: str, scene: tuple, seconds: float, frames: Path) -> list[tuple[float, Path]]:
    name, _, where, setup, cards = scene
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=4 / 3)
    page = ctx.new_page()
    if where.endswith(".html"):
        page.goto(site + where)
        page.wait_for_timeout(1500)
    else:
        page.goto(f"{site}?s={where}")
        page.wait_for_function("() => window.__siv && window.__siv.ready", timeout=60_000)
        page.wait_for_timeout(1000)
    page.add_style_tag(url=FONTS)
    if cards:
        page.evaluate(card_js(cards))

    cdp = ctx.new_cdp_session(page)
    shots: list[tuple[float, Path]] = []
    live = [True]  # frames still in flight when the scene ends are dropped

    def on_frame(e: dict) -> None:
        if not live[0]:
            return
        path = frames / f"{name}_{len(shots):05d}.jpg"
        path.write_bytes(base64.b64decode(e["data"]))
        shots.append((e["metadata"]["timestamp"], path))
        cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]})

    cdp.on("Page.screencastFrame", on_frame)
    cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080})
    if setup:
        page.evaluate(f"() => {{ {setup} }}")
    page.wait_for_timeout(int(seconds * 1000) + 300)
    live[0] = False
    cdp.send("Page.stopScreencast")
    ctx.close()
    return shots


def encode(shots: list[tuple[float, Path]], audio: Path, seconds: float, out: Path) -> None:
    """Frames at their real timestamps -> constant 30 fps, with the line padded to the scene's length."""
    concat = out.with_suffix(".txt")
    t0 = shots[0][0]
    lines = ["ffconcat version 1.0"]
    for i, (t, path) in enumerate(shots):
        nxt = shots[i + 1][0] if i + 1 < len(shots) else t0 + seconds + 1
        lines += [f"file '{path}'", f"duration {max(nxt - t, 0.001):.4f}"]
    lines.append(f"file '{shots[-1][1]}'")
    concat.write_text("\n".join(lines) + "\n")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(audio),
         "-filter_complex", f"[0:v]scale=1920:1080:flags=lanczos,fps={FPS},format=yuv420p[v];"
                            f"[1:a]aresample=48000,apad[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{seconds:.3f}",
         "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-c:a", "aac", "-b:a", "192k", "-ac", "2", str(out)],
        check=True,
    )


def ts(t: float, sep: str) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if sep == ",":  # SRT
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"  # ASS


def pauses(mp3: Path) -> list[float]:
    """Midpoints of the pauses in a narration clip."""
    log = subprocess.run(["ffmpeg", "-i", str(mp3), "-af", "silencedetect=n=-40dB:d=0.2", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    return [(a + b) / 2 for a, b in zip(starts, ends, strict=False)]  # a trailing pause has no end


def captions(timed: list[tuple[float, float, str, Path]]) -> tuple[str, str]:
    """Split each line into sentences; each break lands on the pause nearest its share of the line."""
    cues = []
    for start, spoken, text, mp3 in timed:
        parts = [p for p in re.split(r"(?<=[.?!:])\s+", text) if p]
        gaps = pauses(mp3)
        total, done, t = sum(len(p) for p in parts), 0, 0.0
        for i, p in enumerate(parts):
            done += len(p)
            end = spoken
            if i < len(parts) - 1:
                guess = spoken * done / total
                end = min(gaps, key=lambda g: abs(g - guess)) if gaps else guess
            cues.append((start + t, start + end, p))
            t = end
    srt = "\n".join(f"{i}\n{ts(a, ',')} --> {ts(b, ',')}\n{p}\n" for i, (a, b, p) in enumerate(cues, 1))
    ass = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Inter,34,&H00ECECEC,&H00ECECEC,&H66000000,&H66000000,0,0,0,0,100,100,0,0,3,10,0,2,360,360,22,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "".join(f"Dialogue: 0,{ts(a, '.')},{ts(b, '.')},Cap,,0,0,0,,{p}\n" for a, b, p in cues)
    )
    return srt, ass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", default=SITE, help="the demo to record (default: the deployed one)")
    ap.add_argument("--only", help="record just this scene, for checking timings")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH")
    OUT.mkdir(parents=True, exist_ok=True)

    plan = []
    for scene in SCENES:
        mp3 = narrate(scene[0], scene[1])
        spoken = duration(mp3)
        plan.append((scene, mp3, spoken, spoken + (2.8 if scene[0] == "outro" else GAP)))
    total = sum(p[3] for p in plan)
    print(f"{len(plan)} scenes, {total:.1f} s")
    if total >= 120:
        raise SystemExit("over two minutes: shorten a line")

    parts = []
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium_path())
        for scene, mp3, _, seconds in plan:
            if args.only and scene[0] != args.only:
                continue
            frames = Path(tmp) / scene[0]
            frames.mkdir()
            shots = record(browser, args.site, scene, seconds, frames)
            part = OUT / f"scene_{scene[0]}.mp4"
            encode(shots, mp3, seconds, part)
            parts.append(part)
            print(f"  {scene[0]:8s} {seconds:5.1f} s  {len(shots)} frames")
        browser.close()
    if args.only:
        return

    timed, t = [], 0.0
    for scene, mp3, spoken, seconds in plan:
        timed.append((t, spoken, scene[1], mp3))
        t += seconds
    srt, ass = captions(timed)
    (OUT / "siv-demo.srt").write_text(srt)
    (OUT / "captions.ass").write_text(ass)

    joined = OUT / "joined.mp4"
    (OUT / "parts.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(OUT / "parts.txt"),
                    "-c", "copy", str(joined)], check=True)
    final = OUT / "siv-demo.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined),
         "-vf", f"fade=in:0:12,fade=out:st={total - 0.6:.2f}:d=0.6,ass={OUT / 'captions.ass'}",
         "-af", f"loudnorm=I=-16:TP=-1.5:LRA=11,afade=out:st={total - 0.6:.2f}:d=0.6", "-ar", "48000",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final)],
        check=True,
    )
    print(f"wrote {final.relative_to(ROOT)} ({duration(final):.1f} s, {final.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
