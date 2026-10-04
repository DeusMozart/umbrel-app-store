# mozart-opendots image provenance

Built by `.github/workflows/build-opendots.yml` from upstream + two small build-time patches:

- Upstream: https://github.com/CopilotKit/OpenDots (MIT)
- Pinned commit: `c2569bb6a13a22e565cf3eb791c62267d06babb1` ("feat: add Parallel search and extraction to research", Oct 2 2026)
- Build: `mozart-opendots/docker/Dockerfile` (build stage mirrors upstream with one added patch step; runtime stage
  adds `entrypoint.sh`), platform linux/amd64
- Published: `ghcr.io/deusmozart/mozart-opendots:1.0.2`
- Icon: upstream `public/favicon.svg`

## Why the entrypoint patch (1.0.1)
umbrelOS creates app data directories (`/home/umbrel/umbrel/app-data/<id>/data`, surfaced as
`/Apps/<id>/data`) **root-owned**, while upstream's image runs as the `node` user (uid 1000) →
SQLite fails with `unable to open database file` and the container crash-loops. The entrypoint
starts as root, normalizes `/data` ownership (`chown -R node:node`, chmod fallback — the same
handoff pattern used for the OpenMuse browser worker), echoes `ls -ld /data` before/after into the
app logs, then drops to node via `setpriv`. Everything else is upstream verbatim.

## Why the OpenCode patch (1.0.2)
OpenCode Go rejects model requests without a stable `x-opencode-session` header
(HTTP 400 `MissingSessionID`; see https://opencode.ai/docs/go). Upstream OpenDots does not send
it, so `docker/apply-opencode-patch.mjs` runs before `npm run build` and injects the header into
both model call sites (`src/server/dot-agent.ts` → TanStack adapter `defaultHeaders`;
`src/server/research.ts` → spread into the fetch headers). The header is only sent when
`OPENCODE_SESSION_ID` is set (any stable string), so the patch is inert for other providers.
The patch script fails the build loudly if its anchors no longer match — re-validate on every
upstream commit bump.

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
2. Bump the image tag in the workflow AND in `docker-compose.yml` (keep the `docker/` path trigger in mind).
3. Bump `version:` in `umbrel-app.yml` + add release notes.
4. Re-run `node docker/apply-opencode-patch.mjs` against the new commit's checkout to confirm the
   anchors still match before pushing (it must patch both files exactly once).
5. Push → wait for the Actions build (`gh run watch`) → verify the GHCR manifest is anonymously
   pullable → `update_app('mozart-opendots')` via the umbrel MCP, then poll `get_app_status` to `ready`.
