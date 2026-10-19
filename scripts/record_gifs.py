"""Record looping GIFs of the preview for the README.

    uv run python -m engine.export golden
    uv run python scripts/record_gifs.py            # writes docs/media/*.gif

Each clip is scripted against the page's window.__varNarrative hooks, recorded by headless Chromium,
then cropped, trimmed and palette-encoded by ffmpeg. Same data in, same GIFs out.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "web" / "preview"
OUT = ROOT / "docs" / "media"
VIEWPORT = {"width": 1320, "height": 860}
FPS = 12
WIDTH = 880  # GIF width in pixels; 12 fps keeps each clip a few MB


def serve() -> tuple[http.server.ThreadingHTTPServer, str]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(SITE))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/?s=golden"


def open_call(page, label: str) -> None:
    js = "(l) => [...document.querySelectorAll('#jumps button')].find(b => b.textContent.includes(l)).click()"
    page.evaluate(js, label)


# Each scene: (name, selector to crop to, setup(page), seconds to record after setup)
def scene_offside(page) -> None:
    open_call(page, "VAR offside")
    page.evaluate("() => { const R = window.__varNarrative.R; R.rt = R.pkg.key_t - 3.0; }")


def scene_callouts(page) -> None:
    open_call(page, "VAR offside")
    page.evaluate("() => { const R = window.__varNarrative.R; R.rt = R.pkg.frames[0].t + 1.0; }")


def scene_live_var(page) -> None:
    page.evaluate(
        """() => { const T = window.__varNarrative, at = T.replayAt;
                   const iid = Object.keys(at).find(k => at[k].during != null);
                   T.seek(at[iid].during - 13); T.play(true, 10); }"""
    )


def scene_player(page) -> None:
    page.evaluate(
        "() => { const T = window.__varNarrative; T.seek(3700); T.selectPlayer('NBR-9'); T.play(true, 5); }"
    )


SCENES = [
    ("offside-replay", ".stage", scene_offside, 14.0),
    ("hud-callouts", ".stage", scene_callouts, 7.0),
    ("live-var-check", ".stage", scene_live_var, 10.0),
    ("player-card", ".wrap", scene_player, 8.0),
]


def record(url: str, name: str, selector: str, setup, seconds: float, tmp: Path) -> Path:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=VIEWPORT, record_video_dir=str(tmp), record_video_size=VIEWPORT)
        page = ctx.new_page()
        t0 = time.monotonic()
        page.goto(url)
        page.wait_for_function("() => window.__varNarrative && window.__varNarrative.ready", timeout=60_000)
        page.wait_for_timeout(800)  # fonts and first frame
        box = page.locator(selector).first.bounding_box()
        setup(page)
        start = time.monotonic() - t0
        page.wait_for_timeout(int(seconds * 1000))
        video = Path(page.video.path())
        ctx.close()
        browser.close()

    out = OUT / f"{name}.gif"
    x, y, w, h = (int(round(v)) for v in (box["x"], box["y"], box["width"], box["height"]))
    w, h = min(w, VIEWPORT["width"] - x) // 2 * 2, min(h, VIEWPORT["height"] - y) // 2 * 2
    vf = f"crop={w}:{h}:{x}:{y},fps={FPS},scale={WIDTH}:-1:flags=lanczos"
    palette = tmp / f"{name}-palette.png"
    common = ["-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(video)]
    subprocess.run(
        ["ffmpeg", *common, "-vf", f"{vf},palettegen=max_colors=128:stats_mode=diff", str(palette)],
        check=True,
    )
    use = f"{vf}[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    subprocess.run(["ffmpeg", *common, "-i", str(palette), "-lavfi", use, "-loop", "0", str(out)], check=True)
    return out


def main() -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH")
    if not (SITE / "data" / "golden.json").exists():
        raise SystemExit("run `uv run python -m engine.export golden` first")
    OUT.mkdir(parents=True, exist_ok=True)
    httpd, url = serve()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for name, selector, setup, seconds in SCENES:
                out = record(url, name, selector, setup, seconds, Path(tmp))
                print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} MB)")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
