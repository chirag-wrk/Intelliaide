#!/usr/bin/env bash
# Fix OpenShift OAuth "server_error" for must-gather-frontend oauth-proxy.
#
# Cause: deleting/recreating the namespace (or applying rbac before the Route)
# leaves the service-account OAuth client out of sync with must-gather-frontend-route.
#
# Usage:
#   ./k8s/fix-oauth-client.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-must-gather}"
SA_NAME="must-gather-frontend"
ROUTE_NAME="must-gather-frontend-route"
OAUTH_CLIENT="system:serviceaccount:${NAMESPACE}:${SA_NAME}"
REDIRECT_ANNOTATION='{"kind":"OAuthRedirectReference","apiVersion":"v1","reference":{"kind":"Route","name":"must-gather-frontend-route"}}'

echo "==> Ensure namespace and frontend route exist"
oc apply -f "${ROOT_DIR}/k8s/namespace.yaml"
oc apply -f "${ROOT_DIR}/k8s/openshift-routes.yaml"

if ! oc get route "${ROUTE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
  echo "ERROR: Route ${ROUTE_NAME} not found in ${NAMESPACE}" >&2
  exit 1
fi

FRONTEND_HOST="$(oc get route "${ROUTE_NAME}" -n "${NAMESPACE}" -o jsonpath='{.spec.host}')"
echo "Frontend route host: ${FRONTEND_HOST}"

echo "==> Remove stale cluster OAuthClient (if present)"
if oc get oauthclient "${OAUTH_CLIENT}" >/dev/null 2>&1; then
  oc delete oauthclient "${OAUTH_CLIENT}" || true
fi

echo "==> Recreate frontend ServiceAccount OAuth redirect binding"
oc apply -f "${ROOT_DIR}/k8s/rbac.yaml"

if oc get serviceaccount "${SA_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
  oc delete serviceaccount "${SA_NAME}" -n "${NAMESPACE}" --wait=true
fi

oc create serviceaccount "${SA_NAME}" -n "${NAMESPACE}"
oc label serviceaccount "${SA_NAME}" -n "${NAMESPACE}" app=must-gather-frontend --overwrite
oc annotate serviceaccount "${SA_NAME}" -n "${NAMESPACE}" \
  "serviceaccounts.openshift.io/oauth-redirectreference.primary=${REDIRECT_ANNOTATION}" \
  --overwrite

echo "==> Re-bind RBAC roles (SA was recreated)"
oc apply -f "${ROOT_DIR}/k8s/rbac.yaml"

echo "==> Restart frontend so oauth-proxy picks up the new OAuth client"
if oc get deploy/must-gather-frontend -n "${NAMESPACE}" >/dev/null 2>&1; then
  oc rollout restart deploy/must-gather-frontend -n "${NAMESPACE}"
  oc rollout status deploy/must-gather-frontend -n "${NAMESPACE}"
fi

echo "==> OAuth client status"
if oc get oauthclient "${OAUTH_CLIENT}" >/dev/null 2>&1; then
  oc get oauthclient "${OAUTH_CLIENT}" -o yaml | sed -n '1,40p'
else
  echo "OAuthClient not listed yet — it is often created on first oauth-proxy login attempt."
fi

echo ""
echo "Open the app (use a fresh private window if cookies are stale):"
echo "  https://${FRONTEND_HOST}/"
