#!/usr/bin/env bash
# Rebuild and run the Mu2e Shift Scheduler container for local testing.
#
# Usage:
#   ./scripts/run-docker-local.sh [OPTIONS]
#
# Options:
#   -t TAG       Local image tag (default: local-test)
#   -p PORT      Host port to bind to container port 8000 (default: 8000)
#   --no-seed    Do not copy sample_data/small/shifts.csv to csv/shifts.csv
#   -h, --help   Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

REPOSITORY="docker.io/normanajn/mu2e-shift-scheduler-web"
TAG="local-test"
PORT="8000"
SEED=true

usage() {
    awk '/^# Usage:/{show=1} show && /^#/{sub(/^# ?/, ""); print; next} show{exit}' "$0"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t)        TAG="$2"; shift 2 ;;
        -p)        PORT="$2"; shift 2 ;;
        --no-seed) SEED=false; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

case "$(uname -m)" in
    arm64|aarch64) PLATFORM="linux/arm64" ;;
    x86_64|amd64)  PLATFORM="linux/amd64" ;;
    *)
        echo "ERROR: unsupported host architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

cd "${PROJECT_DIR}"

mkdir -p data csv
if [[ "${SEED}" == true && ! -f csv/shifts.csv ]]; then
    cp sample_data/small/shifts.csv csv/shifts.csv
fi

"${SCRIPT_DIR}/build-docker.sh" \
    -r "${REPOSITORY}" \
    -t "${TAG}" \
    --platforms "${PLATFORM}" \
    --load

echo ""
echo "Starting ${REPOSITORY}:${TAG} on http://127.0.0.1:${PORT}/"
echo "Persistent local mounts:"
echo "  ${PROJECT_DIR}/data -> /app/data"
echo "  ${PROJECT_DIR}/csv  -> /app/csv"
echo ""

exec docker run --rm \
    -p "${PORT}:8000" \
    -e FLASK_SECRET_KEY=local-test-secret \
    -e AUTH_DB_PATH=/app/data/users.sqlite \
    -e MU2E_INITIAL_ADMIN_USERNAME=mu2e-admin \
    -e MU2E_INITIAL_ADMIN_EMAIL=mu2e-admin@fnal.gov \
    -e MU2E_INITIAL_ADMIN_PASSWORD=local-admin-password \
    -v "${PROJECT_DIR}/data:/app/data" \
    -v "${PROJECT_DIR}/csv:/app/csv" \
    "${REPOSITORY}:${TAG}"
