#!/usr/bin/env python3
"""Read-only status page for the Kingshot Redeemer umbrel app.

Serves a small HTML page (STATUS_PORT, default 8098) showing whether
DISCORD_TOKEN is configured and which players are registered in
/data/botData.json. Started alongside the bot by entrypoint.sh; reachable only
through the umbrel app proxy, so it is visible to users signed in to umbrelOS.

Stdlib only, read-only, writes nothing.
"""

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_FILE = os.environ.get("BOT_DATA_FILE", "/data/botData.json")
PORT = int(os.environ.get("STATUS_PORT", "8098"))
MAX_PLAYERS_SHOWN = 200

CSS = """
:root { color-scheme: light dark; }
body { margin: 0; padding: 2.5rem 1.25rem; font: 15px/1.55 system-ui, -apple-system, sans-serif; display: flex; justify-content: center; }
main { width: 100%; max-width: 44rem; }
h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
.sub { opacity: .62; margin: 0 0 1.4rem; }
.state { display: inline-block; padding: .18rem .65rem; border-radius: 999px; font-size: .8rem; font-weight: 650; }
.state-ok { background: #1a7f37; color: #fff; }
.state-wait { background: #9a6700; color: #fff; }
h2 { font-size: .95rem; margin: 1.6rem 0 .45rem; }
ul { margin: 0; padding-left: 1.15rem; }
li { margin: .15rem 0; }
.muted { opacity: .58; }
footer { margin-top: 2rem; font-size: .8rem; opacity: .58; }
"""


def load_state():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}, []
    if not isinstance(data, dict):
        return {}, []
    config = data.get("botConfig")
    players = data.get("players")
    return (config if isinstance(config, dict) else {}), (players if isinstance(players, list) else [])


def render() -> str:
    token_set = bool(os.environ.get("DISCORD_TOKEN", "").strip())
    config, players = load_state()

    state_class = "state-ok" if token_set else "state-wait"
    state_text = "Running" if token_set else "Waiting for DISCORD_TOKEN"

    if token_set:
        token_line = "Discord token: set"
    else:
        token_line = (
            "Discord token: not set yet. Add it under Settings &rarr; Advanced &rarr; "
            "environment variables (service \"bot\"); the app restarts on its own."
        )

    if config.get("allowed_channel") and config.get("admin_role"):
        setup_line = "Command channel and admin role: configured"
    else:
        setup_line = "Command channel and admin role: not configured yet, run /setup in Discord"

    items = []
    for player in players[:MAX_PLAYERS_SHOWN]:
        nick = html.escape(str(player.get("player_nick") or "unnamed"))
        pid = html.escape(str(player.get("player_id") or "?"))
        items.append(f"<li>{nick} <span class='muted'>({pid})</span></li>")
    if len(players) > MAX_PLAYERS_SHOWN:
        items.append(f"<li class='muted'>{len(players) - MAX_PLAYERS_SHOWN} more not shown</li>")
    if items:
        players_block = "<ul>" + "".join(items) + "</ul>"
    else:
        players_block = "<p class='muted'>No players registered yet. Add them in Discord with /add.</p>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kingshot Redeemer</title>
<style>{CSS}</style>
</head>
<body>
<main>
  <h1>Kingshot Redeemer</h1>
  <p class="sub">Discord bot for bulk gift-code redemption</p>
  <p><span class="state {state_class}">{state_text}</span></p>
  <h2>Setup</h2>
  <ul>
    <li>{token_line}</li>
    <li>{setup_line}</li>
  </ul>
  <h2>Players ({len(players)})</h2>
  {players_block}
  <footer>Manage players in Discord with /add, /remove, /list and /find. Based on JareCoder/KingshotRedeemer.</footer>
</main>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
