#!/usr/bin/env bash
# Tear down must-gather on OpenShift (workloads, secrets, PVC, namespace).
#
# Usage:
#   ./k8s/delete-openshift.sh
#   DELETE_NAMESPACE=0 ./k8s/delete-openshift.sh   # keep namespace (faster redeploy)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-must-gather}"
DELETE_NAMESPACE="${DELETE_NAMESPACE:-1}"

cd "${ROOT_DIR}"

echo "==> Scale API to 0 (release RWO PVC before delete)"
if oc get deploy/must-gather-api -n "${NAMESPACE}" >/dev/null 2>&1; then
  oc scale deploy/must-gather-api -n "${NAMESPACE}" --replicas=0 || true
  sleep 3
fi

echo "==> Delete routes and workloads"
oc delete -f k8s/openshift-routes.yaml --ignore-not-found
oc delete -f k8s/frontend-deployment.yaml --ignore-not-found
oc delete -f k8s/api-deployment.yaml --ignore-not-found
oc delete -f k8s/configmap.yaml --ignore-not-found

echo "==> Delete secrets (created by sync-secrets-from-config.sh, not from YAML)"
oc delete secret gcloud-adc-secret must-gather-app-config -n "${NAMESPACE}" --ignore-not-found

echo "==> Delete RBAC and PVC"
oc delete -f k8s/rbac.yaml --ignore-not-found
oc delete -f k8s/shared-pvc.yaml --ignore-not-found

if [[ "${DELETE_NAMESPACE}" == "1" ]]; then
  echo "==> Delete namespace (wipes anything left)"
  oc delete -f k8s/namespace.yaml --ignore-not-found
  echo "Waiting for namespace ${NAMESPACE} to terminate..."
  while oc get namespace "${NAMESPACE}" >/dev/null 2>&1; do
    sleep 2
  done
  echo "Namespace ${NAMESPACE} deleted."
else
  echo "DELETE_NAMESPACE=0 — kept namespace ${NAMESPACE}"
fi

echo "Delete complete."
