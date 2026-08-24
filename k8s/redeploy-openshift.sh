#!/usr/bin/env bash
# Full tear-down, rebuild, and redeploy for OpenShift.
#
# Replaces manual oc delete/apply sequences. Secrets come from config.json via
# sync-secrets-from-config.sh — never from empty k8s/secret.yaml placeholders.
#
# Usage:
#   ./k8s/redeploy-openshift.sh
#   DELETE_NAMESPACE=0 ./k8s/redeploy-openshift.sh   # keep namespace between cycles
#   LLM_KEY_FILE=/path/to/sa-key.json ./k8s/redeploy-openshift.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "========================================"
echo "  must-gather OpenShift full redeploy"
echo "========================================"

"${ROOT_DIR}/k8s/delete-openshift.sh"
"${ROOT_DIR}/k8s/deploy-openshift.sh"

echo "Redeploy complete."
