# OpenMuse app images — build notes

These files build the two container images used by the `mozart-openmuse` umbrelOS app:

| Image | Built from | Dockerfile |
| --- | --- | --- |
| `ghcr.io/deusmozart/openmuse:1.0.1` | CopilotKit/openmuse checkout | `Dockerfile.openmuse` |
| `ghcr.io/deusmozart/openmuse-browser-worker:1.0.0` | CopilotKit/openmuse `apps/worker` | upstream `apps/worker/Dockerfile` |

Both are built by `.github/workflows/build-openmuse.yml`, pinned to upstream commit
`ef8f608bb0305ff97114983de5c9db7ebcd816e2` (2026-09-22).

## How the combined image works

- The OpenMuse API binds `127.0.0.1:8787` (sample mode enforces a loopback bind).
- `proxy.mjs` listens on `0.0.0.0:8080`: it serves the static Expo web export and
  reverse-proxies `/api/*` to the loopback API, so the whole app is same-origin.
- The web bundle is built with `EXPO_PUBLIC_API_URL=/` so browser calls are relative
  and work on any hostname/port the app is exposed on.
- The entrypoint chowns the shared `browser-profiles` bind mount (from the app data
  dir) to uid 1000, because the browser worker runs as `pwuser` while umbrel creates
  bind-mounted app data as root.

## Updating to a newer upstream commit

1. Bump `ref:` in `.github/workflows/build-openmuse.yml` to the new commit.
2. Bump the image tags (`:1.0.0` → new version) in the workflow and in
   `../docker-compose.yml`, and the app `version:` in `../umbrel-app.yml`.
3. Push — the workflow rebuilds and publishes both images.
