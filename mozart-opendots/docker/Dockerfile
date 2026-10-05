# mozart-opendots build: upstream OpenDots + ownership-fixing entrypoint + OpenCode Go header patch + Dot-computers endpoint patch + chat UUID fallback patch + make-it-yours pack (delete Dot, reasoning view, per-Dot model, computer panel) + dark theme.
# Build context: a checkout of https://github.com/CopilotKit/OpenDots at the pinned
# commit, with this docker/ folder copied to .opendots-docker/ (see the workflow).
# The build stage mirrors upstream's Dockerfile with patch steps added before the build:
# docker/apply-opencode-patch.mjs injects the x-opencode-session routing header
# (OpenCode Go) into the model call sites, and docker/apply-computers-patch.mjs
# fixes the computer endpoint checks for split deployments (app and supervisor
# on different hosts: host acceptance + URL fallback). docker/apply-crypto-patch.mjs
# swaps a client-side crypto.randomUUID call for a fallback so chat sends work
# over plain HTTP (non-secure contexts). docker/apply-dark-mode-patch.mjs runs
# FIRST: it verifies the raw stylesheets by sha256 and generates the scoped
# dark theme layer (light mode untouched). The make-it-yours pack
# (apply-delete-dot, apply-reasoning, apply-dot-model, apply-computer-ui)
# adds owner conveniences upstream lacks — see docker/README.md. The runtime
# stage adds docker/entrypoint.sh
# and starts as root so it can normalize /data ownership.

FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN node .opendots-docker/apply-dark-mode-patch.mjs
RUN node .opendots-docker/apply-opencode-patch.mjs
RUN node .opendots-docker/apply-computers-patch.mjs
RUN node .opendots-docker/apply-crypto-patch.mjs
RUN node .opendots-docker/apply-delete-dot-patch.mjs
RUN node .opendots-docker/apply-reasoning-patch.mjs
RUN node .opendots-docker/apply-dot-model-patch.mjs
RUN node .opendots-docker/apply-computer-ui-patch.mjs
RUN npm run build

FROM node:24-bookworm-slim
ENV NODE_ENV=production HOST=0.0.0.0 PORT=4310 DATABASE_PATH=/data/opendots.sqlite
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev \
 && mkdir -p /data && chown node:node /data \
 && if ! command -v setpriv >/dev/null 2>&1; then \
      apt-get update && apt-get install -y --no-install-recommends util-linux \
      && rm -rf /var/lib/apt/lists/*; \
    fi
COPY --from=build /app/dist ./dist
COPY .opendots-docker/entrypoint.sh /usr/local/bin/opendots-entrypoint.sh
RUN chmod +x /usr/local/bin/opendots-entrypoint.sh
EXPOSE 4310
CMD ["/usr/local/bin/opendots-entrypoint.sh"]
