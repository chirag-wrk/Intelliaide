#!/usr/bin/env bash
# Build, push, and deploy must-gather to OpenShift.
#
# Prerequisites:
#   - podman, oc logged in
#   - Version_V1/Config/config.json with claude.service_account_key_data
#
# Usage:
#   ./k8s/deploy-openshift.sh
#   SKIP_BUILD=1 ./k8s/deploy-openshift.sh
#   LLM_KEY_FILE=/path/to/sa-key.json ./k8s/deploy-openshift.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-must-gather}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-quay.io/rh-ee-cdate/must-gather-frontend:latest}"
API_IMAGE="${API_IMAGE:-quay.io/rh-ee-cdate/must-gather-api:latest}"
CONFIG_PATH="${CONFIG_PATH:-${ROOT_DIR}/Version_V1/Config/config.json}"
LLM_KEY_FILE="${LLM_KEY_FILE:-}"
SKIP_BUILD="${SKIP_BUILD:-0}"

cd "${ROOT_DIR}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Missing ${CONFIG_PATH}. Create it before deploying." >&2
  exit 1
fi

if [[ "${SKIP_BUILD}" != "1" ]]; then
  echo "==> Building images"
  podman build -t "${FRONTEND_IMAGE}" ./Frontend
  podman build -t "${API_IMAGE}" ./Version_V1

  echo "==> Pushing images"
  podman push "${FRONTEND_IMAGE}"
  podman push "${API_IMAGE}"
else
  echo "==> SKIP_BUILD=1 — skipping podman build/push"
fi

echo "==> Apply base manifests (route before RBAC for OAuth client)"
oc apply -f k8s/namespace.yaml
oc apply -f k8s/openshift-routes.yaml
oc apply -f k8s/rbac.yaml
oc apply -f k8s/configmap.yaml
oc apply -f k8s/shared-pvc.yaml

echo "==> Sync secrets from config.json (required — do NOT oc apply secret.yaml)"
if [[ -n "${LLM_KEY_FILE}" && -f "${LLM_KEY_FILE}" ]]; then
  "${ROOT_DIR}/k8s/sync-secrets-from-config.sh" "${CONFIG_PATH}" "${LLM_KEY_FILE}"
else
  "${ROOT_DIR}/k8s/sync-secrets-from-config.sh" "${CONFIG_PATH}"
fi

echo "==> Sync log_config.json ConfigMap (runtime log ML settings — no image rebuild)"
"${ROOT_DIR}/k8s/sync-log-config-configmap.sh"

echo "==> Deploy workloads"
oc apply -f k8s/api-deployment.yaml
oc apply -f k8s/frontend-deployment.yaml
oc apply -f k8s/openshift-routes.yaml

echo "==> Fix OAuth client (safe if route/SA already aligned)"
"${ROOT_DIR}/k8s/fix-oauth-client.sh"

echo "==> Verify rollouts"
oc rollout status deploy/must-gather-api -n "${NAMESPACE}" --timeout=300s
oc rollout status deploy/must-gather-frontend -n "${NAMESPACE}" --timeout=300s

echo "==> Routes"
oc get routes -n "${NAMESPACE}"

FRONTEND_HOST="$(oc get route must-gather-frontend-route -n "${NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null || true)"
if [[ -n "${FRONTEND_HOST}" ]]; then
  echo ""
  echo "App URL: https://${FRONTEND_HOST}/"
fi

echo "Deploy complete."
