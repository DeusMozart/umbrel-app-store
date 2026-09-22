#!/bin/bash
# Start the OpenMuse API (loopback) and the web+proxy front (0.0.0.0:8080).
# If either process exits, tear both down so the container restarts as a unit.
set -euo pipefail

export HOST="${HOST:-127.0.0.1}"

node /app/dist/apps/server/src/index.js &
api=$!
node /opt/proxy.mjs &
web=$!

trap 'kill "$api" "$web" 2>/dev/null || true' TERM INT
wait -n "$api" "$web" || true
kill "$api" "$web" 2>/dev/null || true
exit 1
