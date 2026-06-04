FROM python:3.12-slim

ARG GIT_COMMIT=unknown
ARG GIT_DATE=unknown
ARG GIT_TAG=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    FLASK_ENV=production \
    PREFERRED_URL_SCHEME=https \
    SCHEDULER_CONFIG=/app/config.yaml \
    CSV_DIR=/app/csv \
    PREFERENCES_SHIFTS_CSV=/app/csv/shifts.csv \
    PREFERENCES_JSON=/app/data/preferences.json \
    GUNICORN_WORKERS=3 \
    GUNICORN_TIMEOUT=1800 \
    GUNICORN_LOG_LEVEL=info \
    GIT_COMMIT=${GIT_COMMIT} \
    GIT_DATE=${GIT_DATE} \
    GIT_TAG=${GIT_TAG}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --uid 1001 --gid 0 --no-create-home --shell /bin/false appuser \
    && mkdir -p /app/data /app/csv \
    && chgrp -R 0 /app \
    && chmod -R g=u /app \
    && chmod +x /app/docker/web/entrypoint.sh

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

ENTRYPOINT ["/app/docker/web/entrypoint.sh"]
