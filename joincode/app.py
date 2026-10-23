"""A tiny page that always shows the Dragonwilds server's current join code.

It reads the code straight from the live server log (mounted read-only), so it is
never stale: the code changes only on a restart, and the new one appears here on its
own. The join password is deliberately NOT shown — players already have it, so the
public URL alone can't get anyone in.
"""

import glob
import html
import os
import re
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

LOGS_DIR = os.environ.get("DW_LOGS_DIR", "/data/RSDragonwilds/Saved/Logs")
WORLD_NAME = os.environ.get("DW_WORLD_NAME", "WorldWarWirley")
CACHE_TTL = float(os.environ.get("DW_CACHE_TTL", "20"))  # seconds; the code only changes on restart

# Matches e.g. `Setting ["JoinCode"] written with key[xz] value[W63Z-6YZ4]`.
_JOIN_CODE = re.compile(r"JoinCode[^\n]*?value\[([A-Z0-9]{4}-[A-Z0-9]{4})\]")
_cache: tuple[float, str | None] = (0.0, None)


def read_latest_code(logs_dir: str = LOGS_DIR) -> str | None:
    """The most recent JoinCode in the current run's log, or None if not logged yet."""
    logs = glob.glob(os.path.join(logs_dir, "RSDragonwilds*.log"))
    if not logs:
        return None
    newest = max(logs, key=os.path.getmtime)  # the active run writes most recently
    try:
        with open(newest, encoding="utf-8", errors="replace") as handle:
            matches = _JOIN_CODE.findall(handle.read())
    except OSError:
        return None
    return matches[-1] if matches else None


def current_code() -> str | None:
    global _cache
    now = time.monotonic()
    ts, code = _cache
    if now - ts < CACHE_TTL:
        return code
    code = read_latest_code()
    _cache = (now, code)
    return code


app = FastAPI(title="Dragonwilds join code", docs_url=None, redoc_url=None)

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<meta name="robots" content="noindex, nofollow">
<title>{world} — join code</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0f1115; color: #e8eaed;
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    padding: 24px;
  }}
  .card {{
    width: 100%; max-width: 420px; text-align: center;
    background: #171a21; border: 1px solid #262b36; border-radius: 16px;
    padding: 32px 24px; box-shadow: 0 10px 40px rgba(0,0,0,.35);
  }}
  .world {{ font-size: 14px; letter-spacing: .08em; text-transform: uppercase; color: #8a93a6; }}
  .label {{ margin-top: 20px; font-size: 13px; color: #8a93a6; }}
  .code {{
    font: 700 clamp(34px, 12vw, 52px)/1.1 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    letter-spacing: .06em; margin: 6px 0 18px; color: #fff; user-select: all;
  }}
  button {{
    font: inherit; cursor: pointer; border: 1px solid #39465c; border-radius: 10px;
    background: #233049; color: #dce6ff; padding: 10px 18px;
  }}
  button:active {{ transform: translateY(1px); }}
  .pw {{ margin-top: 22px; font-size: 14px; color: #aab2c2; }}
  .pw b {{ color: #e8eaed; }}
  .muted {{ margin-top: 18px; font-size: 12px; color: #5f6878; }}
  .down .code {{ font-size: 24px; color: #c8ccd4; }}
</style>
</head>
<body>
  <main class="card {state}">
    <div class="world">{world}</div>
    <div class="label">{label}</div>
    <div class="code" id="code">{code}</div>
    {button}
    <div class="pw">Join with the <b>usual password</b>.</div>
    <div class="muted">This page updates on its own. The code changes each restart.</div>
  </main>
  <script>
    const btn = document.getElementById("copy");
    const code = document.getElementById("code");
    if (btn) btn.addEventListener("click", () => {{
      navigator.clipboard.writeText(code.textContent.trim()).then(() => {{
        btn.textContent = "Copied!";
        setTimeout(() => {{ btn.textContent = "Copy code"; }}, 1500);
      }}).catch(() => {{}});
    }});
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    code = current_code()
    if code:
        page = _PAGE.format(
            world=html.escape(WORLD_NAME),
            label="Current join code",
            code=html.escape(code),
            button='<button id="copy">Copy code</button>',
            state="",
        )
    else:
        page = _PAGE.format(
            world=html.escape(WORLD_NAME),
            label="Status",
            code="starting up…",
            button="",
            state="down",
        )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/code", response_class=PlainTextResponse)
def code_text() -> str:
    """Plain-text current code (or empty), for scripts/pbcopy."""
    return current_code() or ""


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
