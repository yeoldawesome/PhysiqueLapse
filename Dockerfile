# PhysiqueLapse in server mode (PL_MODE=server). See docs/server-setup.md.
#
#   docker compose up -d --build                      # CPU image (default)
#
# Models are NOT in the image: the app downloads them into the data volume on first start.

# ---------------------------------------------------------------- 1. frontend
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- 2. app (CPU)
FROM python:3.12-slim-bookworm AS cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PL_MODE=server \
    DATA_DIR=/data \
    PORT=8420 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

# OpenCV / MediaPipe runtime libraries
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies first (cached layer): read straight from pyproject.toml so there is one list.
COPY backend/pyproject.toml backend/pyproject.toml
RUN python -c "import tomllib; p = tomllib.load(open('backend/pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['server']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu \
 && rm /tmp/requirements.txt

# Settings added later go here, after the slow dependency layer, so changing them never
# re-downloads the packages.
ENV MPLCONFIGDIR=/tmp/matplotlib

# The app runs from its source folder, like a checkout (config.PROJECT_ROOT = /app).
COPY backend/ backend/
COPY --from=frontend /src/frontend/dist frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/pl-entrypoint

# Runs as an ordinary user. PUID/PGID should own the data folder on the host.
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} pl && useradd -u ${PUID} -g ${PGID} -M -d /app -s /usr/sbin/nologin pl \
 && mkdir -p /data && chown pl:pl /data \
 && chmod 0755 /usr/local/bin/pl-entrypoint
USER pl
WORKDIR /app/backend
VOLUME ["/data"]
EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8420/api/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "pl-entrypoint"]
