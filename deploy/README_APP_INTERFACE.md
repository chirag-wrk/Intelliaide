# IntelliAide — app-interface Onboarding (Stage / Prod)

This guide covers wiring IntelliAide into [app-interface](https://gitlab.cee.redhat.com/service/app-interface) for permanent stage/prod deployments with fixed hostnames, managed secrets, and CI-driven promotions.

> **Prerequisite:** The ClowdApp template ([`clowdapp.yaml`](clowdapp.yaml)) is already validated in ephemeral — see [`README_CLOWDAPP.md`](README_CLOWDAPP.md).

---

## Overview

| Concern | Ephemeral (current) | app-interface (target) |
|---------|---------------------|----------------------|
| Namespace lifecycle | Temporary (8h Firelink reservation) | Permanent, managed by app-interface |
| Secrets | Manual `oc create secret` | Vault-backed, synced by app-interface |
| Image promotion | Manual `podman push` + tag override | Quay tag promotion via `saas-file` |
| Hostname | Dynamic (`*-ephemeral-xxxxx.apps.crc-eph...`) | Fixed (e.g. `intelliaide.stage.devshift.net`) |
| Auth | No identity (nginx passes through `X-Forwarded-User`; nothing sets it locally) | `crcauth` sidecar or 3scale (BOP-registered) sets the header |
| GCS bucket | Shared `rca-inputs` | Dedicated per-env bucket or prefix |

---

## Step 1 — Register the app in app-interface

Create the app definition under `data/services/insights/intelliaide/`:

```
data/services/insights/intelliaide/
├── app.yml                  # App metadata
├── namespaces/
│   ├── stage.yml            # Namespace for stage
│   └── prod.yml             # Namespace for prod
├── deploy/
│   └── saas-intelliaide.yml # SaaS file (image promotion)
└── resources/
    ├── clowdapp.yml         # Template reference
    └── rbac.yml             # RBAC resources
```

### `app.yml`

```yaml
---
$schema: /app-sre/app-1.yml
labels:
  service: intelliaide
name: intelliaide
description: AI-powered must-gather Root Cause Analysis
serviceOwners:
  - name: IntelliAide Team
    email: intelliaide-team@redhat.com
dependencies: []
```

---

## Step 2 — Namespace definitions

### `namespaces/stage.yml`

```yaml
---
$schema: /openshift/namespace-1.yml
labels: {}
name: intelliaide-stage
cluster:
  $ref: /openshift/cluster/crc-stage.yml
managedResourceTypes:
  - Deployment
  - Service
  - ConfigMap
  - Route
  - PersistentVolumeClaim
  - ServiceAccount
  - Role
  - RoleBinding
app:
  $ref: /services/insights/intelliaide/app.yml
environment:
  $ref: /openshift/environment/stage.yml
```

---

## Step 3 — SaaS file (image promotion)

### `deploy/saas-intelliaide.yml`

```yaml
---
$schema: /app-sre/saas-file-2.yml
labels:
  service: intelliaide
name: saas-intelliaide
app:
  $ref: /services/insights/intelliaide/app.yml
pipelinesProvider:
  $ref: /pipeline-providers/tekton/ci-int.yml
managedResourceTypes:
  - ClowdApp
  - Route
  - ConfigMap
  - Service
  - PersistentVolumeClaim
resourceTemplates:
  - name: intelliaide
    url: https://github.com/chirag-wrk/Intelliaide
    path: /deploy/clowdapp.yaml
    targets:
      - namespace:
          $ref: /services/insights/intelliaide/namespaces/stage.yml
        ref: main
        parameters:
          ENV_NAME: env-intelliaide-stage
          NAMESPACE: intelliaide-stage
          API_IMAGE: quay.io/redhat-services-prod/intelliaide-tenant/intelliaide/intelliaide-api
          FRONTEND_IMAGE: quay.io/redhat-services-prod/intelliaide-tenant/intelliaide/intelliaide-frontend
          # Tags: Konflux git short SHA via saas imagePatterns + hash_length (not pinned here)
          AUTH_BYPASS: "false"
          EPHEMERAL_USER: ""
          PVC_SIZE: 10Gi
          STORAGE_CLASS: gp3
```

Image promotions happen by updating `API_TAG` / `FRONTEND_TAG` in the saas-file via merge request.

---

## Step 4 — Secrets (Vault-backed)

Instead of manual `oc create secret`, app-interface syncs secrets from Vault.

### Vault paths

| Secret | Vault path | Key |
|--------|-----------|-----|
| `gcloud-adc-secret` | `app-interface/intelliaide/stage/gcloud-adc` | `application_default_credentials.json` |
| `must-gather-app-config` | `app-interface/intelliaide/stage/app-config` | `config.json` |

### Secret definition in app-interface

```yaml
---
$schema: /openshift/secret-1.yml
labels: {}
name: gcloud-adc-secret
namespace:
  $ref: /services/insights/intelliaide/namespaces/stage.yml
type: Opaque
field: null
data:
  application_default_credentials.json:
    path: insights/intelliaide/stage/gcloud-adc
    field: credentials
    version: 1
```

Store credentials in Vault:

```bash
export VAULT_ADDR=https://vault.ci.ext.devshift.net
vault login -method=oidc

vault kv put app-interface/intelliaide/stage/gcloud-adc \
  application_default_credentials.json=@"$HOME/.config/gcloud/application_default_credentials.json"

vault kv put app-interface/intelliaide/stage/app-config \
  config.json=@Version_V1/Config/config.json
```

---

## Step 5 — Template changes for prod

The current [`clowdapp.yaml`](clowdapp.yaml) works as-is with parameter overrides. Key differences for stage/prod:

| Parameter | Ephemeral | Stage/Prod |
|-----------|-----------|-----------|
| `AUTH_BYPASS` | `true` | `false` |
| `EPHEMERAL_USER` | `$(oc whoami)` | `""` (unused — real auth) |
| `NAMESPACE` | `ephemeral-xxxxx` | `intelliaide-stage` |
| `PVC_SIZE` | `1Gi` | `10Gi+` |
| `API_CALLBACK_URL` | `http://intelliaide-api-http:8000` | Same (in-cluster) |

### Auth in prod

With `AUTH_BYPASS=false`, the API reads `X-Forwarded-User` from the OAuth proxy (crcauth sidecar). To enable crcauth:

1. Register the app in BOP (Business Object Proxy)
2. Add `webServices.public.enabled: true` to the frontend deployment (Clowder injects crcauth)
3. Remove the manual Route (Clowder creates one automatically for public services)

The template's nginx already passes through real `X-Forwarded-User` / `X-Forwarded-Email` headers (no ephemeral stub to remove) — the API reads identity from those headers regardless of environment, so no template change is needed here once crcauth is in front.

Contact **#crc-devprod-team** on Slack for BOP registration.

---

## Step 6 — GCS bucket per environment

Create dedicated buckets per environment to isolate data:

| Environment | Bucket |
|-------------|--------|
| Ephemeral | `rca-inputs` (shared) |
| Stage | `rca-inputs-stage` |
| Prod | `rca-inputs-prod` |

```bash
gcloud storage buckets create gs://rca-inputs-stage \
  --project=itpc-gcp-hcm-pe-eng-claude \
  --location=us-central1 \
  --uniform-bucket-level-access
```

If the bucket name is configurable via env var (check with `oc exec deploy/intelliaide-api -- env | grep -i bucket`), add it to the ClowdApp template parameters. Otherwise, coordinate with the image owner.

---

## Step 7 — Submit the MR

1. Fork [app-interface](https://gitlab.cee.redhat.com/service/app-interface)
2. Create the directory structure above
3. Open an MR targeting `main`
4. Tag `@crc-devprod-team` for review
5. Once merged, app-interface's reconciler deploys automatically

---

## RBAC note

The [`rbac.yaml`](rbac.yaml) resources (ServiceAccount, Role, RoleBinding) should be included in the `managedResourceTypes` of the namespace definition, or applied via a separate resource template in the saas-file. Clowder's `k8sAccessLevel: edit` provides the pod with a service account, but the Job creation permissions still require the explicit Role.

---

## Checklist

- [ ] App registered in app-interface (`app.yml`)
- [ ] Namespace definitions created (stage, prod)
- [ ] SaaS file with correct template path and parameters
- [ ] Secrets stored in Vault and secret definitions created
- [ ] GCS buckets created per environment
- [ ] BOP registration requested (for crcauth in prod)
- [ ] MR opened and reviewed by `#crc-devprod-team`
- [ ] Verify deployment after merge: `oc get clowdapp -n intelliaide-stage`

---

## References

- [app-interface docs](https://docs.engineering.redhat.com/display/APPSRE/App-Interface)
- [Clowder onboarding](https://redhatinsights.github.io/clowder/onboarding/)
- [SaaS file schema](https://github.com/app-sre/qontract-schemas/blob/main/schemas/app-sre/saas-file-2.yml)
- [Vault secrets in app-interface](https://docs.engineering.redhat.com/display/APPSRE/Vault+Usage+in+App-Interface)
- Contact: **#crc-devprod-team** on Slack
