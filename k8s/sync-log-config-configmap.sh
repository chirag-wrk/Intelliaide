#!/usr/bin/env bash
# Push Version_V1/Config/log_config.json to the cluster ConfigMap (no image rebuild).
#
# Usage:
#   ./k8s/sync-log-config-configmap.sh
#   ./k8s/sync-log-config-configmap.sh /path/to/log_config.json

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_CONFIG_PATH="${1:-${ROOT_DIR}/Version_V1/Config/log_config.json}"
NAMESPACE="${NAMESPACE:-must-gather}"

if [[ ! -f "${LOG_CONFIG_PATH}" ]]; then
  echo "log_config.json not found: ${LOG_CONFIG_PATH}" >&2
  exit 1
fi

echo "Syncing must-gather-log-config from ${LOG_CONFIG_PATH}"
python3 - "${LOG_CONFIG_PATH}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    cfg = json.load(f)
flag = cfg.get("output_flags", {}).get("include_information")
print(f"  output_flags.include_information = {flag!r}")
PY

oc create configmap must-gather-log-config \
  --namespace="${NAMESPACE}" \
  --from-file=log_config.json="${LOG_CONFIG_PATH}" \
  --dry-run=client -o yaml | oc apply -f -

echo "ConfigMap updated. Restart API + workers pick up on next job:"
echo "  oc rollout restart deploy/must-gather-api -n ${NAMESPACE}"
