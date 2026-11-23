#!/bin/sh
# Container start: check the data folder is writable, then run the server (one worker: the job
# runner lives inside the server process). Models are fetched by the app itself on first start.
set -eu

if ! touch /data/.write-test 2>/dev/null; then
  echo "ERROR: /data is not writable by uid $(id -u). On the host run:" >&2
  echo "  sudo chown -R $(id -u):$(id -g) <your PL_DATA_PATH>" >&2
  exit 1
fi
rm -f /data/.write-test

# extra commands (e.g. "python -m app.auth set-password") run instead of the server
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec python -m uvicorn app.main:app \
  --host "${PL_HOST:-0.0.0.0}" --port "${PORT:-8420}" \
  --workers 1 --proxy-headers --forwarded-allow-ips "${PL_TRUSTED_PROXIES:-*}" \
  --no-server-header --log-level "${PL_LOG_LEVEL:-info}"
