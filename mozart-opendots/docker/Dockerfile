# mozart-opendots build: upstream OpenDots + ownership-fixing entrypoint.
# Build context: a checkout of https://github.com/CopilotKit/OpenDots at the pinned
# commit, with this docker/ folder copied to .opendots-docker/ (see the workflow).
# The build stage mirrors upstream's Dockerfile verbatim; the runtime stage adds
# docker/entrypoint.sh and starts as root so it can normalize /data ownership.

FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
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
