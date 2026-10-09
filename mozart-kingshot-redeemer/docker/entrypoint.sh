#!/bin/bash
# Kingshot Redeemer entrypoint (umbrel packaging).
#
# Upstream src/config/config.py raises if DISCORD_TOKEN is missing, which
# crash-loops the container on a fresh install. Keep the app up and idle
# instead, and say what to do: save the token under Settings -> Advanced ->
# environment variables (service "bot"); umbrel restarts the app on its own
# and this entrypoint then starts the bot.
set -eu

# Small read-only status page (STATUS_PORT, proxied by the app tile).
python /status.py &

if [ -z "${DISCORD_TOKEN:-}" ]; then
  echo "[kingshot-redeemer] DISCORD_TOKEN is not set yet."
  echo "[kingshot-redeemer] Add it under Settings -> Advanced -> environment variables (service \"bot\"); the app restarts on its own after saving."
  echo "[kingshot-redeemer] Idling until then; the status page stays up."
  while true; do sleep 3600; done
fi

echo "[kingshot-redeemer] DISCORD_TOKEN is set, starting the bot."
exec "$@"
