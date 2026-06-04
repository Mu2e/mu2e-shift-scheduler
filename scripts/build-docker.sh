#!/usr/bin/env bash
# Build and optionally push the Mu2e Shift Scheduler Docker image.
#
# Usage:
#   ./scripts/build-docker.sh [OPTIONS]
#
# Options:
#   -r REPO       Image repository (default: docker.io/normanajn/mu2e-shift-scheduler-web)
#   -t TAG        Image tag (default: exact git tag on HEAD, or "latest")
#   --platforms   Comma-separated platforms (default: linux/amd64,linux/arm64)
#   --push        Push to the registry after building
#   --load        Load into local Docker daemon instead (single platform only)
#   --no-cache    Pass --no-cache to docker buildx build
#   -h, --help    Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REPO="docker.io/normanajn/mu2e-shift-scheduler-web"
PLATFORMS="linux/amd64,linux/arm64"
PUSH=false
LOAD=false
NO_CACHE=false

GIT_TAG="$(git -C "${REPO_ROOT}" describe --tags --exact-match HEAD 2>/dev/null || true)"
TAG="${GIT_TAG:-latest}"

usage() {
    awk '/^# Usage:/{show=1} show && /^#/{sub(/^# ?/, ""); print; next} show{exit}' "$0"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -r)          REPO="$2";       shift 2 ;;
        -t)          TAG="$2";        shift 2 ;;
        --platforms) PLATFORMS="$2";  shift 2 ;;
        --push)      PUSH=true;       shift ;;
        --load)      LOAD=true;       shift ;;
        --no-cache)  NO_CACHE=true;   shift ;;
        -h|--help)   usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

if [[ "${PUSH}" == true && "${LOAD}" == true ]]; then
    echo "ERROR: --push and --load are mutually exclusive." >&2
    exit 1
fi

if [[ "${LOAD}" == true && "${PLATFORMS}" == *","* ]]; then
    echo "ERROR: --load requires a single platform." >&2
    exit 1
fi

GIT_COMMIT="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
GIT_DATE="$(git -C "${REPO_ROOT}" log -1 --format=%cI 2>/dev/null || echo unknown)"
IMAGE="${REPO}:${TAG}"

echo "Repository : ${REPO}"
echo "Tag        : ${TAG}"
echo "Image      : ${IMAGE}"
echo "Platforms  : ${PLATFORMS}"
echo "Push       : ${PUSH}"
echo "Load       : ${LOAD}"
echo "Git commit : ${GIT_COMMIT}"
echo ""

BUILDER="$(docker buildx inspect --bootstrap 2>/dev/null | awk '/^Name:/ { print $2; exit }' || true)"
if [[ "${BUILDER}" == "default" || "${BUILDER}" == "desktop-linux" ]]; then
    echo "Creating multi-arch buildx builder..."
    docker buildx create --use --name multiarch --driver docker-container \
        --driver-opt network=host 2>/dev/null \
    || docker buildx use multiarch
    docker buildx inspect --bootstrap --builder multiarch
fi

BUILDX_ARGS=(
    --platform "${PLATFORMS}"
    --tag "${IMAGE}"
    --build-arg "GIT_COMMIT=${GIT_COMMIT}"
    --build-arg "GIT_DATE=${GIT_DATE}"
    --build-arg "GIT_TAG=${TAG}"
    --file "${REPO_ROOT}/docker/web/Dockerfile"
)

if [[ "${TAG}" != "latest" ]]; then
    BUILDX_ARGS+=(--tag "${REPO}:latest")
fi
if [[ "${PUSH}" == true ]]; then BUILDX_ARGS+=(--push); fi
if [[ "${LOAD}" == true ]]; then BUILDX_ARGS+=(--load); fi
if [[ "${NO_CACHE}" == true ]]; then BUILDX_ARGS+=(--no-cache); fi

if [[ "${PUSH}" != true && "${LOAD}" != true ]]; then
    echo "NOTE: Neither --push nor --load specified. Image stays in build cache only."
    echo ""
fi

docker buildx build "${BUILDX_ARGS[@]}" "${REPO_ROOT}"
echo ""
echo "Done: ${IMAGE}"
