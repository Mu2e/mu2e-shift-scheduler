#!/usr/bin/env bash
# bootstrap.sh — first-time setup for the Mu2e Shift Scheduler
#
# Usage:
#   ./bootstrap.sh [--admin-password PASS] [--no-server] [--no-tests]
#
# Options:
#   --admin-password <pass>   Seed the local admin account with this password
#                             (or set MU2E_INITIAL_ADMIN_PASSWORD)
#   --no-server               Set up the environment but do not start the server
#   --no-tests                Skip running the test suite after setup
#
# Creates the venv, installs/updates dependencies (including an editable
# install of the package), copies .env.example to .env if absent, runs the
# test suite, and starts the development server via
# scripts/start-mu2e-shift-scheduler. Safe to run repeatedly.

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { printf "${CYAN}==> %s${RESET}\n" "$*"; }
success() { printf "${GREEN}[OK] %s${RESET}\n" "$*"; }
warn()    { printf "${YELLOW}[WARN] %s${RESET}\n" "$*"; }
error()   { printf "${RED}[ERROR] %s${RESET}\n" "$*" >&2; }
header()  { printf "\n${BOLD}%s${RESET}\n%s\n" "$*" "$(printf '%.0s─' {1..60})"; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
ADMIN_PASSWORD="${MU2E_INITIAL_ADMIN_PASSWORD:-}"
START_SERVER=true
RUN_TESTS=true

while [[ $# -gt 0 ]]; do
  case $1 in
    --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
    --no-server)      START_SERVER=false; shift ;;
    --no-tests)       RUN_TESTS=false;    shift ;;
    -h|--help)
      awk '/^# Usage:/{show=1} show && /^#/{sub(/^# ?/, ""); print; next} show{exit}' "$0"
      exit 0 ;;
    *) error "Unknown option: $1"; exit 1 ;;
  esac
done

header "Mu2e Shift Scheduler Bootstrap"

info "Locating Python 3.10+..."
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cmd" >/dev/null 2>&1 \
     && "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PYTHON="$cmd"; break
  fi
done
if [[ -z "$PYTHON" ]]; then
  error "Python 3.10 or newer is required."
  exit 1
fi
success "Using $($PYTHON --version) ($(command -v $PYTHON))"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  info "Creating virtual environment in venv/..."
  "$PYTHON" -m venv "$VENV_DIR"
fi
PY="$VENV_DIR/bin/python"

info "Installing/updating dependencies..."
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$PROJECT_DIR/requirements-dev.txt"
"$PY" -m pip install --quiet -e "$PROJECT_DIR"
success "Dependencies installed"

if [[ ! -f "$PROJECT_DIR/.env" && -f "$PROJECT_DIR/.env.example" ]]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  success "Created .env from .env.example — edit it to configure the instance"
fi

if [[ "$RUN_TESTS" == true ]]; then
  info "Running test suite..."
  if "$PY" -m pytest "$PROJECT_DIR/tests" -q; then
    success "All tests passed"
  else
    error "Test suite failed — fix before starting the server, or re-run with --no-tests."
    exit 1
  fi
fi

if [[ "$START_SERVER" == true ]]; then
  ARGS=(--no-update)
  [[ -n "$ADMIN_PASSWORD" ]] && ARGS+=(--admin-password "$ADMIN_PASSWORD")
  exec "$PROJECT_DIR/scripts/start-mu2e-shift-scheduler" "${ARGS[@]}"
else
  success "Bootstrap complete. Start the server with ./scripts/start-mu2e-shift-scheduler"
fi
