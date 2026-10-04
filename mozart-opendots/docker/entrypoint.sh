#!/bin/bash
# mozart-opendots entrypoint.
# umbrelOS creates the mounted app data directory root-owned while the server
# runs as the node user (uid 1000) — normalize /data first, then drop to node.
set -e

if [ "$(id -u)" = "0" ]; then
  if [ -d /data ]; then
    echo "[entrypoint] /data before: $(ls -ld /data)"
    chown -R node:node /data 2>/dev/null || true
    chmod -R a+rwX /data 2>/dev/null || true
    echo "[entrypoint] /data after: $(ls -ld /data)"
  fi
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=1000 --regid=1000 --clear-groups /usr/local/bin/node dist/server/server/index.js
  fi
  echo "[entrypoint] setpriv unavailable; continuing as root"
fi

exec node dist/server/server/index.js
