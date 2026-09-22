#!/bin/bash
# Start the OpenMuse API (loopback) and the web+proxy front (0.0.0.0:8080).
# If either process exits, tear both down so the container restarts as a unit.
set -euo pipefail

export HOST="${HOST:-127.0.0.1}"

# The browser worker runs as pwuser (uid 1000); umbrel creates bind-mounted app
# data as root. Hand the shared profiles directory over so sessions can be created.
if [ -d /browser-profiles ]; then
  echo "[entrypoint] browser-profiles before: $(ls -ld /browser-profiles)"
  chown -R 1000:1000 /browser-profiles 2>/dev/null || true
  chmod -R a+rwX /browser-profiles 2>/dev/null || true
  echo "[entrypoint] browser-profiles after: $(ls -ld /browser-profiles)"
fi

node /app/dist/apps/server/src/index.js &
api=$!
node /opt/proxy.mjs &
web=$!

trap 'kill "$api" "$web" 2>/dev/null || true' TERM INT
wait -n "$api" "$web" || true
kill "$api" "$web" 2>/dev/null || true
exit 1
