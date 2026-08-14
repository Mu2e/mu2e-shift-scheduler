#!/usr/bin/env bash
# Build, push, and deploy the Mu2e Shift Scheduler Docker image to OKD.
#
# Usage:
#   ./scripts/deploy-okd.sh [OPTIONS]
#
# Options:
#   -t TAG          Image tag (default: exact git tag on HEAD, or "latest")
#   -r REPO         Image repository (default: docker.io/normanajn/mu2e-shift-scheduler-web)
#   -n NAMESPACE    OKD namespace/project (default: mu2e-shifts)
#   -f VALUES_FILE  Helm values file; repeat to layer files, later files win
#                   (default: my-values.yaml)
#   --release NAME  Helm release name (default: mu2e-shifts)
#   --tls-secret N  cert-manager TLS secret name (default: mu2e-shifts-tls).
#                   Overrides certManager.secretName so the wait loop and the
#                   Route externalCertificate always agree.
#   --timeout SEC   Rollout timeout in seconds (default: 180)
#   --no-build      Skip the Docker build and push step
#   --no-cert       Skip cert-manager entirely: request no Certificate and let
#                   the Route use the router's default wildcard certificate.
#                   Needed where the OPA admission webhook has not allowlisted
#                   the namespace for the route's DNS name.
#   -h, --help      Show this help message
#
# Deploy the test instance to the mu2e-test project (https://mu2e-okd-test.fnal.gov):
#   ./scripts/deploy-okd.sh -n mu2e-test --release mu2e-test \
#       -t test --tls-secret mu2e-test-tls \
#       -f helm/simple/values-test.yaml -f my-values-test.yaml

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
RESET='\033[0m'

info()    { printf "${CYAN}==> %s${RESET}\n" "$*"; }
success() { printf "${GREEN}[OK] %s${RESET}\n" "$*"; }
warn()    { printf "${YELLOW}[WARN] %s${RESET}\n" "$*"; }
error()   { printf "${RED}[ERROR] %s${RESET}\n" "$*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

REPOSITORY="docker.io/normanajn/mu2e-shift-scheduler-web"
NAMESPACE="mu2e-shifts"
RELEASE="mu2e-shifts"
VALUES_FILES=()
TIMEOUT=180
BUILD=true
CERT_MANAGER=true
TLS_SECRET="mu2e-shifts-tls"

GIT_TAG="$(git -C "${PROJECT_DIR}" describe --tags --exact-match HEAD 2>/dev/null || true)"
TAG="${GIT_TAG:-latest}"

usage() {
    awk '/^# Usage:/{show=1} show && /^#/{sub(/^# ?/, ""); print; next} show{exit}' "$0"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t)         TAG="$2";        shift 2 ;;
        -r)         REPOSITORY="$2"; shift 2 ;;
        -n)          NAMESPACE="$2";  shift 2 ;;
        -f)          VALUES_FILES+=("$2"); shift 2 ;;
        --release)   RELEASE="$2";    shift 2 ;;
        --tls-secret) TLS_SECRET="$2"; shift 2 ;;
        --timeout)   TIMEOUT="$2";    shift 2 ;;
        --no-build)  BUILD=false;     shift ;;
        --no-cert)   CERT_MANAGER=false; shift ;;
        -h|--help)   usage ;;
        *) error "Unknown option: $1"; usage ;;
    esac
done

