#!/usr/bin/env bash
# Scaffold a new WP site app in this store from the mozart-wp-sandbox template.
#
# Usage:   scripts/new-wp-site.sh <slug> <port> "<Site Title>" ["<tagline>"]
# Example: scripts/new-wp-site.sh jandl 8603 "J&L Interiors copy"
#
# After it pushes, the Umbrel picks the app up within ~5 minutes; then install
# it (App Store -> your store -> the new app, or headlessly via the Umbrel MCP
# install_app) and read the [magic-link] line from the app logs.
set -euo pipefail

SLUG="${1:?usage: new-wp-site.sh <slug> <port> \"<Site Title>\" [tagline]}"
PORT="${2:?port required (8603, 8604, ...)}"
TITLE="${3:?site title required}"
TAGLINE="${4:-Local WordPress site for backups, staging & testing}"

cd "$(dirname "$0")/.."

APP="mozart-wp-${SLUG}"
[ -e "$APP" ] && { echo "ERROR: $APP already exists" >&2; exit 1; }
if grep -Rqs "^port: ${PORT}$" --include=umbrel-app.yml .; then
  echo "ERROR: port ${PORT} is already used by another app" >&2; exit 1
fi

cp -R mozart-wp-sandbox "$APP"

# Compose file: swap app id (container names + DB host), port and site title.
sed -i \
  -e "s/mozart-wp-sandbox/${APP}/g" \
  -e "s|http://umbrel.local:8601|http://umbrel.local:${PORT}|g" \
  -e "s/WP Sandbox/${TITLE}/g" \
  "$APP/docker-compose.yml"

# Manifest: swap id, port, title, tagline; fresh release notes.
sed -i \
  -e "s/mozart-wp-sandbox/${APP}/g" \
  -e "s/^port: 8601$/port: ${PORT}/" \
  -e "s/^tagline: .*/tagline: ${TAGLINE}/" \
  -e "s/^version: .*/version: \"1.0.0\"/" \
  -e "s/WP Sandbox/${TITLE}/g" \
  "$APP/umbrel-app.yml"
python3 - "$APP/umbrel-app.yml" <<'PYEOF'
import re, sys
p = sys.argv[1]
t = open(p).read()
t = re.sub(r'releaseNotes: >-\n(?:  .*\n?)+', 'releaseNotes: >-\n  First release.\n', t)
open(p, 'w').write(t)
PYEOF

echo "Created $APP (port ${PORT})"
echo
echo "Next steps:"
echo "  1. git add -A && git commit -m \"Add ${APP}\" && git push"
echo "  2. wait <=5 min for the Umbrel store refetch"
echo "  3. install: App Store -> $APP, or headless: install_app('${APP}')"
echo "  4. read [magic-link] from the app logs for sign-in"
