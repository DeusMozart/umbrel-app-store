# Kingshot Redeemer, packaged for umbrelOS.
#
# Mirrors the upstream Dockerfile (JareCoder/KingshotRedeemer, checked out to
# ./src and pinned by the build workflow) with two packaging additions, both
# under ./docker:
#   - entrypoint.sh: waits for DISCORD_TOKEN instead of letting the app
#     crash-loop before a token is configured.
#   - status.py: tiny read-only status page served behind the umbrel app proxy.
#
# Build context is the upstream repo root; see
# .github/workflows/build-kingshot-redeemer.yml.

FROM python:3.11-slim

WORKDIR /app

# System dependencies for Playwright (same as upstream)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY src/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

COPY src/ /app/

# Packaging additions
COPY .kingshot-docker/entrypoint.sh /entrypoint.sh
COPY .kingshot-docker/status.py /status.py
RUN chmod +x /entrypoint.sh

# Bot data (botData.json) lives at /data, mounted from the app's data folder.
RUN mkdir -p /data

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
