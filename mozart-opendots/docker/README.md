# mozart-opendots image provenance

Built by `.github/workflows/build-opendots.yml` directly from upstream — no local patches:

- Upstream: https://github.com/CopilotKit/OpenDots (MIT)
- Pinned commit: `c2569bb6a13a22e565cf3eb791c62267d06babb1` ("feat: add Parallel search and extraction to research", Oct 2 2026)
- Build: upstream `Dockerfile`, stage `app` (`target: app`), platform linux/amd64
- Published: `ghcr.io/deusmozart/mozart-opendots:1.0.0`
- Icon: upstream `public/favicon.svg`

## Why `${APP_PASSWORD}` is the owner token
`src/server/index.ts` refuses to bind an external HOST without `OWNER_TOKEN` (≥24 chars), so the
container cannot start with `HOST=0.0.0.0` unless a token is set. Umbrel derives `$APP_PASSWORD`
deterministically (HMAC-SHA256 of the umbrel seed, identifier `app-mozart-opendots-seed-APP_PASSWORD`)
and — because `deterministicPassword: true` is set in `umbrel-app.yml` — displays that same value as
the app's password in the UI. Wiring it into `OWNER_TOKEN` means the user unlocks OpenDots with the
password Umbrel already shows them; no manual env editing needed for access.

## Design notes
- Origin handling needs nothing extra: umbreld's app gateway proxies with `changeOrigin: false`, so
  the app sees the browser's Host/Origin and its same-origin checks pass. Only if umbrel ever starts
  rewriting Host would `APP_ORIGIN=http://umbrel.local:4310` become necessary.
- Voice needs a secure context (HTTPS) for the microphone; it will not work over plain
  `http://umbrel.local` LAN access.
- Computer tools are intentionally NOT enabled: they require OpenBot's supervisor with the Docker
  socket mounted, which reaches every umbrel app plus the Hermes VM. Enable deliberately, not by default.
- Data: SQLite at `/data/opendots.sqlite` inside `/Apps/mozart-opendots/data` (internal SSD).

## Updating
1. Bump the pinned `ref:` in the workflow to the new upstream commit.
2. Bump the image tag in the workflow AND in `docker-compose.yml`.
3. Bump `version:` in `umbrel-app.yml` + add release notes.
4. Push → wait for the Actions build (`gh run watch`) → verify the GHCR manifest is anonymously
   pullable → `update_app('mozart-opendots')` via the umbrel MCP, then poll `get_app_status` to `ready`.
