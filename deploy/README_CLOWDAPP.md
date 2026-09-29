# IntelliAide — Deploy to CRC Ephemeral (Firelink)

Deploy with **`oc process -f deploy/clowdapp.yaml`**. The template creates:

| Resource | Purpose |
|----------|---------|
| **ClowdApp** `intelliaide` | API (`intelliaide-api`) + Frontend (`intelliaide-frontend`) — both Clowder-managed |
| **Route** `intelliaide-frontend` | HTTPS UI URL (edge TLS) |
| **Service** `intelliaide-api-http` | Manual Service routing port 8000 → API container (bypasses Clowder's port 10000) |
| **PVC** `must-gather-shared` | Shared storage for API/workers (1Gi gp3) |
| **ConfigMap** `intelliaide-api-config` | Non-secret API config |
| **ConfigMap** `intelliaide-frontend-nginx` | Nginx proxy + SPA routing config |
| **ConfigMap** `intelliaide-jobs-kubeconfig` | In-cluster kubeconfig for worker Job dispatch |
| **ConfigMap** `intelliaide-namespace-pool` | JSON array of namespaces for worker scheduling |

Alternative without Clowder: [`k8s/deploy.sh`](../k8s/deploy.sh) with `OVERLAY=ephemeral`.

---

## Architecture

```
Browser
  └── Route intelliaide-frontend (edge TLS, targetPort 10000)
        └── Service intelliaide-frontend :10000
              └── ClowdApp deployment "frontend" (nginx :10000)
                    └── /api/* → intelliaide-api-http:8000 (manual Service)
                          └── ClowdApp deployment "api" (:8000)
                                ├── PVC must-gather-shared
                                ├── GCS bucket rca-inputs (upload storage)
                                └── gcloud-adc-secret, must-gather-app-config
```

Both deployments live under a **single ClowdApp** `intelliaide`. The frontend deployment has **no `webServices.public`** — this avoids the `crcauth` SSO sidecar that Clowder injects for public services (which crashes for apps not registered in BOP).

| Component | Image (default in template) |
|-----------|--------|
| API | `quay.io/redhat-services-prod/intelliaide-tenant/intelliaide/intelliaide-api` |
| Frontend | `quay.io/redhat-services-prod/intelliaide-tenant/intelliaide/intelliaide-frontend` |

### Clowder port convention

Clowder **always** exposes private services on port **10000** (and metrics on 9000), regardless of the `webServices.private.port` value in the ClowdApp spec. The service `targetPort` is also 10000. Containers must listen on 10000 to receive traffic through Clowder's auto-generated Service.

- **Frontend nginx** listens on port **10000** with `root /opt/intelliaide/www` (via `intelliaide-frontend-nginx` ConfigMap; the image alone listens on 8080 and is overridden at deploy time).
- **API** listens on port 8000 (hardcoded in the image). Because port 8000 ≠ Clowder's targetPort 10000, a **manual Service** `intelliaide-api-http` is created in the template to route port 8000 → container port 8000 directly.
- **Route** targets port 10000 on the `intelliaide-frontend` Service.

### GCS bucket

The API image uploads must-gather archives to GCS bucket **`rca-inputs`** before creating K8s analysis Jobs. This bucket must exist in the GCP project and the ADC credentials must have write access.

```bash
gcloud auth login
gcloud storage buckets create gs://rca-inputs \
  --project=itpc-gcp-hcm-pe-eng-claude \
  --location=us-central1 \
  --uniform-bucket-level-access
```

### Worker callback

Worker pods report progress back to the API via HTTP callbacks. The `API_CALLBACK_URL` env var on the API pod is passed to workers at Job creation time. It must point to the API's in-cluster Service (`http://intelliaide-api-http:8000`).

---

## Files

| File | Purpose |
|------|---------|
| [`clowdapp.yaml`](clowdapp.yaml) | OpenShift Template (`template.openshift.io/v1`) |
| [`rbac.yaml`](rbac.yaml) | ServiceAccount + Job-manager Role for API workers |
| [`nginx-clowdapp.conf`](nginx-clowdapp.conf) | Reference copy of ClowdApp nginx (embedded in `clowdapp.yaml` ConfigMap) |

---

## Prerequisites

1. **Reserve a Firelink namespace** — [Firelink](https://firelink.devshift.net/) or `bonfire namespace reserve --duration 8h`
2. **Log in** with the token from the reservation page
3. **Confirm ClowdEnvironment** exists (created automatically — do not create one yourself):

```bash
NS=ephemeral-xxxxx
oc login --token=<token> --server=https://api.crc-eph.r9lp.p1.openshiftapps.com:6443
oc project "${NS}"

oc api-resources | grep cloud.redhat.com   # clowdapps, clowdenvironments
oc get env | grep "${NS}"                  # env-${NS}  READY 10/10
```

4. **GCP credentials** for Vertex AI (ADC — `gcloud auth application-default login`)
5. **GCS bucket `rca-inputs`** exists in the GCP project (see below)
6. **Frontend image** built and pushed (step 1 below) if tag `eph-v1` is not already on Quay

**Clowder docs:** [Overview](https://redhatinsights.github.io/clowder/) · [API reference](https://redhatinsights.github.io/clowder/clowder_api/)

---

## Step 1 — Build frontend image (once per tag)

```bash
cd ~/Documents/ephemeral/intelliaide-intermediate-deliverables-final-seq-version

FRONTEND_IMAGE=quay.io/rh-ee-cdate/intelliaide-frontend
FRONTEND_TAG=eph-v1
EPHEMERAL_USER="${EPHEMERAL_USER:-$(oc whoami)}"

podman login quay.io

cp Frontend/nginx.conf Frontend/nginx.conf.bak
sed "s/__EPHEMERAL_USER__/${EPHEMERAL_USER}/g" deploy/nginx-clowdapp.conf > Frontend/nginx.conf

podman build -t "${FRONTEND_IMAGE}:${FRONTEND_TAG}" ./Frontend
podman push "${FRONTEND_IMAGE}:${FRONTEND_TAG}"

mv Frontend/nginx.conf.bak Frontend/nginx.conf
```

Backend uses the existing Quay image — no build required:

```bash
podman pull quay.io/swghosh/intelliaide:api-ephemeral-v0.15
```

---

## Step 2 — Secrets (once per namespace)

```bash
NS=ephemeral-xxxxx

gcloud auth application-default login

oc create secret generic gcloud-adc-secret \
  --namespace="${NS}" \
  --from-file=application_default_credentials.json=$HOME/.config/gcloud/application_default_credentials.json \
  --dry-run=client -o yaml | oc apply -f -

oc create secret generic must-gather-app-config \
  --namespace="${NS}" \
  --from-file=config.json=Version_V1/Config/config.json \
  --dry-run=client -o yaml | oc apply -f -
```

Templates (no credentials in git): [`k8s/secret.template.yaml`](../k8s/secret.template.yaml), [`k8s/app-secret.template.yaml`](../k8s/app-secret.template.yaml).

---

## Step 3 — RBAC

```bash
oc apply -f deploy/rbac.yaml -n "${NS}"
```

---

## Step 4 — Deploy (`oc process`)

Run from **repo root** (not from `deploy/`).

```bash
NS=ephemeral-xxxxx
ENV_NAME="env-${NS}"          # from: oc get env | grep ${NS}
EPHEMERAL_USER="$(oc whoami)"

oc project "${NS}"

oc process -f deploy/clowdapp.yaml \
  -p ENV_NAME="${ENV_NAME}" \
  -p NAMESPACE="${NS}" \
  -p API_TAG=api-ephemeral-v0.15 \
  -p FRONTEND_TAG=eph-v1 \
  -p EPHEMERAL_USER="${EPHEMERAL_USER}" \
  -p PVC_SIZE=1Gi \
  -p STORAGE_CLASS=gp3 \
  -p AUTH_BYPASS=true \
  | oc apply -f -
```

Wait for pods:

```bash
oc rollout status deploy/intelliaide-api -n "${NS}" --timeout=300s
oc rollout status deploy/intelliaide-frontend -n "${NS}" --timeout=120s

oc get clowdapp,deploy,pods,route -n "${NS}" | grep intelliaide
```

Expected:

```
intelliaide-api       1/1 Running
intelliaide-frontend  1/1 Running
```

Both pods should be **1/1** (single container each, no crcauth sidecar on frontend).

---

## Step 5 — Open the UI

```bash
FRONTEND_HOST="$(oc get route intelliaide-frontend -n "${NS}" -o jsonpath='{.spec.host}')"
echo "https://${FRONTEND_HOST}/"

curl -sk "https://${FRONTEND_HOST}/api/health"
curl -sk "https://${FRONTEND_HOST}/api/whoami"
```

---

## Template parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| `ENV_NAME` | *(required)* | `env-${NS}` from `oc get env`, e.g. `env-ephemeral-8fbkht` |
| `NAMESPACE` | *(required)* | Ephemeral namespace name (e.g. `ephemeral-ykrvuq`). Used for worker namespace pool. |
| `API_IMAGE` | `quay.io/redhat-services-prod/intelliaide-tenant/intelliaide/intelliaide-api` | Konflux-released backend repo |
| `API_TAG` | `latest` | Override with a Konflux git-short-SHA tag |
| `FRONTEND_IMAGE` | `quay.io/redhat-services-prod/intelliaide-tenant/intelliaide/intelliaide-frontend` | Konflux-released frontend repo |
| `FRONTEND_TAG` | `latest` | Override with a Konflux git-short-SHA tag |
| `PVC_SIZE` | `1Gi` | |
| `STORAGE_CLASS` | `gp3` | |
| `AUTH_BYPASS` | `true` | **Legacy / no-op** — not read by `src/api`; identity comes only from the `X-Forwarded-User` header (see Auth note below) |
| `EPHEMERAL_USER` | `dev-user` | **Legacy / no-op** — same as above; nginx no longer spoofs this header |

**Auth note:** Since nginx now passes through real `X-Forwarded-User` / `X-Forwarded-Email` headers instead of injecting `EPHEMERAL_USER`, `/whoami` and `/admin/*` will show as anonymous/403 in ephemeral unless you're testing behind something that sets those headers (e.g. `curl -H "X-Forwarded-User: myuser"`). This template now targets stage/prod behind crcauth as the primary identity path.

Preview rendered manifests:

```bash
oc process -f deploy/clowdapp.yaml -p ENV_NAME="env-${NS}" -p NAMESPACE="${NS}" -p FRONTEND_TAG=eph-v1
```

---

## Redeploy / upgrade

```bash
oc process -f deploy/clowdapp.yaml \
  -p ENV_NAME="env-${NS}" \
  -p NAMESPACE="${NS}" \
  -p API_TAG=api-ephemeral-v0.15 \
  -p FRONTEND_TAG=eph-v1 \
  -p EPHEMERAL_USER="$(oc whoami)" \
  -p PVC_SIZE=1Gi \
  -p STORAGE_CLASS=gp3 \
  -p AUTH_BYPASS=true \
  | oc apply -f -
```

After config secret change:

```bash
oc create secret generic must-gather-app-config \
  --namespace="${NS}" \
  --from-file=config.json=Version_V1/Config/config.json \
  --dry-run=client -o yaml | oc apply -f -

oc rollout restart deploy/intelliaide-api -n "${NS}"
```

---

## app-interface (stage / prod — later)

See [`README_APP_INTERFACE.md`](README_APP_INTERFACE.md) for full onboarding instructions covering Vault secrets, SaaS file promotion, BOP/crcauth registration, and per-environment GCS buckets.

---

## Troubleshooting

```bash
oc describe clowdapp intelliaide -n "${NS}" | tail -25
oc get events -n "${NS}" --sort-by='.lastTimestamp' | grep intelliaide | tail -15
oc logs deploy/intelliaide-api -n "${NS}" --tail=50
oc logs deploy/intelliaide-frontend -n "${NS}" --tail=50
```

| Symptom | Fix |
|---------|-----|
| Route returns **503** "Application is not available" | Route `targetPort` must be `10000` (Clowder's private port). Verify `intelliaide-frontend` Service exists. |
| `/api/health` **502 Bad Gateway** (from nginx) | Nginx proxy target must use `intelliaide-api-http:8000` (the manual Service). Clowder's auto-service uses port 10000 but API listens on 8000. |
| `Project was not passed and could not be determined` | Add `GOOGLE_CLOUD_PROJECT` env var to API deployment. |
| `The specified bucket does not exist` (rca-inputs) | Create GCS bucket: `gcloud storage buckets create gs://rca-inputs --project=itpc-gcp-hcm-pe-eng-claude` |
| `JOBS_CLUSTER_KUBECONFIG is not set` | Mount the `intelliaide-jobs-kubeconfig` ConfigMap (included in template). |
| `Namespace pool file not found` / `non-empty JSON array` | Mount `intelliaide-namespace-pool` ConfigMap with `["<namespace>"]` (included in template via `NAMESPACE` param). |
| Worker pod: `Invalid URL '/callback/...' No scheme supplied` | `API_CALLBACK_URL` env var is empty. Must be `http://intelliaide-api-http:8000` on the API pod. |
| `no kind "Template" is registered for version "v1"` | Template must use `apiVersion: template.openshift.io/v1` |
| `image: Required value` on API | Clowder needs `podSpec.image`, not `containers[]` |
| `ENV_NAME` mismatch | Must be `env-${NS}` from `oc get env`, not `ephemeral` |
| API `CreateContainerConfigError` | Create secrets (step 2) |
| Frontend `ImagePullBackOff` | Build/push `eph-v1` (step 1) |
| Worker jobs fail (RBAC) | Apply [`rbac.yaml`](rbac.yaml) |

---

## Upgrading from the old split-Deployment template

If you previously deployed the old template (frontend as standalone Deployment outside ClowdApp), clean up orphaned resources before applying the new template:

```bash
oc delete deploy intelliaide-frontend -n "${NS}" --ignore-not-found
oc delete svc intelliaide-frontend intelliaide-api-http -n "${NS}" --ignore-not-found
oc delete route intelliaide-frontend -n "${NS}" --ignore-not-found
# re-run oc process | oc apply
```

---

## Do not mix with `k8s/deploy.sh`

Both use PVC `must-gather-shared` and overlapping names. Use one method per namespace.
