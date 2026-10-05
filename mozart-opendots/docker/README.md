# mozart-opendots image provenance

Built by `.github/workflows/build-opendots.yml` from upstream + seven small build-time patches:

- Upstream: https://github.com/CopilotKit/OpenDots (MIT)
- Pinned commit: `c2569bb6a13a22e565cf3eb791c62267d06babb1` ("feat: add Parallel search and extraction to research", Oct 2 2026)
- Build: `mozart-opendots/docker/Dockerfile` (build stage mirrors upstream with added patch steps (OpenCode
  header + computers endpoint + chat UUID fallback + make-it-yours pack); runtime stage
  adds `entrypoint.sh`), platform linux/amd64
- Published: `ghcr.io/deusmozart/mozart-opendots:1.1.0`
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

## Why the computers patch (1.0.3)
Per-Dot computers are served by an OpenBot supervisor (pinned `b6932d31`), deployed separately from this
app — on the ubuntu-server VM at `10.203.0.2`, not on the umbrel, so the Docker socket a supervisor must
hold stays off the household box (only the supervisor holds it; computers never do). In that split
topology the supervisor returns computer URLs on its own host (`http://10.203.0.2:<port>`), which
upstream's endpoint check rejects (it accepts only a shared-network container name, or loopback when the
supervisor itself is on loopback). `docker/apply-computers-patch.mjs` widens the check to accept exactly
the supervisor's own hostname; single-host behaviour is unchanged and any other host is still rejected.
Enabled by setting `COMPUTER_SUPERVISOR_URL`, `COMPUTER_SUPERVISOR_TOKEN`, `COMPUTER_TOKEN` (and
optionally `COMPUTER_NAMESPACE`) in the app's environment; off until then. Transport: the app
reaches the supervisor over the tailnet — umbrelOS's machine network (libvirt, port-isolated NAT)
rejects app-container traffic to 10.203.0.x outright, and tailscale is the path the house already
uses for VM services. The supervisor side lives in `~/opendots-computers` on the VM (see its README
there).

## Why the computers fallback patch (1.0.4)
The 1.0.3 patch widened the endpoint check to the supervisor's host; the URL fallback in the same
function still assumed loopback for a supervisor response that carries only a port. 1.0.4 extends
`apply-computers-patch.mjs` to use the supervisor's host there too, so a supervisor that omits the
URL field cannot make the app reject a valid computer. Pairs with the supervisor-side fix (its
computer list now includes the URL) from `~/opendots-computers` on the VM.

## Why the chat UUID patch (1.0.5)
Upstream's chat composer builds every outbound user message with `crypto.randomUUID()`
(`src/client/Chat.tsx`). Browsers expose `crypto.randomUUID` only in **secure contexts**; over
plain `http://umbrel.local:4310` it is `undefined`, so the first send threw *before* the message
was added — silently locking the composer (`running` stuck true, the button showing "Stop
response", "thinking" forever, every later send a no-op) while the server side stayed healthy.
`docker/apply-crypto-patch.mjs` swaps the call for a local helper that prefers
`crypto.randomUUID` and falls back to a v4-shaped id, so sends work over http and https alike.
Same fail-closed anchor pattern as the other patches. (Worth reporting upstream: any non-secure
deployment — LAN, reverse-proxy without TLS — hits this.)

## The make-it-yours pack (1.1.0)
Four patches that add the owner conveniences upstream (pinned c2569bb) does not have — all fail-closed
and idempotent, all verified to compile against the pinned source (`npm ci && npm run build` + `tsc --noEmit`).

- **`apply-delete-dot-patch.mjs` — delete a Dot.** Upstream has no delete path at all (no UI, no route,
  no store method). Adds `DELETE /dots/:id` + a two-step "Delete this Dot" control in the Dot editor.
  Cascades the Dot's conversations (thread_bindings), captures, calls, scheduled tasks (+runs/events),
  space links, computer permissions/audit, then the Dot row — and best-effort resets the Dot's computer
  via the supervisor (`/computers/:id/reset`); deletion still proceeds when the supervisor is offline.
- **`apply-reasoning-patch.mjs` — reasoning view.** Reasoning-capable models stream a reasoning trace
  that OpenDots persists as messages with role `reasoning`, then filters out of the transcript. The patch
  includes them in the visible transcript and renders each as a collapsed `<details>` disclosure.
- **`apply-dot-model-patch.mjs` — per-Dot model.** Adds a `dots.model` column (+migration), accepts
  `model` in the dot schema, uses `dot.model` in the TanStack adapter when set (falls back to
  `OPENAI_MODEL`), and adds `GET /models` (provider model list, 5-min cache, key never leaves the server)
  plus a Model select in the Dot editor.
- **`apply-computer-ui-patch.mjs` — computer panel in the Dot editor.** Computers are configured at the
  deployment level (env vars), so nothing can "add" one per Dot — but the editor now shows the computer's
  state and permission summary with Start/Stop buttons reusing the existing `/dots/:id/computer` routes.
  Start requires permissions to be enabled first (the app's safety design — granted in the chat's computer
  panel); when they are not, the section says so instead of offering a button that would fail.

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
- Computer tools stay off unless `COMPUTER_*` env is set. The supervisor intentionally runs on the VM
  (not on umbrel): the umbrel arrangement would mount umbrel's Docker socket (reaching every app), which
  is rejected for the household box. On the VM only the supervisor holds a Docker socket; computers do not.
- Data: SQLite at `/data/opendots.sqlite` inside `/Apps/mozart-opendots/data` (internal SSD).

## Updating
1. Bump the pinned `ref:` in the workflow to the new upstream commit.
2. Bump the image tag in the workflow AND in `docker-compose.yml` (keep the `docker/` path trigger in mind).
3. Bump `version:` in `umbrel-app.yml` + add release notes.
4. Re-run all patch scripts (`node docker/apply-opencode-patch.mjs`, `apply-computers-patch.mjs`,
   `apply-crypto-patch.mjs`, `apply-delete-dot-patch.mjs`, `apply-reasoning-patch.mjs`,
   `apply-dot-model-patch.mjs`, `apply-computer-ui-patch.mjs`) against the new commit's checkout to
   confirm the anchors still match before pushing (each must patch exactly once; they are idempotent).
5. Push → wait for the Actions build (`gh run watch`) → verify the GHCR manifest is anonymously
   pullable → `update_app('mozart-opendots')` via the umbrel MCP, then poll `get_app_status` to `ready`.
