#!/usr/bin/env bash
# Create/update OpenShift secrets for Vertex LLM auth + app config.
#
# NEVER use oc apply -f k8s/secret.yaml — that creates empty credentials and
# leaves the API pod stuck in ContainerCreating (FailedMount).
#
# Second argument accepts either:
#   - Standard GCP service-account JSON (type=service_account at top level)
#   - Vertex wrapper JSON (api_key + service_account_key_data)
#
# Examples:
#   ./k8s/sync-secrets-from-config.sh Version_V1/Config/config.json
#   ./k8s/sync-secrets-from-config.sh Version_V1/Config/config.json /path/to/sa-key.json

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-${ROOT_DIR}/Version_V1/Config/config.json}"
SA_KEY_FILE="${2:-}"
NAMESPACE="${NAMESPACE:-must-gather}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config not found: ${CONFIG_PATH}" >&2
  echo "Create Version_V1/Config/config.json before deploying." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

ADC_JSON="${TMP_DIR}/application_default_credentials.json"
MERGED_CONFIG="${TMP_DIR}/config.json"

python3 - "${CONFIG_PATH}" "${SA_KEY_FILE}" "${ADC_JSON}" "${MERGED_CONFIG}" <<'PY'
import json
import sys

config_path, sa_key_file, adc_path, merged_config_path = sys.argv[1:5]

with open(config_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

claude = cfg.get("claude", {})
if not isinstance(claude, dict):
    claude = {}
    cfg["claude"] = claude

sa = None
api_key = None

if sa_key_file:
    with open(sa_key_file, "r", encoding="utf-8") as f:
        key_doc = json.load(f)
    if key_doc.get("type") == "service_account":
        sa = key_doc
    elif isinstance(key_doc.get("service_account_key_data"), dict):
        sa = key_doc["service_account_key_data"]
        api_key = key_doc.get("api_key")
    elif isinstance(key_doc.get("claude"), dict):
        nested = key_doc["claude"]
        if isinstance(nested.get("service_account_key_data"), dict):
            sa = nested["service_account_key_data"]
            api_key = nested.get("api_key")
    if api_key and not claude.get("api_key"):
        claude["api_key"] = api_key

if sa is None:
    sa = claude.get("service_account_key_data")
    if not isinstance(sa, dict):
        sa = None

if not isinstance(sa, dict) or sa.get("type") != "service_account":
    print(
        "ERROR: No service account found. Provide a GCP key JSON or Vertex wrapper "
        "(api_key + service_account_key_data) as the second argument, or set "
        "claude.service_account_key_data in config.json.",
        file=sys.stderr,
    )
    sys.exit(1)

private_key = sa.get("private_key", "")
if isinstance(private_key, str):
    sa = dict(sa)
    sa["private_key"] = private_key.replace("\\n", "\n").replace("\r\n", "\n").strip()

claude.setdefault("auth_type", "gcloud")
claude["service_account_key_file"] = "/secrets/gcloud/application_default_credentials.json"
claude["service_account_key_data"] = sa
claude["project_id"] = sa.get("project_id")
claude["api_url"] = "https://aiplatform.googleapis.com"
claude["endpoint_pattern"] = (
    "https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/global/"
    "publishers/anthropic/models/{model_id}:rawPredict"
)
if api_key:
    claude["api_key"] = api_key

for legacy_key in list(cfg.keys()):
    if legacy_key.startswith("_old_") or "DO_NOT_USE" in legacy_key:
        del cfg[legacy_key]

with open(adc_path, "w", encoding="utf-8") as f:
    json.dump(sa, f)

with open(merged_config_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PY

python3 - "${ADC_JSON}" <<'PY'
import json
import sys

adc_path = sys.argv[1]
with open(adc_path, "r", encoding="utf-8") as f:
    data = json.load(f)
if data.get("type") != "service_account":
    print(f"ERROR: expected type=service_account, got {data.get('type')}", file=sys.stderr)
    sys.exit(1)

validated = False
try:
    from google.oauth2 import service_account
    service_account.Credentials.from_service_account_file(
        adc_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    validated = True
except ImportError:
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        load_pem_private_key(data["private_key"].encode("utf-8"), password=None)
        validated = True
        print("WARNING: google-auth not installed; validated private key with cryptography only.")
    except ImportError:
        print("WARNING: skipping private key validation (install google-auth or cryptography).")
        validated = True
except Exception as exc:
    print(f"ERROR: service account private_key is invalid: {exc}", file=sys.stderr)
    sys.exit(1)

if validated and "google.oauth2" in sys.modules:
    print("Service account key validated.")
PY

echo "Syncing secrets in namespace ${NAMESPACE}"

oc delete secret gcloud-adc-secret -n "${NAMESPACE}" --ignore-not-found
oc create secret generic gcloud-adc-secret \
  --namespace="${NAMESPACE}" \
  --from-file=application_default_credentials.json="${ADC_JSON}"

oc delete secret must-gather-app-config -n "${NAMESPACE}" --ignore-not-found
oc create secret generic must-gather-app-config \
  --namespace="${NAMESPACE}" \
  --from-file=config.json="${MERGED_CONFIG}"

echo "Secrets updated:"
oc get secret gcloud-adc-secret must-gather-app-config -n "${NAMESPACE}"