if [[ ${#VALUES_FILES[@]} -eq 0 ]]; then
    VALUES_FILES=("my-values.yaml")
fi

# Resolve relative paths against the project directory and build the repeated
# --values arguments in order, so later -f files override earlier ones.
VALUES_ARGS=()
for i in "${!VALUES_FILES[@]}"; do
    if [[ "${VALUES_FILES[$i]}" != /* ]]; then
        VALUES_FILES[$i]="${PROJECT_DIR}/${VALUES_FILES[$i]}"
    fi
    VALUES_ARGS+=(--values "${VALUES_FILES[$i]}")
done

trap 'error "Deployment failed at line ${LINENO}."' ERR

cd "${PROJECT_DIR}"

info "[1/7] Checking prerequisites"
for command in docker helm oc; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        error "Required command is not available: ${command}"
        exit 1
    fi
done
for values_file in "${VALUES_FILES[@]}"; do
    if [[ ! -f "${values_file}" ]]; then
        error "Helm values file not found: ${values_file}"
        error "Create one from helm/simple/README.md with at least flask.secretKey set."
        exit 1
    fi
done
if ! oc whoami >/dev/null 2>&1; then
    warn "No active OKD login found. Starting web login..."
    oc login --web
    if ! oc whoami >/dev/null 2>&1; then
        error "OKD login did not produce an active session."
        exit 1
    fi
fi
success "Authenticated to OKD as $(oc whoami)"
oc get deployments -n "${NAMESPACE}" >/dev/null
success "Prerequisites available; OKD project is ${NAMESPACE}"

if [[ "${TAG}" == "latest" ]]; then
    warn "HEAD has no exact git tag; deploying mutable image tag 'latest'."
fi

IMAGE="${REPOSITORY}:${TAG}"
info "[2/7] Selected image: ${IMAGE}"

if [[ "${BUILD}" == true ]]; then
    info "[3/7] Building and pushing Docker image"
    "${SCRIPT_DIR}/build-docker.sh" -r "${REPOSITORY}" -t "${TAG}" --push
    success "Docker image pushed: ${IMAGE}"
else
    warn "[3/7] Skipping Docker build and push"
fi

info "[4/7] Applying Helm release ${RELEASE}"
if [[ "${CERT_MANAGER}" == false ]]; then
    # No Certificate is requested, so there is no TLS secret to wait for and no
    # externalCertificate to attach; the Route falls back to the router's own
    # wildcard certificate. Single pass is enough.
    warn "cert-manager disabled; Route will use the default router certificate"
    helm upgrade --install "${RELEASE}" ./helm/simple \
        --namespace "${NAMESPACE}" \
        "${VALUES_ARGS[@]}" \
        --set-string "image.repository=${REPOSITORY}" \
        --set-string "image.tag=${TAG}" \
        --set "certManager.enabled=false" \
        --set "certManager.externalCertificate=false"
    success "Helm release applied"
else
    info "Applying first pass without Route externalCertificate while cert-manager prepares ${TLS_SECRET}"
    helm upgrade --install "${RELEASE}" ./helm/simple \
        --namespace "${NAMESPACE}" \
        "${VALUES_ARGS[@]}" \
        --set-string "image.repository=${REPOSITORY}" \
        --set-string "image.tag=${TAG}" \
        --set-string "certManager.secretName=${TLS_SECRET}" \
        --set "certManager.externalCertificate=false"
    success "Helm first pass applied"

    info "Waiting for TLS secret ${TLS_SECRET} (${TIMEOUT}s timeout)"
    deadline=$((SECONDS + TIMEOUT))
    while ! oc get secret "${TLS_SECRET}" -n "${NAMESPACE}" >/dev/null 2>&1; do
        if (( SECONDS >= deadline )); then
            error "TLS secret ${TLS_SECRET} was not created before timeout."
            error "Check: oc describe certificate ${RELEASE} -n ${NAMESPACE}"
            error "If the Certificate was refused by the openpolicyagent admission"
            error "webhook, the namespace is not allowlisted for that DNS name."
            error "Ask the OKD admins to allowlist it, or redeploy with --no-cert."
            exit 1
        fi
        sleep 5
    done
    success "TLS secret is available: ${TLS_SECRET}"

    info "Applying final pass with Route externalCertificate enabled"
    helm upgrade --install "${RELEASE}" ./helm/simple \
        --namespace "${NAMESPACE}" \
        "${VALUES_ARGS[@]}" \
        --set-string "image.repository=${REPOSITORY}" \
        --set-string "image.tag=${TAG}" \
        --set-string "certManager.secretName=${TLS_SECRET}" \
        --set "certManager.externalCertificate=true"
    success "Helm release applied"
fi

info "[5/7] Restarting deployment/web to pull ${IMAGE}"
oc rollout restart deployment/web -n "${NAMESPACE}"
success "Restart requested"

info "[6/7] Waiting for deployment/web readiness (${TIMEOUT}s timeout)"
oc rollout status deployment/web -n "${NAMESPACE}" --timeout="${TIMEOUT}s"
success "Deployment is ready"

info "[7/7] Current OKD status"
oc get pods,pvc,svc,route -n "${NAMESPACE}"

ROUTE_HOST="$(oc get route web -n "${NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null || true)"
if [[ -n "${ROUTE_HOST}" ]]; then
    URL="https://${ROUTE_HOST}/"
    info "Checking ${URL}"
    if command -v curl >/dev/null 2>&1 && curl --fail --silent --show-error --head --max-time 20 "${URL}" >/dev/null; then
        success "HTTPS route is responding"
    else
        warn "Deployment is ready, but the HTTPS route check failed: ${URL}"
    fi
fi

success "Deployment complete: ${IMAGE}"
