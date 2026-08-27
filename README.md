### Functional Documentation ###
https://gitlab.cee.redhat.com/intelliaide-debug/intelliaide-intermediate-deliverables/-/blob/main/src/api/AGENTIC_FRAMEWORK_DOCUMENTATION.md

### GCP_ChatUI — Must-Gather Analysis UI (Vertex AI Claude) + OpenShift Manifests

This repo contains:

- **API** (`src/api/`): a FastAPI service that runs the must-gather analysis / RCA workflow.
- **UI** (`frontend/`): a React (Vite) dashboard served by nginx, which **reverse-proxies** `/api/*` to the API Service.
- **Manifests** (`k8s/`): OpenShift-ready Kubernetes YAML (Namespace, ConfigMap, Deployments, Services, Routes).

The LLM calls have been updated to use **Claude via GCP Vertex AI** (token auth via Application Default Credentials).

---

### Architecture (production)

- **Browser → Frontend Route**: `https://<frontend-route-host>/`
- **Frontend nginx → API Service**: `/api/*` → `http://must-gather-api:8000/*` (in-cluster)
- **API reads must-gather data** from a path you pass in the request as `must_gather_root_folder`
  - In the provided Deployment, a writable directory is mounted at **`/data/must-gather`** for test data.

---

### Prerequisites

- **OpenShift** cluster (uses `Route`; for vanilla Kubernetes, you’ll need an Ingress or `oc/kubectl port-forward`)
- `oc` CLI (or `kubectl` for non-Route resources)
- A container registry accessible by your cluster (images in manifests currently reference `quay.io/...`)
- A GCP identity with Vertex AI permissions for the configured project/model

---

### Build & push images (if you are not using prebuilt images)

API image:

```bash
podman build -t quay.io/rh-ee-cdate/must-gather-api:latest src/api
podman push quay.io/rh-ee-cdate/must-gather-api:latest
```

Frontend image:

```bash
podman build -t quay.io/rh-ee-cdate/must-gather-frontend:latest frontend
podman push quay.io/rh-ee-cdate/must-gather-frontend:latest
```

If you use a different registry/image tag, update:
- `k8s/api-deployment.yaml`
- `k8s/frontend-deployment.yaml`

---

### Configure GCP credentials (ADC) for Vertex AI Claude

The API Deployment expects the credentials file at:

- `GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcloud/application_default_credentials.json`

Create the Kubernetes Secret from your local ADC file (recommended):

```bash
gcloud auth application-default login

oc create secret generic gcloud-adc-secret \
  --namespace=must-gather \
  --from-file=application_default_credentials.json=$HOME/.config/gcloud/application_default_credentials.json
```

If the Secret already exists and you want an idempotent “apply”:

```bash
gcloud auth application-default login

oc create secret generic gcloud-adc-secret \
  --namespace=must-gather \
  --from-file=application_default_credentials.json=$HOME/.config/gcloud/application_default_credentials.json \
  --dry-run=client -o yaml | oc apply -f -
```

Notes:
- Do **not** `oc apply -f k8s/secret.yaml` — empty placeholders leave the API pod stuck in `ContainerCreating`.
- Use `./k8s/sync-secrets-from-config.sh` (or the deploy scripts below) to create `gcloud-adc-secret` and `must-gather-app-config`.
- Templates: `k8s/secret.template.yaml`, `k8s/app-secret.template.yaml`.
- The API uses `google-auth` (preferred) or `gcloud auth print-access-token` (fallback) for Vertex.

---

### Deploy to OpenShift

**Full redeploy** (delete namespace, rebuild images, deploy):

```bash
chmod +x k8s/*.sh
./k8s/redeploy-openshift.sh
```

**Deploy only** (namespace already exists, or after `./k8s/delete-openshift.sh`):

```bash
./k8s/deploy-openshift.sh
```

**Delete only**:

```bash
./k8s/delete-openshift.sh
```

Secrets are synced from `Version_V1/Config/config.json` automatically — never from YAML placeholders.

---

### Get the Route URL and open the UI

```bash
FRONTEND_HOST="$(oc get route must-gather-frontend-route -n must-gather -o jsonpath='{.spec.host}')"
echo "https://${FRONTEND_HOST}/"
```

In production, the UI will call the API via the same host at:
- `https://<frontend-route-host>/api/health`
- `https://<frontend-route-host>/api/analyze`

That works because nginx in the frontend image proxies `/api/` to the in-cluster Service `must-gather-api`.

---

### Load must-gather logs into the API pod (for testing)

The API Deployment includes an `emptyDir` mounted at:
- `/data/must-gather`

Copy a must-gather folder from your workstation into the running API pod:

```bash
API_POD="$(oc get pod -n must-gather -l app=must-gather-api -o jsonpath='{.items[0].metadata.name}')"

# Example: copy a local directory named "must-gather.local.12345"
oc cp ./must-gather.local.12345 "${API_POD}:/data/must-gather/must-gather.local.12345" -n must-gather
```

Important:
- `emptyDir` is **ephemeral** (pod restart = data gone). For persistent test data, change the volume to a PVC.

---

### Run an analysis (API call)

The API requires `must_gather_root_folder` unless you bake a default into `Version_V1/Config/config.json`.

Using the UI route (recommended, since it already proxies `/api`):

```bash
FRONTEND_HOST="$(oc get route must-gather-frontend-route -n must-gather -o jsonpath='{.spec.host}')"

curl -sk "https://${FRONTEND_HOST}/api/health"

curl -sk "https://${FRONTEND_HOST}/api/analyze" \
  -H 'Content-Type: application/json' \
  -d '{
    "user_query": "Why are nodes reporting NotReady after upgrade?",
    "must_gather_root_folder": "/data/must-gather/must-gather.local.12345"
  }'
```

Poll status (replace `<session_id>` with the returned id):

```bash
curl -sk "https://${FRONTEND_HOST}/api/status/<session_id>"
```

---

### Vertex AI / Claude settings

Cluster-side settings live in `k8s/configmap.yaml` (example values):

- `ANTHROPIC_VERTEX_PROJECT_ID`
- `CLOUD_ML_REGION`
- `ANTHROPIC_MODEL`
- `VERIFY_SSL`

The API code also ships with defaults in `Version_V1/Config/config.json`:
- `claude.auth_type`: `gcloud`
- `claude.endpoint_pattern`: Vertex AI `rawPredict` endpoint
- `claude.model_id`: `claude-sonnet-4-6`

If you change project/region/model, update `k8s/configmap.yaml` and/or `Version_V1/Config/config.json` and rebuild the API image.

---

### Troubleshooting

- Check pods/services/routes:

```bash
oc get all -n must-gather
oc get route -n must-gather
```

- API logs:

```bash
oc logs -n must-gather deploy/must-gather-api
```

- Frontend logs (nginx):

```bash
oc logs -n must-gather deploy/must-gather-frontend
```

- Validate the in-cluster reverse proxy from the frontend pod:

```bash
FRONTEND_POD="$(oc get pod -n must-gather -l app=must-gather-frontend -o jsonpath='{.items[0].metadata.name}')"
oc rsh -n must-gather "${FRONTEND_POD}" sh -lc 'wget -qO- http://must-gather-api:8000/health && echo'
```

