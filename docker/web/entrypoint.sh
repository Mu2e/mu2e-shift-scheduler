#!/usr/bin/env sh
set -eu

mkdir -p /app/data /app/csv

exec gunicorn \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-1800}" \
  --log-level "${GUNICORN_LOG_LEVEL:-info}" \
  wsgi:app
