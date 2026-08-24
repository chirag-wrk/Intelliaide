// Auto-generated from Version_V1/Results - mock RCA data for all 3 passes

export const mockRCATier1 = `The RCA is complete and highly detailed. Let me now compile the full final report:

---

## User Reported Issue

Test cases are failing during the OpenShift cluster update process. The cluster is attempting to upgrade from OKD/SCOS version \`4.21.0-okd-scos.ec.13\` to \`4.21.0-okd-scos.ec.18\` and is encountering failures that block successful completion of the update.

---

## Executive Summary

The cluster update from OKD/SCOS \`4.21.0-okd-scos.ec.13\` → \`4.21.0-okd-scos.ec.18\` is blocked by multiple compounding failures. **The key cause for the user's problem is:** the Cluster Version Operator (CVO) cannot apply an **invalid CRD payload resource** (\`ipaddressclaims.ipam.cluster.x-k8s.io\`), triggering a \`Failing\` condition with \`UpdatePayloadResourceInvalid\`. This is compounded by a **degraded MachineConfigPool** (master pool) where one node (\`ip-10-0-49-48\`) retained a bootstrap-generated MachineConfig that mismatches the in-cluster controller-generated config, and a **missing kubelet server certificate** that delayed the previous update phase. Together, these three issues caused the update to stall in a \`Partial\` state.

---

## Chronology of Events

- **2026-01-12T13:41:16Z**: Cluster update from \`4.21.0-okd-scos.ec.13\` begins (\`history[1].startedTime\`). Update channel is unconfigured (\`NoChannel\`).
- **2026-01-12T13:42:15Z**: Kubelet on a master node fails to initialize its certificate reloader — \`kubelet-server-current.pem\` is missing from \`/var/lib/kubelet/pki/\`. Early sign of node PKI instability.
- **2026-01-12T13:45:07Z**: \`kube-storage-version-migrator\` begins failing to list \`flowcontrol\` resources (FlowSchema, PriorityLevelConfiguration) — API server does not yet expose these resources at the expected version during the upgrade transition.
- **2026-01-12T13:45:48Z**: \`insights\` cluster operator transitions to \`Available=True\` — operator is healthy.
- **2026-01-12T13:46:02Z**: MachineConfig Operator (MCO) cannot find the \`kube-apiserver-server-ca\` ConfigMap — API server CA not yet propagated, indicating kube-apiserver is still rolling.
- **2026-01-12T13:46:15Z**: MachineConfigPool \`master\` is fully degraded (0/3 nodes ready, 0 updated). Node \`ip-10-0-49-48\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that conflicts with the in-cluster controller-generated config.
- **2026-01-12T13:47:30Z**: CVO attempts to apply the new release payload and encounters \`UpdatePayloadResourceInvalid\` on \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD — the CRD spec in the payload is rejected by the API server.
- **2026-01-12T13:48:00Z**: CVO sets \`Failing=True\` condition. ClusterVersion enters \`Partial\` state. Update is effectively blocked.
- **2026-01-12T13:50+Z**: Repeated CVO reconciliation loops continue to fail on the same CRD resource. etcd and kube-apiserver remain healthy throughout.

---

## Primary Root Cause(s)

1. **Invalid CRD Payload Resource — \`ipaddressclaims.ipam.cluster.x-k8s.io\`**
   - The CVO reports \`UpdatePayloadResourceInvalid\` when attempting to apply the CRD \`ipaddressclaims.ipam.cluster.x-k8s.io\` from the \`4.21.0-okd-scos.ec.18\` release payload.
   - The API server rejects the CRD spec — likely due to a structural schema validation failure, a conversion webhook conflict, or an incompatible field change between the ec.13 and ec.18 CRD versions.
   - This is the **primary blocker** preventing the upgrade from completing. The CVO cannot proceed past this resource application step.
   - Evidence: \`clusterversions.yaml\` shows \`Failing=True\` with reason \`UpdatePayloadResourceInvalid\`; CVO \`current.log\` shows repeated failed attempts to apply this specific CRD.

2. **Degraded MachineConfigPool \`master\` — Bootstrap MachineConfig Mismatch on \`ip-10-0-49-48\`**
   - Node \`ip-10-0-49-48.us-east-2.compute.internal\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that does not match the in-cluster controller-generated equivalent.
   - The MachineConfigPool \`master\` shows 0/3 nodes updated and 0/3 nodes ready, blocking the MCO from completing its node configuration rollout.
   - Evidence: \`openshift-machine-config-operator/core/events.yaml\` shows \`MachineConfigPool master is degraded\`; machine-config-controller logs show the config mismatch on \`ip-10-0-49-48\`.

3. **Missing Kubelet Server Certificate (\`kubelet-server-current.pem\`)**
   - During the early phase of the upgrade, the kubelet on at least one master node could not initialize its certificate reloader because \`/var/lib/kubelet/pki/kubelet-server-current.pem\` was absent.
   - This caused transient API server connectivity issues and delayed the kube-apiserver static pod rollout.
   - Evidence: kube-apiserver \`current.log\` on \`ip-10-0-122-129\` shows \`failed to initialize certificate reloader\` with the missing PEM path.

---

## Secondary Causes / Contributing Factors

- **Storage Version Migrator Failures**: The \`kube-storage-version-migrator\` repeatedly failed to list \`flowcontrol.apiserver.k8s.io\` resources (FlowSchema, PriorityLevelConfiguration) during the upgrade window. This is expected during API server rollout but can delay upgrade completion if the migrator is blocked for extended periods.
- **Missing \`kube-apiserver-server-ca\` ConfigMap**: The MCO could not find this ConfigMap during the upgrade, indicating the kube-apiserver CA propagation was delayed. This is a transient condition but contributed to the MCO degradation window.
- **No Update Channel Configured**: The ClusterVersion shows \`NoChannel\` for the update channel. While not a direct blocker, this means the cluster cannot receive automatic update recommendations or rollback guidance from the Cincinnati update service.
- **Multiple kube-controller-manager installer retries**: Installer pods for the kube-controller-manager (revisions 4, 4-retry-1, 4-retry-2) show repeated retry attempts, indicating the controller manager static pod rollout was unstable during the upgrade.

---

## Aggregated Error Patterns

| Pattern | Source | Classification | Significance |
|---------|--------|----------------|--------------|
| \`UpdatePayloadResourceInvalid: unable to apply CRD ipaddressclaims.ipam.cluster.x-k8s.io\` | clusterversions.yaml | Error | Primary upgrade blocker — CVO cannot apply CRD from new payload |
| \`Failing=True, Progressing=True (Partial)\` on ClusterVersion | clusterversions.yaml | Error | Upgrade is actively failing and stalled in partial state |
| \`MachineConfigPool master is degraded: 0/3 nodes updated\` | openshift-machine-config-operator/core/events.yaml | Error | All master nodes blocked from MCO config rollout |
| \`Node ip-10-0-49-48 has bootstrap-generated MachineConfig mismatch (99-master-generated-registries)\` | machine-config-controller/logs/current.log | Error | Specific node stuck with conflicting bootstrap config |
| \`failed to initialize certificate reloader: kubelet-server-current.pem: no such file or directory\` | kube-apiserver/logs/current.log (ip-10-0-122-129) | Error | Missing kubelet server cert caused API server startup delay |
| \`failed to list flowcontrol.apiserver.k8s.io/v1 FlowSchema: the server could not find the requested resource\` | openshift-kube-storage-version-migrator/logs/current.log | Majority Error | Storage migrator blocked on missing API resource during rollout |
| \`configmaps "kube-apiserver-server-ca" not found\` | machine-config-operator/logs/current.log | Majority Error | MCO cannot read CA configmap — API server CA propagation delayed |
| \`kube-controller-manager installer-4 retry-1, retry-2 failures\` | openshift-kube-controller-manager/pods/installer-*/logs | Majority Error | Controller manager static pod rollout required multiple retries |
| \`ClusterOperator machine-config-operator: Degraded=True\` | clusteroperators.yaml | Error | MCO operator itself reporting degraded state during upgrade |

---

## Remediation

### 1. [IMMEDIATE — CRITICAL] Resolve the Invalid CRD Payload Resource

The \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD in the \`4.21.0-okd-scos.ec.18\` payload is being rejected by the API server.

**Steps:**
\`\`\`bash
# Check the current state of the CRD
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o yaml

# Check for any existing conversion webhooks that may conflict
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o jsonpath='{.spec.conversion}'

# Check CVO error details
oc get clusterversion version -o jsonpath='{.status.conditions[?(@.type=="Failing")].message}'

# If the CRD exists with an incompatible schema, manually delete it to allow CVO to re-apply
# WARNING: Only do this if the CRD has no active custom resources
oc get ipaddressclaims --all-namespaces
# If empty/safe to delete:
oc delete crd ipaddressclaims.ipam.cluster.x-k8s.io
\`\`\`

**Validation:** After deletion, the CVO should re-apply the CRD from the payload within 1-2 reconciliation cycles:
\`\`\`bash
oc get clusterversion version -w
# Watch for Failing=False and Progressing=True (healthy progress)
\`\`\`

**Note:** If the CRD cannot be safely deleted (active resources exist), escalate to Red Hat/OKD support with the specific validation error from the CVO logs.

---

### 2. [HIGH] Fix the MachineConfigPool Master Degradation

The bootstrap-generated MachineConfig on \`ip-10-0-49-48\` must be reconciled.

**Steps:**
\`\`\`bash
# Check the current MachineConfigPool status
oc get mcp master -o yaml

# Identify the degraded node
oc get nodes -l node-role.kubernetes.io/master

# Check the node's current MachineConfig
oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/currentConfig}'

# Check the desired MachineConfig
oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/desiredConfig}'

# Force the MachineConfigDaemon to re-apply the correct config
# SSH to the node (via bastion or debug pod)
oc debug node/ip-10-0-49-48.us-east-2.compute.internal

# Inside the debug pod:
chroot /host
# Check the bootstrap-generated registries config
cat /etc/containers/registries.conf

# If the bootstrap config is stale, force MCD to re-apply:
systemctl restart machine-config-daemon
\`\`\`

**Validation:**
\`\`\`bash
oc get mcp master -w
# Wait for: READYMACHINECOUNT=3, UPDATEDMACHINECOUNT=3, DEGRADEDMACHINECOUNT=0
\`\`\`

**Dependency:** Complete Step 1 first, as the CVO failure may be preventing MCO from receiving the correct target config.

---

### 3. [HIGH] Restore Missing Kubelet Server Certificate

**Steps:**
\`\`\`bash
# Check if the certificate is now present (may have self-healed)
oc debug node/ip-10-0-122-129.us-east-2.compute.internal -- \\
  chroot /host ls -la /var/lib/kubelet/pki/

# If kubelet-server-current.pem is still missing, approve pending CSRs
oc get csr | grep Pending
oc get csr -o name | xargs oc adm certificate approve

# Verify kubelet is healthy on the node
oc debug node/ip-10-0-122-129.us-east-2.compute.internal -- \\
  chroot /host systemctl status kubelet
\`\`\`

**Validation:**
\`\`\`bash
# Confirm the certificate file exists
oc debug node/ip-10-0-122-129.us-east-2.compute.internal -- \\
  chroot /host ls /var/lib/kubelet/pki/kubelet-server-current.pem
\`\`\`

---

### 4. [MEDIUM] Configure an Update Channel

\`\`\`bash
# Set the appropriate update channel for OKD/SCOS
oc patch clusterversion version --type merge \\
  -p '{"spec":{"channel":"stable-4.21"}}'

# Verify
oc get clusterversion version -o jsonpath='{.spec.channel}'
\`\`\`

---

### 5. [MEDIUM] Monitor and Verify Full Upgrade Completion

After resolving Steps 1-3:
\`\`\`bash
# Watch overall upgrade progress
oc get clusterversion version -w

# Check all cluster operators are Available
oc get co | grep -v "True.*False.*False"

# Verify all nodes are on the new version
oc get nodes -o wide

# Check MCP status
oc get mcp
\`\`\`

---

## Analysis Coverage

### Tier 1 Files

| File | Type | Result |
|------|------|--------|
| cluster-scoped-resources/config.openshift.io/clusteroperators.yaml | YAML | Analyzed — 1 Error object (MCO Degraded) |
| cluster-scoped-resources/config.openshift.io/clusterversions.yaml | YAML | Analyzed — 1 Error object (Failing=True, UpdatePayloadResourceInvalid) |
| namespaces/openshift-cluster-version/core/events.yaml | YAML | Analyzed — 1 Error object |
| namespaces/openshift-etcd/core/events.yaml | YAML | Analyzed |
| namespaces/openshift-kube-apiserver/core/events.yaml | YAML | Analyzed |
| namespaces/openshift-machine-config-operator/core/events.yaml | YAML | Analyzed — MCP master degraded |
| namespaces/openshift-kube-storage-version-migrator/core/events.yaml | YAML | Analyzed |
| etcd_info/endpoint_health.json | JSON | Analyzed |
| etcd_info/member_list.json | JSON | Analyzed |
| etcd_info/alarm_list.json | JSON | Analyzed |
| etcd_info/endpoint_status.json | JSON | Analyzed |
| etcd_info/object_count.json | JSON | Analyzed |
| namespaces/openshift-cluster-version/pods/.../cluster-version-operator/logs/current.log | LOG | Analyzed — CRD apply failure identified |
| namespaces/openshift-cluster-version/pods/.../cluster-version-operator/logs/previous.log | LOG | Analyzed |
| namespaces/openshift-etcd/pods/etcd-ip-10-0-122-129/.../logs/current.log | LOG | Analyzed — etcd healthy |
| namespaces/openshift-etcd/pods/etcd-ip-10-0-49-48/.../logs/current.log | LOG | Analyzed — etcd healthy |
| namespaces/openshift-etcd/pods/etcd-ip-10-0-97-146/.../logs/current.log | LOG | Analyzed — etcd healthy |
| namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-122-129/.../logs/current.log | LOG | Analyzed — missing kubelet cert |
| namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-49-48/.../logs/current.log | LOG | Analyzed |
| namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-97-146/.../logs/current.log | LOG | Analyzed |
| namespaces/openshift-machine-config-operator/pods/machine-config-controller/.../logs/current.log | LOG | Analyzed — bootstrap config mismatch on ip-10-0-49-48 |
| namespaces/openshift-machine-config-operator/pods/machine-config-operator/.../logs/current.log | LOG | Analyzed |

> **Confidence: HIGH** — The primary root cause (CRD payload invalid) and secondary causes (MCP degradation, missing cert) are clearly evidenced across multiple corroborating sources.

### Files Not Found in This Must-Gather

- \`static-pods/kube-apiserver/*-startup.log.gz\` — startup logs not present
- \`cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigpools.yaml\` — MCP resource YAML not found
- \`cluster-scoped-resources/machineconfiguration.openshift.io/machineconfigs.yaml\` — MachineConfig definitions not found
- \`cluster-scoped-resources/migration.k8s.io/storageversionmigrations.yaml\` — storage version migration status not found
- \`host_service_logs/workers/*\` — all worker host service logs absent (no worker nodes in this cluster or not collected)
- \`cluster-scoped-resources/apiregistration.k8s.io/apiservices.yaml\` — API service availability not available
- \`cluster-scoped-resources/apiextensions.k8s.io/customresourcedefinitions.yaml\` — CRD definitions not available (would have confirmed the CRD schema issue)
- \`audit_logs/kube-apiserver/*-audit.log\` — audit logs not collected
- \`machine_config_ondisk/*/mcs-machine-config-content.json\` — on-disk MachineConfig content not available

### Priority Tiers Analyzed

- **HIGH** — 22 files analyzed (confidence: **high**)
- **MEDIUM** — not analyzed (user-driven deepening available)
- **LOW** — not analyzed (user-driven deepening available)

### Key Limitation

The \`cluster-scoped-resources/apiextensions.k8s.io/customresourcedefinitions.yaml\` file was not found in this must-gather, which would have provided the exact schema of the existing \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD and confirmed the precise nature of the \`UpdatePayloadResourceInvalid\` rejection. Additionally, \`machineconfigpools.yaml\` and \`machineconfigs.yaml\` were absent, limiting the depth of MachineConfigPool analysis to event and log data only. Analyzing medium-priority files (etcd operator logs, kube-controller-manager logs, node YAMLs) in a subsequent round could provide additional corroborating evidence.


====================================================================================================
LLM API TOKEN & COST SUMMARY — ALL PHASES
====================================================================================================
  Phase 1: Selecting probable/available files   Phase 2: YAML processing   Phase 3: Log processing
  Phase 4: Data aggregation (YAML+LOG)   Phase 5: Root Cause analysis   Phase 6: Cost Calculation

  WITH ML EXTRACTION (current run)
----------------------------------------------------------------------------------------------------
  Phase                              Input tokens  Output tokens     Cost (USD)
  0. Orchestrator ReAct loop              405,752          8,545   $   1.345431
     (8 iterations)
  1. File selection                        39,504          2,649   $   0.158247
  2. YAML processing (local ML)                 —              —              —
  3. Log processing (local Drain3)              —              —              —
  4. Data aggregation (no LLM)                  —              —              —
  5. RCA (extracted payload)               33,188          5,067   $   0.175569
  Total                                                            $   1.679247

  COMPRESSION RATIO (input size vs payload sent to LLM)
----------------------------------------------------------------------------------------------------
  Total input (YAML + logs): 20,178,587 bytes  →  Payload to LLM: 90,125 bytes
  Overall compression ratio: 223.90x

  File                                               Type     Original (B)    Payload (B)      Ratio
----------------------------------------------------------------------------------------------------
  clusteroperators.yaml                              yaml          183,895          2,339      78.62x
  clusterversions.yaml                               yaml            6,985          2,913       2.40x
  events.yaml                                        yaml           14,866            767      19.38x
  current_rare_error.txt                             log        10,684,428          2,174    4914.64x
  current_highfreq_error.txt                         log        10,684,428          4,305    2481.86x
  previous_rare_error.txt                            log           204,917            273     750.61x
  current_rare_error.txt                             log           610,129          2,174     280.65x
  current_highfreq_error.txt                         log           610,129          4,305     141.73x
  current_rare_error.txt                             log           130,236          2,174      59.91x
  current_highfreq_error.txt                         log           130,236          4,305      30.25x
  current_rare_error.txt                             log           120,696          2,174      55.52x
  current_highfreq_error.txt                         log           120,696          4,305      28.04x
  current_rare_error.txt                             log         1,982,058          2,174     911.71x
  current_highfreq_error.txt                         log         1,982,058          4,305     460.41x
  current_rare_error.txt                             log         2,172,914          2,174     999.50x
  current_highfreq_error.txt                         log         2,172,914          4,305     504.74x
  current_rare_error.txt                             log         1,960,285          2,174     901.70x
  current_highfreq_error.txt                         log         1,960,285          4,305     455.35x
  current_rare_error.txt                             log           135,300          2,174      62.24x
  current_highfreq_error.txt                         log           135,300          4,305      31.43x
  current_rare_error.txt                             log            27,435          2,174      12.62x
  current_rare_error.txt                             log            45,571          2,174      20.96x
  current_rare_error.txt                             log            27,058          2,174      12.45x
  current_rare_error.txt                             log            46,188          2,174      21.25x
  current_rare_error.txt                             log            47,420          2,174      21.81x
  current_rare_error.txt                             log            29,403          2,174      13.52x
  current_rare_error.txt                             log            52,620          2,174      24.20x
  current_highfreq_error.txt                         log            52,620          4,305      12.22x
  previous_rare_error.txt                            log             1,408            273       5.16x
  previous_rare_error.txt                            log             1,240            273       4.54x
  previous_rare_error.txt                            log             1,240            273       4.54x
  current_rare_error.txt                             log             5,399          2,174       2.48x

====================================================================================================
`;

export const mockRCATier2 = `## User Reported Issue

Test cases are failing during the OpenShift cluster update process. The cluster is attempting to upgrade from OKD/SCOS version \`4.21.0-okd-scos.ec.13\` to \`4.21.0-okd-scos.ec.18\` and has stalled in a \`Partial\` / \`Failing\` state, blocking test suite completion.

## Executive Summary

The cluster update from OKD/SCOS \`4.21.0-okd-scos.ec.13\` → \`4.21.0-okd-scos.ec.18\` is blocked by multiple compounding failures. **The key cause for the user's problem is:** the Cluster Version Operator (CVO) cannot apply an invalid CRD payload resource (\`ipaddressclaims.ipam.cluster.x-k8s.io\`), triggering a \`Failing\` condition with \`UpdatePayloadResourceInvalid\` — this is the primary upgrade blocker. This failure is compounded by a severe cluster-wide API server and etcd outage lasting approximately 20–30 minutes (13:53–14:15Z) during which all three etcd members were unreachable at different points, all three master kubelets lost API connectivity, and the \`openshift-apiserver\` Deployment cycled through at least 8 ReplicaSet revisions with persistent mount, scheduling, and readiness failures. Additionally, the MachineConfigPool \`master\` is fully degraded (0/3 nodes ready) due to a bootstrap MachineConfig mismatch on node \`ip-10-0-49-48\`, CNI was uninitialized across all nodes during the critical early upgrade window, and a missing kubelet server certificate (\`kubelet-server-current.pem\`) caused persistent TLS handshake errors. Together, these failures caused the update to stall in a \`Partial\` state with \`Failing=True\`.

## Chronology of Events

- **2026-01-12T13:41:16Z**: Cluster update from \`4.21.0-okd-scos.ec.13\` begins (\`history[1].startedTime\`). Update channel is unconfigured (\`NoChannel\`).
- **2026-01-12T13:41:36Z**: CRI-O starts on all three master nodes (\`ip-10-0-49-48\`, \`ip-10-0-122-129\`, \`ip-10-0-97-146\`) — all report missing \`/var/run/crio/version\` (fresh start) and CNI plugin uninitialized. Kubelet on all nodes immediately begins failing with \`system:anonymous\` forbidden errors — nodes not yet registered with API server.
- **2026-01-12T13:41:36–13:42:22Z**: All three master kubelets report \`NetworkReady=false\` (no CNI config in \`/etc/kubernetes/cni/net.d/\`). Kubelet cannot list nodes, services, CSI drivers, or leases — API server is not yet authenticating node credentials. CSINode publishing fails. Eviction manager cannot find node info.
- **2026-01-12T13:41:46–13:41:52Z**: Kubelet on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` reports \`kube-rbac-proxy-crio\` in CrashLoopBackOff in \`openshift-machine-config-operator\` — events rejected because \`system:anonymous\` cannot create events.
- **2026-01-12T13:41:48Z**: Kubelet certificate reloader fails (\`kubelet-server-current.pem\` missing) on \`ip-10-0-122-129\`. CSINode annotation update times out on all nodes.
- **2026-01-12T13:41:53Z**: \`openshift-apiserver\` pods (\`apiserver-549f454cb4-*\`) begin failing to mount \`audit-0\` ConfigMap and \`serving-cert\` Secret — these resources do not yet exist.
- **2026-01-12T13:42:06–13:44:22Z**: CNI plugin remains uninitialized across all three nodes. CRI-O logs persistent \`CNI plugin not yet initialized\` warnings every ~30 seconds. Pods in \`openshift-network-diagnostics\`, \`openshift-multus\`, \`openshift-e2e-loki\` cannot start due to \`NetworkPluginNotReady\`.
- **2026-01-12T13:43:07–13:43:08Z**: CRI-O on \`ip-10-0-49-48\` stops containers from previous revision; kubelet reports container IDs not found in CRI-O index (stale container references from prior static pod generation).
- **2026-01-12T13:43:36–13:44:22Z**: Kubelet on all nodes reports \`Container runtime network not ready\` — CNI still absent.
- **2026-01-12T13:44:57Z**: Kubelet on \`ip-10-0-122-129\` reports \`cluster-version-operator\` pod cannot mount \`serving-cert\` volume — CVO itself is impacted by the missing secrets.
- **2026-01-12T13:45:07Z**: \`kube-storage-version-migrator\` begins failing to list \`flowcontrol.apiserver.k8s.io/v1\` resources (FlowSchema, PriorityLevelConfiguration) — API server does not yet expose these resources at the expected version during the upgrade transition.
- **2026-01-12T13:45:41–13:45:42Z**: \`openshift-console\` downloads pods fail readiness probes (connection refused) — console not yet serving.
- **2026-01-12T13:45:48Z**: \`insights\` cluster operator transitions to \`Available=True\` — operator is healthy.
- **2026-01-12T13:46:02Z**: MCO cannot find \`kube-apiserver-server-ca\` ConfigMap — API server CA not yet propagated, indicating kube-apiserver is still rolling.
- **2026-01-12T13:46:07–13:46:34Z**: Kubelet on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` reports \`openshift-apiserver\` pods (\`apiserver-549f454cb4-*\`) cannot sync due to unmounted \`audit\` volume — context canceled.
- **2026-01-12T13:46:15Z**: MachineConfigPool \`master\` is fully degraded (0/3 nodes ready, 0 updated). Node \`ip-10-0-49-48\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that conflicts with the in-cluster controller-generated config.
- **2026-01-12T13:46:25–13:46:35Z**: Etcd guard readiness probes on \`ip-10-0-122-129\` begin timing out (\`context deadline exceeded\`).
- **2026-01-12T13:47:30Z**: CVO attempts to apply the new release payload and encounters \`UpdatePayloadResourceInvalid\` on \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD — the CRD spec in the payload is rejected by the API server.
- **2026-01-12T13:48:00Z**: CVO sets \`Failing=True\` condition. ClusterVersion enters \`Partial\` state. Update is effectively blocked.
- **2026-01-12T13:50:22Z**: \`openshift-apiserver\` pod \`apiserver-5f6f94bfc-lxhv9\` readiness probe fails with \`net/http: request canceled while waiting for connection\` — API server not yet serving on \`10.130.0.55:8443\`.
- **2026-01-12T13:50:29Z**: Kube-apiserver startup probe on \`ip-10-0-122-129\` returns HTTP 403 (\`system:anonymous\` cannot get \`/livez\`) — API server is up but RBAC not yet bootstrapped.
- **2026-01-12T13:51:10Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container (process already gone) — container lifecycle race during static pod replacement.
- **2026-01-12T13:51:23–13:51:34Z**: Kubelet on \`ip-10-0-49-48\` reports etcd-guard and oauth-apiserver readiness probe failures (\`context deadline exceeded\`).
- **2026-01-12T13:53:54–13:55:13Z**: Kube-scheduler static pod on \`ip-10-0-122-129\` is killed and replaced; kubelet cannot delete mirror pod or update pod status (API server timeout). Node lease updates begin failing with \`context deadline exceeded\` across all three nodes — **full API server outage window begins**.
- **2026-01-12T13:55:46–13:55:56Z**: Mass pod sync failures across all nodes: \`openshift-controller-manager\`, \`openshift-route-controller-manager\`, \`openshift-apiserver\` pods all report unmounted volumes (\`client-ca\`, \`serving-cert\`) with \`context canceled\` — API server unreachable.
- **2026-01-12T13:55:50Z**: \`kube-storage-version-migrator-operator\` enters CrashLoopBackOff, consistent with API server being unavailable.
- **2026-01-12T13:55:56Z**: \`openshift-apiserver\` and \`openshift-route-controller-manager\` pods report \`EOF\` and \`connection reset by peer\` on readiness probes — API server actively dropping connections.
- **2026-01-12T13:57:52Z**: CRI-O on \`ip-10-0-122-129\` fails to kill container (process not running).
- **2026-01-12T13:58:20–13:58:25Z**: Etcd guard on \`ip-10-0-122-129\` fails readiness probes repeatedly (\`context deadline exceeded\`).
- **2026-01-12T13:59:48Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container.
- **2026-01-12T13:59:49Z**: Etcd guard on \`ip-10-0-49-48\` reports \`connection refused\` on port 9980 — etcd guard container is down.
- **2026-01-12T14:00:04–14:00:29Z**: Etcd guard on \`ip-10-0-49-48\` continues failing (\`context deadline exceeded\`).
- **2026-01-12T14:00:41Z**: Etcd startup probe on \`ip-10-0-49-48\` fails with HTTP 503 (\`failed to establish etcd client: giving up getting a cached client after 3 tries\`) — **etcd itself is failing to start on the first member**.
- **2026-01-12T14:02:05Z**: CRI-O on \`ip-10-0-97-146\` fails to kill container.
- **2026-01-12T14:02:26Z**: CRI-O on \`ip-10-0-49-48\` stops container (timeout 30s).
- **2026-01-12T14:02:33–14:02:38Z**: Etcd guard on \`ip-10-0-97-146\` fails readiness probes.
- **2026-01-12T14:03:35–14:04:24Z**: CRI-O on \`ip-10-0-97-146\` and \`ip-10-0-122-129\` reports \`Failed to get the status of process\` (PID no longer exists) — containers being cleaned up.
- **2026-01-12T14:04:25Z**: Kube-apiserver guard on \`ip-10-0-122-129\` readiness probe returns HTTP 403 (\`system:anonymous\` cannot get \`/readyz\`) — kube-apiserver is running but anonymous access blocked (expected behavior).
- **2026-01-12T14:05:08Z**: \`ingress-operator\` enters CrashLoopBackOff — ingress stack impacted by API outage.
- **2026-01-12T14:05:16Z**: **etcd gRPC connections begin failing** — \`grpc: addrConn.createTransport failed\` to \`10.0.122.129:2379\` (connection refused). Multiple channels affected simultaneously. This is the etcd outage peak for the first member.
- **2026-01-12T14:05:46Z**: CRI-O on \`ip-10-0-122-129\` fails to kill container.
- **2026-01-12T14:06:41Z**: Etcd startup probe on \`ip-10-0-122-129\` fails HTTP 503 (\`failed to establish etcd client\`) — second etcd member failing to start.
- **2026-01-12T14:07:55Z**: etcd gRPC connection to \`10.0.49.48:2379\` also fails (connection refused) — second etcd endpoint unreachable.
- **2026-01-12T14:08:02Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container.
- **2026-01-12T14:08:44Z**: Etcd guard on \`ip-10-0-49-48\` fails readiness probe.
- **2026-01-12T14:08:52Z**: \`PodNetworkConnectivityCheck\` CRD not found — network connectivity check operator cannot list its CRD (API server resource not yet registered). This error persists across dozens of log files throughout the incident.
- **2026-01-12T14:08:58Z**: Etcd startup probe on \`ip-10-0-49-48\` fails HTTP 503 again.
- **2026-01-12T14:10:18Z**: CRI-O on \`ip-10-0-97-146\` fails to kill container.
- **2026-01-12T14:10:43–14:11:13Z**: Etcd guard on \`ip-10-0-97-146\` fails readiness probes repeatedly.
- **2026-01-12T14:11:13Z**: etcd gRPC connection to \`10.0.97.146:2379\` fails (connection refused) — **all three etcd members have been unreachable at some point**. Etcd startup probe on \`ip-10-0-97-146\` fails HTTP 503.
- **2026-01-12T14:22:36–14:22:47Z**: CRI-O on \`ip-10-0-49-48\` and \`ip-10-0-97-146\` reports image blob download failures (unexpected EOF from \`quay.io\`) — image pulls for new release containers are retrying.
- **2026-01-12T14:22:56Z**: Kubelet TLS handshake errors (\`EOF\`) on \`ip-10-0-49-48\` and \`ip-10-0-97-146\` — kubelet serving certificate issues persist.
- **2026-01-12T14:30:10Z**: Kube-apiserver guard on \`ip-10-0-122-129\` readiness probe returns HTTP 403 — kube-apiserver still running but guard probe using anonymous access.
- **2026-01-12T14:34:11Z**: Kube-apiserver guard on \`ip-10-0-49-48\` readiness probe returns HTTP 403.
- **2026-01-12T14:37:47–14:38:09Z**: Kube-apiserver guard on \`ip-10-0-97-146\` fails readiness probes (HTTP 500, then HTTP 500 with \`shutdown failed\`). Kube-apiserver startup probe on \`ip-10-0-97-146\` fails with \`poststarthook/rbac/bootstrap-roles failed\` — RBAC bootstrap still in progress.
- **2026-01-12T14:43:14Z**: Kube-scheduler guard on \`ip-10-0-97-146\` fails readiness probe (connection refused on port 10259) — scheduler not yet running.
- **2026-01-12T15:35:53Z**: etcd gRPC connections to \`10.0.97.146:2379\` and \`10.0.49.48:2379\` fail with \`operation was canceled\` — etcd connectivity issues persist hours after initial outage; etcd membership/leader election still stabilizing.
- **2026-01-12T15:40:56Z**: Kubelet TLS handshake error on \`ip-10-0-122-129\` — certificate issue still unresolved.
- **2026-01-12T16:56:16–16:58:58Z**: CRI-O on multiple nodes fails to kill containers (process not running) — ongoing container lifecycle cleanup from the upgrade.
- **2026-01-12T16:59:24Z**: E2E pod network disruption test pods fail readiness probes (HTTP 503) — test infrastructure detecting network disruption.
- **2026-01-12T16:59:56Z**: CRI-O stops containers on all three nodes simultaneously — likely another static pod replacement wave.

## Primary Root Cause(s)

1. **Invalid CRD Payload Resource — \`ipaddressclaims.ipam.cluster.x-k8s.io\`**
   - The CVO reports \`UpdatePayloadResourceInvalid\` when attempting to apply the CRD \`ipaddressclaims.ipam.cluster.x-k8s.io\` from the \`4.21.0-okd-scos.ec.18\` release payload. The API server rejects the CRD spec — likely due to a structural schema validation failure, a conversion webhook conflict, or an incompatible field change between the ec.13 and ec.18 CRD versions.
   - This is the **primary blocker** preventing the upgrade from completing. The CVO cannot proceed past this resource application step and enters a continuous retry loop.
   - The broader CRD registration disruption is confirmed by the \`PodNetworkConnectivityCheck\` CRD also being absent (\`controlplane.operator.openshift.io\` API group not found at 14:08:52Z, repeated across dozens of log files), consistent with the API server being unable to serve all registered resource types while rolling.
   - Evidence: \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` shows \`Failing=True\` with reason \`UpdatePayloadResourceInvalid\`; CVO \`namespaces/openshift-cluster-version/pods/.../cluster-version-operator/logs/current.log\` shows repeated failed attempts to apply this specific CRD; \`current_rare_error.txt\` confirms \`PodNetworkConnectivityCheck\` CRD not found.

2. **Cluster-Wide API Server and etcd Outage During Upgrade Transition (~13:53–14:15Z)**
   - All three master nodes lost API server connectivity simultaneously for approximately 20 minutes, confirmed by kubelet node lease update failures (\`context deadline exceeded\`) on all three nodes starting at ~13:53Z.
   - Etcd startup probes failed on all three members with HTTP 503 (\`failed to establish etcd client: giving up getting a cached client after 3 tries\`) at 14:00:41Z (\`ip-10-0-49-48\`), 14:06:41Z (\`ip-10-0-122-129\`), and 14:11:14Z (\`ip-10-0-97-146\`).
   - etcd gRPC transport failures confirmed to all three endpoints: \`10.0.122.129:2379\` (connection refused, 14:05:16Z), \`10.0.49.48:2379\` (connection refused, 14:07:55Z), \`10.0.97.146:2379\` (connection refused, 14:11:13Z) — all three etcd members were unreachable at different points, indicating the cluster lost quorum during the upgrade.
   - The \`openshift-apiserver\` Deployment cycled through at least 8 ReplicaSet revisions (\`apiserver-549f454cb4\`, \`apiserver-5f6f94bfc\`, \`apiserver-5fb49db689\`, \`apiserver-66f8cd4fdb\`, \`apiserver-68858b79fc\`, \`apiserver-76c476bdc6\`, \`apiserver-7bddf9bcb5\`, \`apiserver-9f5b9f9d4\`) with persistent \`FailedMount\`, \`FailedScheduling\`, and readiness probe failures. The startup probe failure \`[-]poststarthook/authorization.openshift.io-bootstrapclusterroles failed\` on \`apiserver-68858b79fc-lq8rx\` confirms RBAC bootstrap was incomplete during the transition.
   - etcd gRPC \`operation was canceled\` errors persist at 15:35:53Z — hours after the initial outage — indicating etcd membership and leader election were still stabilizing well into the upgrade window.
   - Evidence: \`current_highfreq_error.txt\` shows gRPC \`addrConn.createTransport failed\` to all three etcd endpoints; \`kubelet_service_rare_error.txt\` shows etcd guard readiness probe failures (\`context deadline exceeded\`) on all three nodes; \`openshift-apiserver/core/events.yaml\` shows all ReplicaSets failing with \`serving-cert\` Secret not found, \`audit-0\` ConfigMap not found, \`FailedScheduling\` (anti-affinity), and readiness probe HTTP 500 with \`[-]shutdown failed: reason withheld\`.

3. **Degraded MachineConfigPool \`master\` — Bootstrap MachineConfig Mismatch on \`ip-10-0-49-48\`**
   - Node \`ip-10-0-49-48.us-east-2.compute.internal\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that does not match the in-cluster controller-generated equivalent. The MachineConfigPool \`master\` shows 0/3 nodes updated and 0/3 nodes ready, blocking the MCO from completing its node configuration rollout.
   - \`kube-rbac-proxy-crio\` on \`ip-10-0-49-48\` is in CrashLoopBackOff from the very start of the upgrade (13:41:46Z), directly related to the MCO's inability to apply the correct MachineConfig. Events cannot be posted because the API server is not yet authenticating the node.
   - Evidence: \`namespaces/openshift-machine-config-operator/core/events.yaml\` shows \`MachineConfigPool master is degraded\`; \`namespaces/openshift-machine-config-operator/pods/machine-config-controller/.../logs/current.log\` shows the config mismatch on \`ip-10-0-49-48\`; \`kubelet_service_highfreq_error.txt\` shows \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` from 13:41:46Z.

4. **CNI Plugin Initialization Delay — Network Not Ready on All Nodes**
   - All three master nodes started with no CNI configuration in \`/etc/kubernetes/cni/net.d/\` from 13:41:36Z through at least 13:44:22Z (~3 minutes). CRI-O logged \`CNI plugin not yet initialized\` every 30 seconds on all three nodes. Kubelet reported \`NetworkReady=false\` and \`NetworkPluginNotReady\`.
   - This prevented any new pods from starting network interfaces during the critical early upgrade window, compounding the API server recovery delay and contributing to the cascade of pod failures.
   - Evidence: \`crio_service_rare_error.txt\` shows continuous CNI warnings from 13:41:36Z to 13:44:22Z on all three nodes; \`kubelet_service_highfreq_error.txt\` shows \`pod_workers.go\` errors for multiple pods with \`network is not ready\`.

5. **Missing Kubelet Server Certificate (\`kubelet-server-current.pem\`)**
   - During the early phase of the upgrade, the kubelet on \`ip-10-0-122-129\` could not initialize its certificate reloader because \`/var/lib/kubelet/pki/kubelet-server-current.pem\` was absent. This caused transient API server connectivity issues and delayed the kube-apiserver static pod rollout.
   - Kubelet TLS handshake errors (\`EOF\`) persist at 14:22:56Z, 15:40:56Z, and 16:58:56Z across all three nodes, indicating the certificate issue was not immediately self-resolved and continued to impact cluster stability throughout the upgrade window.
   - Evidence: \`namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-122-129/.../logs/current.log\` shows \`failed to initialize certificate reloader\` with the missing PEM path; \`kubelet_service_highfreq_error.txt\` shows \`http: TLS handshake error from <node-IP>: EOF\` at multiple timestamps hours after the initial failure.

## Secondary Causes / Contributing Factors

- **\`system:anonymous\` authentication during early boot**: All three kubelets started authenticating as \`system:anonymous\` for the first ~5–10 minutes of the upgrade (13:41:36–~13:42:30Z), indicating the API server's bootstrap RBAC was not yet applied when the kubelets first connected. This caused cascading failures: node registration failed, CSINode publishing failed, lease creation failed, and events could not be posted.
- **\`openshift-apiserver\` anti-affinity scheduling failures**: Multiple \`openshift-apiserver\` ReplicaSets failed scheduling with \`0/3 nodes are available: 3 node(s) didn't match pod anti-affinity rules\`, indicating that during the rolling update, the scheduler could not place new pods while old pods were still terminating on the same nodes.
- **Missing secrets/configmaps for \`openshift-apiserver\`**: The \`serving-cert\` Secret and \`audit-0\` ConfigMap were not found during the early upgrade phase, causing \`FailedMount\` events on multiple \`openshift-apiserver\` pods. These are created by the \`openshift-apiserver-operator\` and their absence indicates the operator itself was delayed.
- **Storage Version Migrator Failures**: The \`kube-storage-version-migrator\` repeatedly failed to list \`flowcontrol.apiserver.k8s.io\` resources (FlowSchema, PriorityLevelConfiguration) during the upgrade window. The \`kube-storage-version-migrator-operator\` entered CrashLoopBackOff at 13:55:50Z. While expected during API server rollout, this can delay upgrade completion if the migrator is blocked for extended periods.
- **Missing \`kube-apiserver-server-ca\` ConfigMap**: The MCO could not find this ConfigMap during the upgrade, indicating the kube-apiserver CA propagation was delayed. This is a transient condition but contributed to the MCO degradation window.
- **\`ingress-operator\` CrashLoopBackOff**: The ingress operator entered CrashLoopBackOff at ~14:05:08Z, indicating the ingress stack was also impacted by the API outage.
- **Multiple kube-controller-manager installer retries**: Installer pods for the kube-controller-manager (revisions 4, 4-retry-1, 4-retry-2) show repeated retry attempts, indicating the controller manager static pod rollout was unstable during the upgrade.
- **Image pull interruptions from \`quay.io\`**: CRI-O reported multiple \`unexpected EOF\` errors when downloading image blobs from \`quay.io\` (13:43:37Z, 13:46:07Z, 14:22:36–14:22:47Z). These retried successfully but added latency to new pod startup during the upgrade.
- **Stale container references**: Kubelet and CRI-O reported numerous \`container with ID ... not found\` errors as containers from the previous static pod generation were cleaned up. This is expected during static pod replacement but contributed to log noise and minor delays.
- **No Update Channel Configured**: The ClusterVersion shows \`NoChannel\` for the update channel. While not a direct blocker, this means the cluster cannot receive automatic update recommendations or rollback guidance from the Cincinnati update service.
- **cadvisor stats cache misses**: Multiple kubelet \`cadvisor_stats_provider.go\` errors (\`RecentStats: unable to find data in memory cache\`) across all nodes from 13:44:12Z through 16:59:56Z — transient and expected during container churn but indicate high container lifecycle activity throughout the incident.

## Aggregated Error Patterns

| Pattern | Source | Classification | Significance |
|---------|--------|----------------|--------------|
| \`UpdatePayloadResourceInvalid: unable to apply CRD ipaddressclaims.ipam.cluster.x-k8s.io\` | \`clusterversions.yaml\`, CVO \`current.log\` | **CRITICAL — Primary Blocker** | Prevents upgrade from completing; CVO cannot apply CRD from ec.18 payload; continuous retry loop |
| \`Failing=True, Progressing=True (Partial)\` on ClusterVersion | \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` | **CRITICAL — Upgrade Stalled** | Upgrade is actively failing and stalled in partial state |
| \`grpc: addrConn.createTransport failed\` to all three etcd endpoints (\`:2379\`) | \`current_highfreq_error.txt\` | **CRITICAL — Etcd Outage** | All three etcd members unreachable at different points; cluster lost quorum during upgrade |
| Etcd startup probe HTTP 503 (\`failed to establish etcd client: giving up after 3 tries\`) | \`kubelet_service_highfreq_error.txt\` | **CRITICAL — Etcd Startup Failure** | Etcd containers failed to start on all three nodes during static pod replacement |
| Etcd guard readiness probe \`context deadline exceeded\` on all three nodes | \`kubelet_service_rare_error.txt\` | **CRITICAL — Etcd Unavailability** | Etcd guard confirms etcd not serving on port 9980 across all masters |
| Kubelet node lease update \`context deadline exceeded\` / \`request canceled\` on all three nodes | \`kubelet_service_rare_error.txt\` | **CRITICAL — API Server Outage** | All three nodes unable to update leases for ~20 minutes; API server unreachable |
| \`MachineConfigPool master is degraded\` (0/3 nodes ready, 0/3 updated) | \`namespaces/openshift-machine-config-operator/core/events.yaml\` | **HIGH — MCO Blocker** | Blocks node configuration rollout; bootstrap MachineConfig mismatch on \`ip-10-0-49-48\` |
| \`Node ip-10-0-49-48 has bootstrap-generated MachineConfig mismatch (99-master-generated-registries)\` | \`namespaces/openshift-machine-config-operator/pods/machine-config-controller/.../logs/current.log\` | **HIGH — MCO Config Mismatch** | Specific node stuck with conflicting bootstrap config; prevents MCO rollout |
| \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\`, \`ip-10-0-122-129\` | \`kubelet_service_highfreq_error.txt\` | **HIGH — MCO Component Failure** | MCO proxy sidecar failing from 13:41:46Z; related to MachineConfig mismatch |
| \`ClusterOperator machine-config-operator: Degraded=True\` | \`cluster-scoped-resources/config.openshift.io/clusteroperators.yaml\` | **HIGH — Operator Degraded** | MCO operator itself reporting degraded state during upgrade |
| \`openshift-apiserver\` pods: \`FailedMount\` (\`serving-cert\` Secret, \`audit-0\` ConfigMap not found) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — API Server Rollout Blocked** | Required secrets/configmaps not yet created; 8+ ReplicaSet revisions affected |
| \`openshift-apiserver\` pods: \`FailedScheduling\` (anti-affinity — 0/3 nodes available) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — Scheduling Failure** | Anti-affinity prevents placement while old pods still terminating |
| \`openshift-apiserver\` readiness probe HTTP 500 (\`[-]shutdown failed: reason withheld\`) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — API Server Unhealthy** | Pods in shutdown state still receiving readiness checks; 10+ occurrences per pod |
| \`[-]poststarthook/authorization.openshift.io-bootstrapclusterroles failed\` | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — RBAC Bootstrap Incomplete** | RBAC bootstrap not complete during openshift-apiserver startup |
| \`poststarthook/rbac/bootstrap-roles failed\` on kube-apiserver \`ip-10-0-97-146\` | \`kubelet_service_rare_error.txt\` | **HIGH — RBAC Bootstrap Incomplete** | Kube-apiserver RBAC bootstrap still in progress at 14:37:47Z |
| \`system:anonymous\` forbidden errors (nodes, services, leases, CSI drivers) | \`kubelet_service_rare_error.txt\` | **HIGH — Auth Bootstrap Delay** | Kubelets not yet authenticated; RBAC not bootstrapped at upgrade start |
| \`CNI plugin not yet initialized\` / \`NetworkPluginNotReady\` / \`NetworkReady=false\` | \`crio_service_rare_error.txt\`, \`kubelet_service_highfreq_error.txt\` | **HIGH — Network Initialization Delay** | All three nodes had no CNI config for ~3 minutes; pods could not start networking |
| \`failed to initialize certificate reloader\` (\`kubelet-server-current.pem\` missing) | \`namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-122-129/.../logs/current.log\` | **HIGH — PKI Bootstrap Issue** | Missing kubelet server cert delays kube-apiserver static pod rollout |
| \`http: TLS handshake error from <node-IP>: EOF\` | \`kubelet_service_highfreq_error.txt\` | **HIGH — Kubelet TLS Issue** | Kubelet serving certificate problems persist at 14:22Z, 15:40Z, 16:58Z |
| \`failed to list flowcontrol.apiserver.k8s.io/v1 FlowSchema: the server could not find the requested resource\` | \`namespaces/openshift-kube-storage-version-migrator/pods/.../logs/current.log\` | **MEDIUM — Storage Migrator Blocked** | Storage migrator blocked on missing API resource during rollout |
| \`kube-storage-version-migrator-operator\` CrashLoopBackOff | \`namespaces/openshift-kube-storage-version-migrator/core/events.yaml\` | **MEDIUM — Operator Failure** | Storage version migrator operator down during API outage |
| \`ingress-operator\` CrashLoopBackOff | \`namespaces/openshift-ingress-operator/core/events.yaml\` | **MEDIUM — Operator Failure** | Ingress operator impacted by API outage |
| \`configmaps "kube-apiserver-server-ca" not found\` | \`namespaces/openshift-machine-config-operator/pods/machine-config-operator/.../logs/current.log\` | **MEDIUM — CA Propagation Delay** | MCO cannot read CA configmap; API server CA propagation delayed |
| \`PodNetworkConnectivityCheck\` CRD not found (\`controlplane.operator.openshift.io\`) | \`current_rare_error.txt\` | **MEDIUM — CRD Registration Delay** | Network connectivity check operator cannot function; CRD not yet registered during API server roll |
| etcd gRPC \`operation was canceled\` at 15:35:53Z | \`current_highfreq_error.txt\` | **MEDIUM — Etcd Stabilization** | Etcd connections still being canceled hours after outage; etcd not fully stable |
| \`kube-controller-manager installer-4 retry-1, retry-2 failures\` | \`namespaces/openshift-kube-controller-manager/pods/installer-*/logs\` | **MEDIUM — Static Pod Rollout Unstable** | Controller manager static pod rollout required multiple retries |
| CRI-O \`Killing container ... failed: process not running\` | \`crio_service_rare_error.txt\` | **LOW — Container Lifecycle Race** | Containers already exited before SIGKILL; expected during static pod replacement |
| CRI-O \`Error encountered when checking whether cri-o should wipe containers\` (missing \`/var/run/crio/version\`) | \`crio_service_rare_error.txt\` | **LOW — CRI-O Fresh Start** | Normal on first start after reboot/upgrade; all three nodes affected |
| Image blob download \`unexpected EOF\` from \`quay.io\` | \`crio_service_rare_error.txt\` | **LOW — Network Transient** | Image pull retries; resolved automatically but adds latency to pod startup |
| \`cadvisor_stats_provider: RecentStats: unable to find data in memory cache\` | \`kubelet_service_rare_error.txt\` | **LOW — Stats Cache Miss** | Transient during container churn; no operational impact |
| \`Failed to get the status of process with PID ... no such file or directory\` | \`crio_service_rare_error.txt\` | **LOW — Process Cleanup Race** | CRI-O checking PID of already-exited process; benign |
| \`NoChannel\` — no update channel configured on ClusterVersion | \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` | **LOW — Configuration Gap** | Cluster cannot receive automatic update recommendations or rollback guidance |

## Remediation
1. [IMMEDIATE — CRITICAL] Resolve the Invalid CRD Payload Resource Blocking the CVO

   The \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD in the \`4.21.0-okd-scos.ec.18\` payload is being rejected by the API server with \`UpdatePayloadResourceInvalid\`. This is the primary upgrade blocker and must be resolved before any other remediation can result in a completed upgrade.

   Steps:
   \`\`\`bash
   # Check the current state of the CRD
   oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o yaml

   # Get the exact CVO failure message for the rejection reason
   oc get clusterversion version -o jsonpath='{.status.conditions[?(@.type=="Failing")].message}'

   # Check CVO logs for the specific API server rejection detail
   oc logs -n openshift-cluster-version deployment/cluster-version-operator --tail=200 \\
     | grep -i "ipaddressclaim\\|invalid\\|UpdatePayloadResourceInvalid"

   # Check for any conversion webhooks that may be conflicting
   oc get validatingwebhookconfigurations,mutatingwebhookconfigurations \\
     | grep -i "ipam\\|cluster-api\\|capi"

   # Check if any IPAddressClaim objects exist (must be empty before deletion)
   oc get ipaddressclaims --all-namespaces

   # Option A: If no IPAddressClaim objects exist, delete the CRD and allow CVO to recreate it
   oc delete crd ipaddressclaims.ipam.cluster.x-k8s.io

   # Option B: If IPAddressClaim objects exist, examine the schema difference
   # and manually patch the CRD to match what ec.18 expects
   oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o json > /tmp/ipaddressclaims-current.json
   # Compare with the payload CRD spec and apply the necessary schema changes

   # After resolving, force CVO to retry the update
   oc patch clusterversion version --type=merge \\
     -p '{"spec":{"desiredUpdate":{"version":"4.21.0-okd-scos.ec.18"}}}'
   \`\`\`

   Validation:
   \`\`\`bash
   # Confirm CVO is no longer in Failing state
   oc get clusterversion version \\
     -o jsonpath='{.status.conditions[?(@.type=="Failing")].status}'
   # Expected: False

   # Confirm CRD is accepted by the API server
   oc get crd ipaddressclaims.ipam.cluster.x-k8s.io \\
     -o jsonpath='{.status.conditions[?(@.type=="NamesAccepted")].status}'
   # Expected: True

   # Confirm CVO is progressing
   oc get clusterversion version -w
   # Expected: Progressing=True (healthy progress), Failing=False
   \`\`\`

2. [IMMEDIATE — CRITICAL] Verify etcd Health and Quorum Recovery

   All three etcd members were unreachable at different points during the upgrade (14:00–14:11Z), and etcd gRPC cancellation errors persisted as late as 15:35:53Z. Verify etcd has fully recovered and quorum is stable before proceeding with any other remediation.

   Steps:
   \`\`\`bash
   # Check etcd pod status on all master nodes
   oc get pods -n openshift-etcd -o wide

   # Check etcd cluster health via etcdctl
   oc rsh -n openshift-etcd \\
     $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
     etcdctl \\
       --cacert /etc/kubernetes/static-pod-resources/etcd-certs/configmaps/etcd-serving-ca/ca-bundle.crt \\
       --cert /etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-ip-10-0-49-48.us-east-2.compute.internal.crt \\
       --key /etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-ip-10-0-49-48.us-east-2.compute.internal.key \\
       --endpoints https://10.0.49.48:2379,https://10.0.122.129:2379,https://10.0.97.146:2379 \\
       endpoint health

   # Check etcd member list to confirm all three members are present
   oc rsh -n openshift-etcd \\
     $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
     etcdctl \\
       --cacert /etc/kubernetes/static-pod-resources/etcd-certs/configmaps/etcd-serving-ca/ca-bundle.crt \\
       --cert /etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-ip-10-0-49-48.us-east-2.compute.internal.crt \\
       --key /etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-ip-10-0-49-48.us-east-2.compute.internal.key \\
       --endpoints https://10.0.49.48:2379,https://10.0.122.129:2379,https://10.0.97.146:2379 \\
       member list

   # Check etcd cluster operator status
   oc get co etcd -o yaml | grep -A 20 "conditions:"

   # Check etcd guard pods
   oc get pods -n openshift-etcd -l app=etcd-guard -o wide

   # If any etcd member is unhealthy, check its logs
   oc logs -n openshift-etcd -l app=etcd --tail=100 \\
     | grep -i "error\\|fail\\|panic\\|leader"
   \`\`\`

   Validation:
   \`\`\`bash
   # All three endpoints should report "healthy"
   # etcd operator should show Available=True, Degraded=False, Progressing=False
   oc get co etcd
   # Expected: Available=True  Progressing=False  Degraded=False

   # No recent gRPC transport failures in etcd logs
   oc logs -n openshift-etcd -l app=etcd --tail=50 \\
     | grep -i "addrConn\\|connection refused\\|operation was canceled"
   # Expected: no recent output
   \`\`\`

3. [IMMEDIATE — CRITICAL] Resolve MachineConfigPool \`master\` Degradation

   The master MachineConfigPool is degraded with 0/3 nodes updated due to a bootstrap MachineConfig mismatch (\`99-master-generated-registries\`) on \`ip-10-0-49-48\`. This blocks the MCO from completing node configuration and is a prerequisite for the upgrade to proceed. The \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` is a direct symptom of this mismatch.

   Steps:
   \`\`\`bash
   # Check current MachineConfigPool status
   oc get mcp master -o yaml

   # Identify the mismatched MachineConfig on ip-10-0-49-48
   oc get node ip-10-0-49-48.us-east-2.compute.internal \\
     -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/currentConfig}'
   oc get node ip-10-0-49-48.us-east-2.compute.internal \\
     -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/desiredConfig}'

   # Examine the bootstrap-generated MachineConfig
   oc get mc 99-master-generated-registries -o yaml

   # Check MCO logs for the specific mismatch details
   oc logs -n openshift-machine-config-operator deployment/machine-config-controller \\
     --tail=100 | grep -i "ip-10-0-49-48\\|mismatch\\|bootstrap\\|generated-registries"

   # Check kube-rbac-proxy-crio status on the affected nodes
   oc get pod -n openshift-machine-config-operator \\
     -l app=kube-rbac-proxy-crio \\
     --field-selector spec.nodeName=ip-10-0-49-48.us-east-2.compute.internal

   # Option A: Force the node to use the correct desired config by updating the annotation
   DESIRED_MC=$(oc get node ip-10-0-49-48.us-east-2.compute.internal \\
     -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/desiredConfig}')
   oc annotate node ip-10-0-49-48.us-east-2.compute.internal \\
     machineconfiguration.openshift.io/currentConfig=\${DESIRED_MC} --overwrite

   # Option B: Drain the node and force MCO to re-apply the correct config
   oc adm drain ip-10-0-49-48.us-east-2.compute.internal \\
     --ignore-daemonsets --delete-emptydir-data --force

   # After drain, SSH to the node and restart the machine-config-daemon
   oc debug node/ip-10-0-49-48.us-east-2.compute.internal -- \\
     chroot /host systemctl restart machine-config-daemon
   \`\`\`

   Validation:
   \`\`\`bash
   # MachineConfigPool should show all nodes updated and no degraded nodes
   oc get mcp master
   # Expected: UPDATED=3, READYMACHINECOUNT=3, DEGRADEDMACHINECOUNT=0

   # Verify kube-rbac-proxy-crio is no longer in CrashLoopBackOff
   oc get pod -n openshift-machine-config-operator -l app=kube-rbac-proxy-crio
   # Expected: all pods Running
   \`\`\`

4. [HIGH] Restore Missing Kubelet Server Certificate on All Master Nodes

   The kubelet server certificate (\`kubelet-server-current.pem\`) was missing at upgrade start on \`ip-10-0-122-129\`, causing TLS handshake errors that persisted across all three nodes as late as 16:58Z. Pending CSRs must be approved to allow the kubelet to obtain and persist its serving certificate.

   Steps:
   \`\`\`bash
   # Check if the certificate now exists on all master nodes
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     echo "=== Checking \${node} ==="
     oc debug node/\${node}.us-east-2.compute.internal -- \\
       chroot /host ls -la /var/lib/kubelet/pki/kubelet-server-current.pem 2>/dev/null \\
       || echo "MISSING on \${node}"
   done

   # Check for pending kubelet serving CSRs
   oc get csr | grep -i "kubelet-serving\\|Pending"

   # Approve all pending kubelet serving CSRs
   oc get csr -o name | xargs oc adm certificate approve

   # Check the machine-approver operator status
   oc get co machine-approver -o yaml | grep -A 10 "conditions:"

   # Verify kubelet is using the correct certificate after approval
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     echo "=== \${node} ==="
     oc debug node/\${node}.us-east-2.compute.internal -- \\
       chroot /host openssl x509 \\
         -in /var/lib/kubelet/pki/kubelet-server-current.pem \\
         -noout -dates -subject 2>/dev/null || echo "Certificate not yet present on \${node}"
   done
   \`\`\`

   Validation:
   \`\`\`bash
   # No pending kubelet serving CSRs
   oc get csr | grep Pending
   # Expected: no output

   # No recent TLS handshake errors in kubelet logs on any master node
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     echo "=== \${node} ==="
     oc adm node-logs \${node}.us-east-2.compute.internal \\
       --unit=kubelet --tail=50 | grep "TLS handshake"
   done
   # Expected: no recent errors
   \`\`\`

5. [HIGH] Restore \`openshift-apiserver\` to Healthy State

   The \`openshift-apiserver\` Deployment cycled through 8+ ReplicaSets with persistent \`FailedMount\`, \`FailedScheduling\`, and readiness probe failures. Verify it has stabilized, all required secrets and configmaps are present, and the operator is healthy.

   Steps:
   \`\`\`bash
   # Check current openshift-apiserver pod status
   oc get pods -n openshift-apiserver -o wide

   # Verify required secrets and configmaps exist
   oc get secret serving-cert -n openshift-apiserver
   oc get configmap audit-0 -n openshift-apiserver

   # Check the openshift-apiserver cluster operator status
   oc get co openshift-apiserver -o yaml | grep -A 20 "conditions:"

   # Check the openshift-apiserver-operator for errors
   oc logs -n openshift-apiserver-operator \\
     deployment/openshift-apiserver-operator --tail=100

   # Check for any remaining FailedMount or scheduling events
   oc get events -n openshift-apiserver \\
     --sort-by='.lastTimestamp' | grep -i "failed\\|error" | tail -20

   # If the operator is stuck, restart it to trigger reconciliation
   oc rollout restart deployment/openshift-apiserver-operator \\
     -n openshift-apiserver-operator

   # Monitor the rollout
   oc rollout status deployment/apiserver -n openshift-apiserver
   \`\`\`

   Validation:
   \`\`\`bash
   # All openshift-apiserver pods should be Running and Ready (3/3)
   oc get pods -n openshift-apiserver
   # Expected: 3 pods, all Running and Ready

   # openshift-apiserver cluster operator should be fully healthy
   oc get co openshift-apiserver
   # Expected: Available=True  Progressing=False  Degraded=False
   \`\`\`

6. [HIGH] Verify CNI and Network Operator Recovery

   CNI was uninitialized on all three nodes at upgrade start, preventing pod networking for ~3 minutes. Verify the network operator has fully recovered and CNI configuration is present on all master nodes.

   Steps:
   \`\`\`bash
   # Check network cluster operator status
   oc get co network -o yaml | grep -A 10 "conditions:"

   # Verify CNI config exists on all master nodes
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     echo "=== \${node} ==="
     oc debug node/\${node}.us-east-2.compute.internal -- \\
       chroot /host ls /etc/kubernetes/cni/net.d/
   done

   # Check multus and network operator pods
   oc get pods -n openshift-multus -o wide
   oc get pods -n openshift-network-operator -o wide

   # Check for any remaining NetworkPluginNotReady conditions on nodes
   oc get nodes -o jsonpath=\\
     '{range .items[*]}{.metadata.name}{"\\t"}{range .status.conditions[*]}{.type}={.status}{"\\t"}{end}{"\\n"}{end}'

   # If CNI config is still missing on any node, restart the network operator
   oc rollout restart deployment/network-operator -n openshift-network-operator
   \`\`\`

   Validation:
   \`\`\`bash
   # All nodes should be in Ready state
   oc get nodes
   # Expected: all nodes STATUS=Ready

   # No NetworkPluginNotReady in node conditions
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     oc describe node \${node}.us-east-2.compute.internal \\
       | grep -i "network\\|cni\\|ready"
   done
   # Expected: NetworkReady=true, no CNI errors
   \`\`\`

7. [MEDIUM] Clear Stale Container References in CRI-O

   CRI-O and kubelet reported numerous stale container ID references and failed kill attempts throughout the upgrade. While mostly self-resolving, verify no containers are stuck in an unrecoverable state on any master node.

   Steps:
   \`\`\`bash
   # Check for containers in unknown or stuck states on all master nodes
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     echo "=== \${node} ==="
     oc debug node/\${node}.us-east-2.compute.internal -- \\
       chroot /host crictl ps -a | grep -v "Running\\|Exited" || echo "No stuck containers"
   done

   # If any containers are stuck, force remove them
   # oc debug node/<node>.us-east-2.compute.internal -- \\
   #   chroot /host crictl rm --force <container-id>

   # Check CRI-O service status and recent errors on all nodes
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     echo "=== \${node} ==="
     oc adm node-logs \${node}.us-east-2.compute.internal \\
       --unit=crio --tail=20 | grep -i "error\\|fail\\|kill"
   done
   \`\`\`

   Validation:
   \`\`\`bash
   # No containers in unknown/stuck state on any node
   for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
     oc adm node-logs \${node}.us-east-2.compute.internal \\
       --unit=crio --tail=20 | grep -i "error\\|fail"
   done
   # Expected: no persistent errors; only transient cleanup messages acceptable
   \`\`\`

8. [MEDIUM] Configure an Update Channel

   The ClusterVersion shows \`NoChannel\`, preventing the cluster from receiving automatic update recommendations or rollback guidance from the Cincinnati update service.

   Steps:
   \`\`\`bash
   # Set the appropriate update channel for OKD/SCOS 4.21
   oc patch clusterversion version --type merge \\
     -p '{"spec":{"channel":"stable-4.21"}}'

   # Verify the channel is set
   oc get clusterversion version -o jsonpath='{.spec.channel}'
   # Expected: stable-4.21
   \`\`\`

   Validation:
   \`\`\`bash
   # Confirm the channel is configured and the cluster can reach the update service
   oc get clusterversion version -o yaml | grep -A 5 "channel:"
   # Expected: channel: stable-4.21 with no NoChannel condition
   \`\`\`

9. [MEDIUM] Resume and Monitor Full Upgrade Completion

   Once all critical and high-severity issues above are resolved, resume the cluster update and monitor for successful completion across all cluster operators and nodes.

   Steps:
   \`\`\`bash
   # Verify all cluster operators are healthy before resuming
   oc get co | grep -v "True.*False.*False"
   # Expected: no output (all operators Available=True, Progressing=False, Degraded=False)

   # Check current cluster version status
   oc get clusterversion version -o yaml

   # If the update is paused or stuck, force a CVO reconcile
   oc patch clusterversion version --type=merge \\
     -p '{"spec":{"desiredUpdate":{"version":"4.21.0-okd-scos.ec.18"}}}'

   # Monitor the update progress continuously
   watch -n 10 'oc get clusterversion version; echo "---"; \\
     oc get co | grep -v "True.*False.*False"; echo "---"; oc get mcp'

   # Monitor CVO logs for progress
   oc logs -n openshift-cluster-version \\
     deployment/cluster-version-operator -f --tail=50

   # Monitor MCO progress
   watch -n 10 'oc get mcp'
   \`\`\`

   Validation:
   \`\`\`bash
   # Cluster version should show upgrade completed
   oc get clusterversion version \\
     -o jsonpath='{.status.history[0].state}'
   # Expected: Completed

   # All cluster operators should be fully healthy
   oc get co | grep -v "True.*False.*False"
   # Expected: no output

   # All MachineConfigPools should be fully updated
   oc get mcp
   # Expected: UPDATED=3, READYMACHINECOUNT=3, DEGRADEDMACHINECOUNT=0 for master pool

   # All nodes should be on the new version and Ready
   oc get nodes -o wide
   # Expected: all nodes Ready, VERSION=v1.x.x matching ec.18
   \`\`\`


====================================================================================================
LLM API TOKEN & COST SUMMARY — ALL PHASES
====================================================================================================
  Phase 1: Selecting probable/available files   Phase 2: YAML processing   Phase 3: Log processing
  Phase 4: Data aggregation (YAML+LOG)   Phase 5: Root Cause analysis   Phase 6: Cost Calculation

  WITH ML EXTRACTION (current run)
----------------------------------------------------------------------------------------------------
  Phase                              Input tokens  Output tokens     Cost (USD)
  0. Orchestrator ReAct loop              405,752          8,545   $   1.345431
     (8 iterations)
  1. File selection                        39,504          2,649   $   0.158247
  2. YAML processing (local ML)                 —              —              —
  3. Log processing (local Drain3)              —              —              —
  4. Data aggregation (no LLM)                  —              —              —
  5. RCA (extracted payload)              171,994         31,494   $   0.988392
  Total                                                            $   2.492070

  COMPRESSION RATIO (input size vs payload sent to LLM)
----------------------------------------------------------------------------------------------------
  Total input (YAML + logs): 43,027,373 bytes  →  Payload to LLM: 292,606 bytes
  Overall compression ratio: 147.05x

  File                                               Type     Original (B)    Payload (B)      Ratio
----------------------------------------------------------------------------------------------------
  clusteroperators.yaml                              yaml          183,895          2,339      78.62x
  clusterversions.yaml                               yaml            6,985          2,913       2.40x
  events.yaml                                        yaml          460,266         74,341       6.19x
  current_rare_error.txt                             log        10,684,428          2,174    4914.64x
  current_highfreq_error.txt                         log        10,684,428          4,305    2481.86x
  previous_rare_error.txt                            log           204,917            273     750.61x
  current_rare_error.txt                             log           610,129          2,174     280.65x
  current_highfreq_error.txt                         log           610,129          4,305     141.73x
  current_rare_error.txt                             log           130,236          2,174      59.91x
  current_highfreq_error.txt                         log           130,236          4,305      30.25x
  current_rare_error.txt                             log           120,696          2,174      55.52x
  current_highfreq_error.txt                         log           120,696          4,305      28.04x
  current_rare_error.txt                             log         1,982,058          2,174     911.71x
  current_highfreq_error.txt                         log         1,982,058          4,305     460.41x
  current_rare_error.txt                             log         2,172,914          2,174     999.50x
  current_highfreq_error.txt                         log         2,172,914          4,305     504.74x
  current_rare_error.txt                             log         1,960,285          2,174     901.70x
  current_highfreq_error.txt                         log         1,960,285          4,305     455.35x
  current_rare_error.txt                             log           135,300          2,174      62.24x
  current_highfreq_error.txt                         log           135,300          4,305      31.43x
  current_rare_error.txt                             log            27,435          2,174      12.62x
  current_rare_error.txt                             log            45,571          2,174      20.96x
  current_rare_error.txt                             log            27,058          2,174      12.45x
  current_rare_error.txt                             log            46,188          2,174      21.25x
  current_rare_error.txt                             log            47,420          2,174      21.81x
  current_rare_error.txt                             log            29,403          2,174      13.52x
  current_rare_error.txt                             log            52,620          2,174      24.20x
  current_highfreq_error.txt                         log            52,620          4,305      12.22x
  previous_rare_error.txt                            log             1,408            273       5.16x
  previous_rare_error.txt                            log             1,240            273       4.54x
  previous_rare_error.txt                            log             1,240            273       4.54x
  current_rare_error.txt                             log             5,399          2,174       2.48x
  current_rare_error.txt                             log           115,946            387     299.60x
  current_highfreq_error.txt                         log           115,946          3,067      37.80x
  current_rare_error.txt                             log            23,352            387      60.34x
  current_rare_error.txt                             log            22,986            387      59.40x
  current_rare_error.txt                             log            22,986            387      59.40x
  current_rare_error.txt                             log            57,526            387     148.65x
  current_rare_error.txt                             log            57,512            387     148.61x
  current_rare_error.txt                             log            57,519            387     148.63x
  current_rare_error.txt                             log            57,933            387     149.70x
  current_rare_error.txt                             log            57,919            387     149.66x
  current_rare_error.txt                             log            57,926            387     149.68x
  current_rare_error.txt                             log            58,166            387     150.30x
  current_rare_error.txt                             log            58,152            387     150.26x
  current_rare_error.txt                             log            58,159            387     150.28x
  current_rare_error.txt                             log            58,532            387     151.25x
  current_rare_error.txt                             log            58,152            387     150.26x
  current_rare_error.txt                             log            58,159            387     150.28x
  current_rare_error.txt                             log           195,197            387     504.39x
  current_highfreq_error.txt                         log           195,197          3,067      63.64x
  current_rare_error.txt                             log           894,978            387    2312.60x
  current_highfreq_error.txt                         log           894,978          3,067     291.81x
  current_rare_error.txt                             log             2,530            387       6.54x
  current_rare_error.txt                             log            16,605            387      42.91x
  current_rare_error.txt                             log            63,343            387     163.68x
  current_rare_error.txt                             log             2,528            387       6.53x
  current_rare_error.txt                             log            13,805            387      35.67x
  current_rare_error.txt                             log            66,081            387     170.75x
  current_rare_error.txt                             log            12,656            387      32.70x
  current_rare_error.txt                             log            34,972            387      90.37x
  current_rare_error.txt                             log            34,775            387      89.86x
  current_rare_error.txt                             log            34,955            387      90.32x
  current_rare_error.txt                             log            34,941            387      90.29x
  current_rare_error.txt                             log            34,782            387      89.88x
  current_rare_error.txt                             log            35,178            387      90.90x
  current_rare_error.txt                             log            35,164            387      90.86x
  current_rare_error.txt                             log            35,171            387      90.88x
  current_rare_error.txt                             log            35,178            387      90.90x
  current_rare_error.txt                             log            35,164            387      90.86x
  current_rare_error.txt                             log            35,171            387      90.88x
  current_rare_error.txt                             log            73,217            387     189.19x
  current_rare_error.txt                             log             4,434            387      11.46x
  current_rare_error.txt                             log            62,123            387     160.52x
  current_rare_error.txt                             log             2,504            387       6.47x
  current_rare_error.txt                             log            62,122            387     160.52x
  current_rare_error.txt                             log             2,504            387       6.47x
  current_rare_error.txt                             log           250,941            387     648.43x
  current_highfreq_error.txt                         log           250,941          3,067      81.82x
  current_rare_error.txt                             log            15,671            387      40.49x
  current_rare_error.txt                             log           176,747            387     456.71x
  current_highfreq_error.txt                         log           176,747          3,067      57.63x
  current_rare_error.txt                             log            15,673            387      40.50x
  current_rare_error.txt                             log           261,134            387     674.76x
  current_highfreq_error.txt                         log           261,134          3,067      85.14x
  current_rare_error.txt                             log            15,673            387      40.50x
  kubelet_service_rare_error.txt                     log        12,137,055         81,906     148.18x
  kubelet_service_highfreq_error.txt                 log        12,137,055         76,466     158.72x
  crio_service_rare_error.txt                        log         5,341,176         18,676     285.99x
  crio_service_highfreq_error.txt                    log         5,341,176          2,035    2624.66x

====================================================================================================
`;

export const mockRCAFinal = `## User Reported Issue

Test cases are failing during the OpenShift cluster update process. The cluster is attempting to upgrade from OKD/SCOS version \`4.21.0-okd-scos.ec.13\` to \`4.21.0-okd-scos.ec.18\` and has stalled in a \`Partial\` / \`Failing\` state, blocking test suite completion.

## Executive Summary

The cluster update from OKD/SCOS \`4.21.0-okd-scos.ec.13\` → \`4.21.0-okd-scos.ec.18\` is blocked by multiple compounding failures across six distinct failure domains. **The primary cause of the user's problem is:** the Cluster Version Operator (CVO) cannot apply an invalid CRD payload resource (\`ipaddressclaims.ipam.cluster.x-k8s.io\`), triggering \`UpdatePayloadResourceInvalid\` and entering a continuous retry loop that prevents the upgrade from advancing. This primary blocker is compounded by: (1) a cluster-wide API server and etcd quorum loss lasting approximately 20–30 minutes (13:53–14:15Z) during which all three etcd members were simultaneously unreachable and the \`openshift-apiserver\` Deployment cycled through at least 8 ReplicaSet revisions; (2) a fully degraded MachineConfigPool \`master\` (0/3 nodes ready) due to a bootstrap MachineConfig mismatch on node \`ip-10-0-49-48\`; (3) a complete OLM subsystem failure where the \`packageserver\` CSV was stuck in \`Installing\`, the \`community-operators\` CatalogSource was unreachable via gRPC for 10+ minutes, and all OLM operator pods failed scheduling due to untolerated node taints and missing serving-cert Secrets; (4) CNI plugin initialization delay across all three master nodes during the critical early upgrade window; and (5) a missing kubelet server certificate (\`kubelet-server-current.pem\`) causing persistent TLS handshake errors across all nodes for hours. Together, these failures caused the update to stall in a \`Partial\` state with \`Failing=True\`.

## Chronology of Events

- **2026-01-12T13:41:16Z**: Cluster update from \`4.21.0-okd-scos.ec.13\` begins (\`history[1].startedTime\`). Update channel is unconfigured (\`NoChannel\`).
- **2026-01-12T13:41:36Z**: CRI-O starts on all three master nodes (\`ip-10-0-49-48\`, \`ip-10-0-122-129\`, \`ip-10-0-97-146\`) — all report missing \`/var/run/crio/version\` (fresh start) and CNI plugin uninitialized. Kubelet on all nodes immediately begins failing with \`system:anonymous\` forbidden errors — nodes not yet registered with API server.
- **2026-01-12T13:41:36–13:42:22Z**: All three master kubelets report \`NetworkReady=false\` (no CNI config in \`/etc/kubernetes/cni/net.d/\`). Kubelet cannot list nodes, services, CSI drivers, or leases — API server is not yet authenticating node credentials. CSINode publishing fails. Eviction manager cannot find node info.
- **2026-01-12T13:41:46–13:41:52Z**: Kubelet on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` reports \`kube-rbac-proxy-crio\` in CrashLoopBackOff in \`openshift-machine-config-operator\` — events rejected because \`system:anonymous\` cannot create events.
- **2026-01-12T13:41:48Z**: Kubelet certificate reloader fails (\`kubelet-server-current.pem\` missing) on \`ip-10-0-122-129\`. CSINode annotation update times out on all nodes.
- **2026-01-12T13:41:53Z**: \`openshift-apiserver\` pods (\`apiserver-549f454cb4-*\`) begin failing to mount \`audit-0\` ConfigMap and \`serving-cert\` Secret — these resources do not yet exist.
- **2026-01-12T13:42:06–13:44:22Z**: CNI plugin remains uninitialized across all three nodes. CRI-O logs persistent \`CNI plugin not yet initialized\` warnings every ~30 seconds. Pods in \`openshift-network-diagnostics\`, \`openshift-multus\`, \`openshift-e2e-loki\` cannot start due to \`NetworkPluginNotReady\`.
- **2026-01-12T13:43:07–13:43:08Z**: CRI-O on \`ip-10-0-49-48\` stops containers from previous revision; kubelet reports container IDs not found in CRI-O index (stale container references from prior static pod generation).
- **2026-01-12T13:43:36–13:44:22Z**: Kubelet on all nodes reports \`Container runtime network not ready\` — CNI still absent.
- **2026-01-12T13:44:57Z**: Kubelet on \`ip-10-0-122-129\` reports \`cluster-version-operator\` pod cannot mount \`serving-cert\` volume — CVO itself is impacted by the missing secrets.
- **2026-01-12T13:45:07Z**: \`kube-storage-version-migrator\` begins failing to list \`flowcontrol.apiserver.k8s.io/v1\` resources (FlowSchema, PriorityLevelConfiguration) — API server does not yet expose these resources at the expected version during the upgrade transition.
- **2026-01-12T13:45:41–13:45:42Z**: \`openshift-console\` downloads pods fail readiness probes (connection refused) — console not yet serving.
- **2026-01-12T13:45:48Z**: \`insights\` cluster operator transitions to \`Available=True\` — operator is healthy.
- **2026-01-12T13:46:02Z**: MCO cannot find \`kube-apiserver-server-ca\` ConfigMap — API server CA not yet propagated, indicating kube-apiserver is still rolling.
- **2026-01-12T13:46:07–13:46:34Z**: Kubelet on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` reports \`openshift-apiserver\` pods (\`apiserver-549f454cb4-*\`) cannot sync due to unmounted \`audit\` volume — context canceled.
- **2026-01-12T13:46:15Z**: MachineConfigPool \`master\` is fully degraded (0/3 nodes ready, 0 updated). Node \`ip-10-0-49-48\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that conflicts with the in-cluster controller-generated config.
- **2026-01-12T13:46:23Z**: gRPC \`addrConn.createTransport\` connection refused errors begin to \`community-operators.openshift-marketplace.svc:50051\` (\`172.30.105.7:50051\`). The \`community-operators\` CatalogSource pod is completely unreachable. Errors repeat with exponential backoff through 13:47:04Z.
- **2026-01-12T13:46:25–13:46:35Z**: Etcd guard readiness probes on \`ip-10-0-122-129\` begin timing out (\`context deadline exceeded\`).
- **2026-01-12T13:46:26Z**: OLM catalog operator logs \`error getting bundle stream\` from \`community-operators\` source — cache refresh fails due to gRPC connection refused.
- **2026-01-12T13:47:30Z**: CVO attempts to apply the new release payload and encounters \`UpdatePayloadResourceInvalid\` on \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD — the CRD spec in the payload is rejected by the API server.
- **2026-01-12T13:48:00Z**: CVO sets \`Failing=True\` condition. ClusterVersion enters \`Partial\` state. Update is effectively blocked.
- **2026-01-12T13:50:22Z**: \`openshift-apiserver\` pod \`apiserver-5f6f94bfc-lxhv9\` readiness probe fails with \`net/http: request canceled while waiting for connection\` — API server not yet serving on \`10.130.0.55:8443\`.
- **2026-01-12T13:50:29Z**: Kube-apiserver startup probe on \`ip-10-0-122-129\` returns HTTP 403 (\`system:anonymous\` cannot get \`/livez\`) — API server is up but RBAC not yet bootstrapped.
- **2026-01-12T13:50:34Z**: OLM operator begins logging \`could not update install status\` for \`packageserver\` CSV with error \`the server is currently unable to handle the request\` — API server is rejecting OLM status update writes. \`queueinformer_operator.go:312\` logs \`Unhandled Error\` for \`sync "openshift-operator-lifecycle-manager/packageserver"\` repeatedly every 5 seconds.
- **2026-01-12T13:51:10Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container (process already gone) — container lifecycle race during static pod replacement.
- **2026-01-12T13:51:23–13:51:34Z**: Kubelet on \`ip-10-0-49-48\` reports etcd-guard and oauth-apiserver readiness probe failures (\`context deadline exceeded\`).
- **2026-01-12T13:52:04Z**: OLM \`packageserver\` sync failures continue; CSV remains stuck in \`Installing\` phase.
- **2026-01-12T13:53:19–13:53:34Z**: Final burst of OLM \`packageserver\` sync failures before the full API server outage begins. This is the last recorded OLM activity before the etcd/API outage window.
- **2026-01-12T13:53:54–13:55:13Z**: Kube-scheduler static pod on \`ip-10-0-122-129\` is killed and replaced; kubelet cannot delete mirror pod or update pod status (API server timeout). Node lease updates begin failing with \`context deadline exceeded\` across all three nodes — **full API server outage window begins**.
- **2026-01-12T13:55:00Z**: Webhook authorizer request fails: \`client rate limiter Wait returned an error: context canceled\`. Post-timeout activity logged for \`GET /apis/packages.operators.coreos.com/v1\` — the \`packageserver\` API endpoint is completely unavailable. This confirms the \`packages.operators.coreos.com\` API group (served by \`packageserver\`) was down during the outage.
- **2026-01-12T13:55:46–13:55:56Z**: Mass pod sync failures across all nodes: \`openshift-controller-manager\`, \`openshift-route-controller-manager\`, \`openshift-apiserver\` pods all report unmounted volumes (\`client-ca\`, \`serving-cert\`) with \`context canceled\` — API server unreachable.
- **2026-01-12T13:55:50Z**: \`kube-storage-version-migrator-operator\` enters CrashLoopBackOff, consistent with API server being unavailable.
- **2026-01-12T13:55:56Z**: \`openshift-apiserver\` and \`openshift-route-controller-manager\` pods report \`EOF\` and \`connection reset by peer\` on readiness probes — API server actively dropping connections.
- **2026-01-12T13:56:14–13:56:23Z**: Second burst of gRPC connection refused errors to \`community-operators\` (\`172.30.105.7:50051\`) — catalog source pod still unreachable ~10 minutes after first failure. \`error getting package stream\` and \`error getting bundle stream\` logged at 13:56:15Z.
- **2026-01-12T13:57:52Z**: CRI-O on \`ip-10-0-122-129\` fails to kill container (process not running).
- **2026-01-12T13:58:20–13:58:25Z**: Etcd guard on \`ip-10-0-122-129\` fails readiness probes repeatedly (\`context deadline exceeded\`).
- **2026-01-12T13:59:48Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container.
- **2026-01-12T13:59:49Z**: Etcd guard on \`ip-10-0-49-48\` reports \`connection refused\` on port 9980 — etcd guard container is down.
- **2026-01-12T14:00:04–14:00:29Z**: Etcd guard on \`ip-10-0-49-48\` continues failing (\`context deadline exceeded\`).
- **2026-01-12T14:00:41Z**: Etcd startup probe on \`ip-10-0-49-48\` fails with HTTP 503 (\`failed to establish etcd client: giving up getting a cached client after 3 tries\`) — **etcd itself is failing to start on the first member**.
- **2026-01-12T14:02:05Z**: CRI-O on \`ip-10-0-97-146\` fails to kill container.
- **2026-01-12T14:02:26Z**: CRI-O on \`ip-10-0-49-48\` stops container (timeout 30s).
- **2026-01-12T14:02:33–14:02:38Z**: Etcd guard on \`ip-10-0-97-146\` fails readiness probes.
- **2026-01-12T14:03:35–14:04:24Z**: CRI-O on \`ip-10-0-97-146\` and \`ip-10-0-122-129\` reports \`Failed to get the status of process\` (PID no longer exists) — containers being cleaned up.
- **2026-01-12T14:04:25Z**: Kube-apiserver guard on \`ip-10-0-122-129\` readiness probe returns HTTP 403 (\`system:anonymous\` cannot get \`/readyz\`) — kube-apiserver is running but anonymous access blocked (expected behavior).
- **2026-01-12T14:05:08Z**: \`ingress-operator\` enters CrashLoopBackOff — ingress stack impacted by API outage.
- **2026-01-12T14:05:16Z**: **etcd gRPC connections begin failing** — \`grpc: addrConn.createTransport failed\` to \`10.0.122.129:2379\` (connection refused). Multiple channels affected simultaneously. This is the etcd outage peak for the first member.
- **2026-01-12T14:05:46Z**: CRI-O on \`ip-10-0-122-129\` fails to kill container.
- **2026-01-12T14:06:41Z**: Etcd startup probe on \`ip-10-0-122-129\` fails HTTP 503 (\`failed to establish etcd client\`) — second etcd member failing to start.
- **2026-01-12T14:07:55Z**: etcd gRPC connection to \`10.0.49.48:2379\` also fails (connection refused) — second etcd endpoint unreachable.
- **2026-01-12T14:08:02Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container.
- **2026-01-12T14:08:44Z**: Etcd guard on \`ip-10-0-49-48\` fails readiness probe.
- **2026-01-12T14:08:52Z**: \`PodNetworkConnectivityCheck\` CRD not found — network connectivity check operator cannot list its CRD (API server resource not yet registered). This error persists across dozens of log files throughout the incident.
- **2026-01-12T14:08:58Z**: Etcd startup probe on \`ip-10-0-49-48\` fails HTTP 503 again.
- **2026-01-12T14:10:18Z**: CRI-O on \`ip-10-0-97-146\` fails to kill container.
- **2026-01-12T14:10:43–14:11:13Z**: Etcd guard on \`ip-10-0-97-146\` fails readiness probes repeatedly.
- **2026-01-12T14:11:13–14:11:14Z**: etcd gRPC connection to \`10.0.97.146:2379\` fails (connection refused) — **all three etcd members have been unreachable at some point**. Etcd startup probe on \`ip-10-0-97-146\` fails HTTP 503.
- **2026-01-12T14:15Z (approx.)**: API server begins recovering; etcd membership stabilizing.
- **2026-01-12T14:22:36–14:22:47Z**: CRI-O on \`ip-10-0-49-48\` and \`ip-10-0-97-146\` reports image blob download failures (unexpected EOF from \`quay.io\`) — image pulls for new release containers are retrying.
- **2026-01-12T14:22:56Z**: Kubelet TLS handshake errors (\`EOF\`) on \`ip-10-0-49-48\` and \`ip-10-0-97-146\` — kubelet serving certificate issues persist.
- **2026-01-12T14:30:10Z**: Kube-apiserver guard on \`ip-10-0-122-129\` readiness probe returns HTTP 403 — kube-apiserver still running but guard probe using anonymous access.
- **2026-01-12T14:34:11Z**: Kube-apiserver guard on \`ip-10-0-49-48\` readiness probe returns HTTP 403.
- **2026-01-12T14:37:47–14:38:09Z**: Kube-apiserver guard on \`ip-10-0-97-146\` fails readiness probes (HTTP 500, then HTTP 500 with \`shutdown failed\`). Kube-apiserver startup probe on \`ip-10-0-97-146\` fails with \`poststarthook/rbac/bootstrap-roles failed\` — RBAC bootstrap still in progress.
- **2026-01-12T14:43:14Z**: Kube-scheduler guard on \`ip-10-0-97-146\` fails readiness probe (connection refused on port 10259) — scheduler not yet running.
- **2026-01-12T15:35:53Z**: etcd gRPC connections to \`10.0.97.146:2379\` and \`10.0.49.48:2379\` fail with \`operation was canceled\` — etcd connectivity issues persist hours after initial outage; etcd membership/leader election still stabilizing.
- **2026-01-12T15:40:56Z**: Kubelet TLS handshake error on \`ip-10-0-122-129\` — certificate issue still unresolved.
- **2026-01-12T16:56:16–16:58:58Z**: CRI-O on multiple nodes fails to kill containers (process not running) — ongoing container lifecycle cleanup from the upgrade.
- **2026-01-12T16:59:24Z**: E2E pod network disruption test pods fail readiness probes (HTTP 503) — test infrastructure detecting network disruption.
- **2026-01-12T16:59:56Z**: CRI-O stops containers on all three nodes simultaneously — likely another static pod replacement wave.
- **2026-01-12T17:02:34Z**: \`sysinfo_rare_error.txt\` logs \`Failed to get global filesystem information: not implemented\` across all nodes — benign cAdvisor/sysinfo limitation, unrelated to upgrade failure.

## Primary Root Cause(s)

1. **Invalid CRD Payload Resource — \`ipaddressclaims.ipam.cluster.x-k8s.io\` (Primary Upgrade Blocker)**
   - The CVO reports \`UpdatePayloadResourceInvalid\` when attempting to apply the CRD \`ipaddressclaims.ipam.cluster.x-k8s.io\` from the \`4.21.0-okd-scos.ec.18\` release payload. The API server rejects the CRD spec — likely due to a structural schema validation failure, a conversion webhook conflict, or an incompatible field change between the ec.13 and ec.18 CRD versions.
   - The CVO cannot proceed past this resource application step and enters a continuous retry loop, keeping the cluster in \`Partial\` / \`Failing=True\` state indefinitely.
   - The broader CRD registration disruption is confirmed by the \`PodNetworkConnectivityCheck\` CRD also being absent (\`controlplane.operator.openshift.io\` API group not found at 14:08:52Z, repeated across dozens of log files), consistent with the API server being unable to serve all registered resource types while rolling.
   - Evidence: \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` shows \`Failing=True\` with reason \`UpdatePayloadResourceInvalid\`; CVO pod logs (\`namespaces/openshift-cluster-version/pods/.../cluster-version-operator/logs/current.log\`) show repeated failed attempts to apply this specific CRD; \`current_rare_error.txt\` confirms \`PodNetworkConnectivityCheck\` CRD not found at 14:08:52Z.

2. **Cluster-Wide API Server and etcd Quorum Loss (~13:53–14:15Z)**
   - All three master nodes lost API server connectivity simultaneously for approximately 20 minutes, confirmed by kubelet node lease update failures (\`context deadline exceeded\`) on all three nodes starting at ~13:53Z.
   - Etcd startup probes failed on all three members with HTTP 503 (\`failed to establish etcd client: giving up getting a cached client after 3 tries\`) at 14:00:41Z (\`ip-10-0-49-48\`), 14:06:41Z (\`ip-10-0-122-129\`), and 14:11:14Z (\`ip-10-0-97-146\`).
   - etcd gRPC transport failures confirmed to all three endpoints: \`10.0.122.129:2379\` (connection refused, 14:05:16Z), \`10.0.49.48:2379\` (connection refused, 14:07:55Z), \`10.0.97.146:2379\` (connection refused, 14:11:13Z) — all three etcd members were unreachable at different points, indicating the cluster lost quorum during the upgrade.
   - The \`openshift-apiserver\` Deployment cycled through at least 8 ReplicaSet revisions (\`apiserver-549f454cb4\`, \`apiserver-5f6f94bfc\`, \`apiserver-5fb49db689\`, \`apiserver-66f8cd4fdb\`, \`apiserver-68858b79fc\`, \`apiserver-76c476bdc6\`, \`apiserver-7bddf9bcb5\`, \`apiserver-9f5b9f9d4\`) with persistent \`FailedMount\`, \`FailedScheduling\`, and readiness probe failures.
   - OLM evidence confirms the API server was already degraded before the full outage: OLM was receiving \`the server is currently unable to handle the request\` errors from 13:50Z — 3 minutes before the documented outage start — indicating the API server entered an overloaded/unavailable state progressively, not instantaneously.
   - etcd gRPC \`operation was canceled\` errors persist at 15:35:53Z — hours after the initial outage — indicating etcd membership and leader election were still stabilizing well into the upgrade window.
   - Evidence: \`current_highfreq_error.txt\` shows gRPC \`addrConn.createTransport failed\` to all three etcd endpoints; \`kubelet_service_rare_error.txt\` shows etcd guard readiness probe failures (\`context deadline exceeded\`) on all three nodes; \`openshift-apiserver/core/events.yaml\` shows all ReplicaSets failing with \`serving-cert\` Secret not found, \`audit-0\` ConfigMap not found, \`FailedScheduling\` (anti-affinity), and readiness probe HTTP 500 with \`[-]shutdown failed: reason withheld\`; OLM \`current_highfreq_error.txt\` shows \`the server is currently unable to handle the request\` from 13:50:34Z.

3. **OLM Subsystem Complete Failure — packageserver CSV Stuck and CatalogSource Unreachable**
   - The \`packageserver\` ClusterServiceVersion was stuck in \`Installing\` phase throughout the upgrade window. OLM could not update its install status because the API server was rejecting writes (\`the server is currently unable to handle the request\`) from 13:50Z onward.
   - The \`community-operators\` CatalogSource pod at \`172.30.105.7:50051\` was completely unreachable via gRPC from 13:46:23Z through at least 13:56:23Z (10+ minutes), causing OLM's catalog cache refresh to fail continuously for both bundle and package streams.
   - All four key OLM operator pods (\`catalog-operator-7c455f5695-gcg6p\`, \`olm-operator-6bd44c8847-5mdrd\`, \`package-server-manager-769cdb658c-kvrck\`, \`collect-profiles-29470425-g9kcc\`) failed scheduling due to untolerated node taints (all nodes tainted during upgrade) and failed to mount required serving-cert Secrets (\`catalog-operator-serving-cert\`, \`olm-operator-serving-cert\`, \`package-server-manager-serving-cert\` — all not found, 7 occurrences each).
   - The \`packages.operators.coreos.com/v1\` API endpoint (served by \`packageserver\`) was completely unavailable at 13:55Z, confirmed by the webhook authorizer timeout with \`context canceled\`.
   - The \`packageserver\` CSV did eventually reach \`InstallSucceeded\` after the API server recovered, confirming this was a transient failure caused by the API outage rather than a permanent OLM bug.
   - Evidence: \`openshift-operator-lifecycle-manager/core/events.yaml\` (\`FailedScheduling\` for all OLM pods; \`FailedMount\` for serving-cert Secrets; \`InstallSucceeded\` and \`InstallWaiting\` for \`packageserver\`); \`current_highfreq_error.txt\` (OLM \`queueinformer_operator.go:312\` errors 13:50–13:53Z); \`current_rare_error.txt\` (gRPC connection refused to \`172.30.105.7:50051\` 13:46–13:56Z; webhook authorizer \`context canceled\` at 13:55Z).

4. **Degraded MachineConfigPool \`master\` — Bootstrap MachineConfig Mismatch on \`ip-10-0-49-48\`**
   - Node \`ip-10-0-49-48.us-east-2.compute.internal\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that does not match the in-cluster controller-generated equivalent. The MachineConfigPool \`master\` shows 0/3 nodes updated and 0/3 nodes ready, blocking the MCO from completing its node configuration rollout.
   - \`kube-rbac-proxy-crio\` on \`ip-10-0-49-48\` is in CrashLoopBackOff from the very start of the upgrade (13:41:46Z), directly related to the MCO's inability to apply the correct MachineConfig.
   - Evidence: \`namespaces/openshift-machine-config-operator/core/events.yaml\` shows \`MachineConfigPool master is degraded\`; \`namespaces/openshift-machine-config-operator/pods/machine-config-controller/.../logs/current.log\` shows the config mismatch on \`ip-10-0-49-48\`; \`kubelet_service_highfreq_error.txt\` shows \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` from 13:41:46Z.

5. **CNI Plugin Initialization Delay — Network Not Ready on All Nodes**
   - All three master nodes started with no CNI configuration in \`/etc/kubernetes/cni/net.d/\` from 13:41:36Z through at least 13:44:22Z (~3 minutes). CRI-O logged \`CNI plugin not yet initialized\` every 30 seconds on all three nodes. Kubelet reported \`NetworkReady=false\` and \`NetworkPluginNotReady\`.
   - This prevented any new pods from starting network interfaces during the critical early upgrade window, compounding the API server recovery delay, contributing to the cascade of pod failures, and directly contributing to the OLM pod scheduling failures.
   - Evidence: \`crio_service_rare_error.txt\` shows continuous CNI warnings from 13:41:36Z to 13:44:22Z on all three nodes; \`kubelet_service_highfreq_error.txt\` shows \`pod_workers.go\` errors for multiple pods with \`network is not ready\`.

6. **Missing Kubelet Server Certificate (\`kubelet-server-current.pem\`)**
   - During the early phase of the upgrade, the kubelet on \`ip-10-0-122-129\` could not initialize its certificate reloader because \`/var/lib/kubelet/pki/kubelet-server-current.pem\` was absent. This caused transient API server connectivity issues and delayed the kube-apiserver static pod rollout.
   - Kubelet TLS handshake errors (\`EOF\`) persist at 14:22:56Z, 15:40:56Z, and 16:58:56Z across all three nodes, indicating the certificate issue was not immediately self-resolved and continued to impact cluster stability throughout the upgrade window.
   - Evidence: \`namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-122-129/.../logs/current.log\` shows \`failed to initialize certificate reloader\` with the missing PEM path; \`kubelet_service_highfreq_error.txt\` shows \`http: TLS handshake error from <node-IP>: EOF\` at multiple timestamps hours after the initial failure.

## Secondary Causes / Contributing Factors

- **\`system:anonymous\` authentication during early boot**: All three kubelets started authenticating as \`system:anonymous\` for the first ~5–10 minutes of the upgrade (13:41:36–~13:42:30Z), indicating the API server's bootstrap RBAC was not yet applied when the kubelets first connected. This caused cascading failures: node registration failed, CSINode publishing failed, lease creation failed, and events could not be posted.
- **\`openshift-apiserver\` anti-affinity scheduling failures**: Multiple \`openshift-apiserver\` ReplicaSets failed scheduling with \`0/3 nodes are available: 3 node(s) didn't match pod anti-affinity rules\`, indicating that during the rolling update, the scheduler could not place new pods while old pods were still terminating on the same nodes.
- **Missing secrets/configmaps for \`openshift-apiserver\`**: The \`serving-cert\` Secret and \`audit-0\` ConfigMap were not found during the early upgrade phase, causing \`FailedMount\` events on multiple \`openshift-apiserver\` pods. These are created by the \`openshift-apiserver-operator\` and their absence indicates the operator itself was delayed.
- **Node Taints Blocking OLM Pod Scheduling**: All three master nodes carried untolerated taints during the upgrade window, causing \`FailedScheduling\` for \`catalog-operator\`, \`olm-operator\`, \`package-server-manager\`, and \`collect-profiles\` pods. This is expected behavior during a master node upgrade but compounded the OLM recovery delay.
- **Missing OLM Serving-Cert Secrets**: \`catalog-operator-serving-cert\`, \`olm-operator-serving-cert\`, and \`package-server-manager-serving-cert\` Secrets were not found during pod startup (7 \`FailedMount\` events each), indicating the service-ca operator had not yet provisioned these Secrets when the pods were first scheduled.
- **Secret Cache Sync Timeout**: \`collect-profiles-29470425-g9kcc\` pod failed with \`MountVolume.SetUp failed for volume "secret-volume": failed to sync secret cache: timed out waiting for the condition\` — confirming the API server's secret informer cache was not synchronized during the outage window.
- **Webhook Authorizer Rate Limiter Exhaustion**: At 13:55:00Z, the webhook authorizer's client rate limiter was exhausted (\`context canceled\`), indicating the API server was under extreme load or had lost connectivity to the webhook backend during the outage transition.
- **\`community-operators\` CatalogSource Pod Restart**: The \`community-operators\` pod at \`172.30.105.7:50051\` was restarted or rescheduled during the upgrade (connection refused from 13:46Z), likely due to the node taint/CNI issues, causing a 10+ minute gap in operator catalog availability.
- **\`packageserver\` APIService Registration Delay**: The \`InstallWaiting: apiServices not installed\` event (count=2) for \`packageserver\` confirms the \`packages.operators.coreos.com\` APIService registration was delayed, consistent with the API server being unable to register aggregated API services during the etcd outage.
- **Storage Version Migrator Failures**: The \`kube-storage-version-migrator\` repeatedly failed to list \`flowcontrol.apiserver.k8s.io\` resources (FlowSchema, PriorityLevelConfiguration) during the upgrade window. The \`kube-storage-version-migrator-operator\` entered CrashLoopBackOff at 13:55:50Z. While expected during API server rollout, this can delay upgrade completion if the migrator is blocked for extended periods.
- **Missing \`kube-apiserver-server-ca\` ConfigMap**: The MCO could not find this ConfigMap during the upgrade, indicating the kube-apiserver CA propagation was delayed. This is a transient condition but contributed to the MCO degradation window.
- **\`ingress-operator\` CrashLoopBackOff**: The ingress operator entered CrashLoopBackOff at ~14:05:08Z, indicating the ingress stack was also impacted by the API outage.
- **Multiple kube-controller-manager installer retries**: Installer pods for the kube-controller-manager (revisions 4, 4-retry-1, 4-retry-2) show repeated retry attempts, indicating the controller manager static pod rollout was unstable during the upgrade.
- **Image pull interruptions from \`quay.io\`**: CRI-O reported multiple \`unexpected EOF\` errors when downloading image blobs from \`quay.io\` (13:43:37Z, 13:46:07Z, 14:22:36–14:22:47Z). These retried successfully but added latency to new pod startup during the upgrade.
- **Stale container references**: Kubelet and CRI-O reported numerous \`container with ID ... not found\` errors as containers from the previous static pod generation were cleaned up. This is expected during static pod replacement but contributed to log noise and minor delays.
- **No Update Channel Configured**: The ClusterVersion shows \`NoChannel\` for the update channel. While not a direct blocker, this means the cluster cannot receive automatic update recommendations or rollback guidance from the Cincinnati update service.
- **cadvisor stats cache misses**: Multiple kubelet \`cadvisor_stats_provider.go\` errors (\`RecentStats: unable to find data in memory cache\`) across all nodes from 13:44:12Z through 16:59:56Z — transient and expected during container churn but indicate high container lifecycle activity throughout the incident.
- **Benign sysinfo Error**: \`Failed to get global filesystem information: not implemented\` in \`sysinfo_rare_error.txt\` at 17:02:34Z across all nodes is a known cAdvisor limitation on certain container runtimes/kernels and has no impact on the upgrade.

## Aggregated Error Patterns

| Pattern | Source | Classification | Significance |
|---------|--------|----------------|--------------|
| \`UpdatePayloadResourceInvalid: unable to apply CRD ipaddressclaims.ipam.cluster.x-k8s.io\` | \`clusterversions.yaml\`, CVO \`current.log\` | **CRITICAL — Primary Blocker** | Prevents upgrade from completing; CVO cannot apply CRD from ec.18 payload; continuous retry loop |
| \`Failing=True, Progressing=True (Partial)\` on ClusterVersion | \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` | **CRITICAL — Upgrade Stalled** | Upgrade is actively failing and stalled in partial state |
| \`grpc: addrConn.createTransport failed\` to all three etcd endpoints (\`:2379\`) | \`current_highfreq_error.txt\` | **CRITICAL — etcd Quorum Loss** | All three etcd members unreachable at different points; cluster lost quorum during upgrade |
| Etcd startup probe HTTP 503 (\`failed to establish etcd client: giving up after 3 tries\`) on all three master nodes | \`kubelet_service_highfreq_error.txt\` | **CRITICAL — etcd Startup Failure** | etcd containers failed to start on all three nodes during static pod replacement |
| Etcd guard readiness probe \`context deadline exceeded\` on all three nodes | \`kubelet_service_rare_error.txt\` | **CRITICAL — etcd Unavailability** | etcd guard confirms etcd not serving on port 9980 across all masters |
| Kubelet node lease update \`context deadline exceeded\` / \`request canceled\` on all three nodes | \`kubelet_service_rare_error.txt\` | **CRITICAL — API Server Outage** | All three nodes unable to update leases for ~20 minutes; API server unreachable |
| \`the server is currently unable to handle the request\` (packageserver CSV sync) | \`current_highfreq_error.txt\` (OLM) | **HIGH — API Server Degradation Indicator** | OLM cannot update packageserver install status; confirms API server overload began at 13:50Z, 3 min before documented outage |
| \`grpc: addrConn.createTransport failed\` to \`172.30.105.7:50051\` (community-operators) | \`current_rare_error.txt\` (OLM catalog) | **HIGH — CatalogSource Unavailable** | community-operators catalog pod unreachable for 10+ minutes; OLM cache refresh fails; operator updates blocked |
| \`sync "openshift-operator-lifecycle-manager/packageserver" failed\` (queueinformer) | \`current_highfreq_error.txt\` | **HIGH — OLM Reconciliation Failure** | packageserver CSV stuck in Installing; OLM operator reconciliation loop failing repeatedly |
| \`error getting bundle stream\` / \`error getting package stream\` (community-operators) | \`current_rare_error.txt\` | **HIGH — Catalog Cache Stale** | OLM catalog cache cannot refresh; operator bundle/package metadata unavailable |
| \`Failed to make webhook authorizer request: context canceled\` | \`current_rare_error.txt\` | **HIGH — Webhook/Auth Failure** | Webhook authorizer exhausted during API outage; packages.operators.coreos.com/v1 endpoint timed out |
| \`FailedScheduling: 0/3 nodes available: untolerated taint(s)\` (OLM pods) | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — OLM Pod Scheduling Blocked** | All OLM operator pods unschedulable during master node upgrade taint window |
| \`FailedMount: secret "catalog-operator-serving-cert" not found\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Missing TLS Secrets** | OLM operator pods cannot start without serving-cert Secrets; cert provisioning lagging |
| \`FailedMount: secret "olm-operator-serving-cert" not found\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Missing TLS Secrets** | OLM operator pod startup blocked |
| \`FailedMount: secret "package-server-manager-serving-cert" not found\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Missing TLS Secrets** | Package server manager pod startup blocked |
| \`failed to sync secret cache: timed out waiting for the condition\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Secret Informer Cache Timeout** | API server informer cache not synchronized during outage; pod volume mount fails |
| \`MachineConfigPool master is degraded\` (0/3 nodes ready, 0/3 updated) | \`namespaces/openshift-machine-config-operator/core/events.yaml\` | **HIGH — MCO Blocker** | Blocks node configuration rollout; bootstrap MachineConfig mismatch on \`ip-10-0-49-48\` |
| \`Node ip-10-0-49-48 has bootstrap-generated MachineConfig mismatch (99-master-generated-registries)\` | \`namespaces/openshift-machine-config-operator/pods/machine-config-controller/.../logs/current.log\` | **HIGH — MCO Config Mismatch** | Specific node stuck with conflicting bootstrap config; prevents MCO rollout |
| \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\`, \`ip-10-0-122-129\` | \`kubelet_service_highfreq_error.txt\` | **HIGH — MCO Component Failure** | MCO proxy sidecar failing from 13:41:46Z; related to MachineConfig mismatch |
| \`ClusterOperator machine-config-operator: Degraded=True\` | \`cluster-scoped-resources/config.openshift.io/clusteroperators.yaml\` | **HIGH — Operator Degraded** | MCO operator itself reporting degraded state during upgrade |
| \`openshift-apiserver\` pods: \`FailedMount\` (\`serving-cert\` Secret, \`audit-0\` ConfigMap not found) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — API Server Rollout Blocked** | Required secrets/configmaps not yet created; 8+ ReplicaSet revisions affected |
| \`openshift-apiserver\` pods: \`FailedScheduling\` (anti-affinity — 0/3 nodes available) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — Scheduling Failure** | Anti-affinity prevents placement while old pods still terminating |
| \`openshift-apiserver\` readiness probe HTTP 500 (\`[-]shutdown failed: reason withheld\`) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — API Server Unhealthy** | Pods in shutdown state still receiving readiness checks; 10+ occurrences per pod |
| \`[-]poststarthook/authorization.openshift.io-bootstrapclusterroles failed\` | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — RBAC Bootstrap Incomplete** | RBAC bootstrap not complete during openshift-apiserver startup |
| \`poststarthook/rbac/bootstrap-roles failed\` on kube-apiserver \`ip-10-0-97-146\` | \`kubelet_service_rare_error.txt\` | **HIGH — RBAC Bootstrap Incomplete** | Kube-apiserver RBAC bootstrap still in progress at 14:37:47Z |
| \`system:anonymous\` forbidden errors (nodes, services, leases, CSI drivers) | \`kubelet_service_rare_error.txt\` | **HIGH — Auth Bootstrap Delay** | Kubelets not yet authenticated; RBAC not bootstrapped at upgrade start |
| \`CNI plugin not yet initialized\` / \`NetworkPluginNotReady\` / \`NetworkReady=false\` | \`crio_service_rare_error.txt\`, \`kubelet_service_highfreq_error.txt\` | **HIGH — Network Initialization Delay** | All three nodes had no CNI config for ~3 minutes; pods could not start networking |
| \`failed to initialize certificate reloader\` (\`kubelet-server-current.pem\` missing) | \`namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-122-129/.../logs/current.log\` | **HIGH — PKI Bootstrap Issue** | Missing kubelet server cert delays kube-apiserver static pod rollout |
| \`http: TLS handshake error from <node-IP>: EOF\` | \`kubelet_service_highfreq_error.txt\` | **HIGH — Kubelet TLS Issue** | Kubelet serving certificate problems persist at 14:22Z, 15:40Z, 16:58Z |
| \`InstallWaiting: apiServices not installed\` (packageserver) | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **MEDIUM — APIService Registration Delay** | packages.operators.coreos.com APIService not registered; aggregated API unavailable |
| \`failed to list flowcontrol.apiserver.k8s.io/v1 FlowSchema: the server could not find the requested resource\` | \`namespaces/openshift-kube-storage-version-migrator/pods/.../logs/current.log\` | **MEDIUM — Storage Migrator Blocked** | Storage migrator blocked on missing API resource during rollout |
| \`kube-storage-version-migrator-operator\` CrashLoopBackOff | \`namespaces/openshift-kube-storage-version-migrator/core/events.yaml\` | **MEDIUM — Operator Failure** | Storage version migrator operator down during API outage |
| \`ingress-operator\` CrashLoopBackOff | \`namespaces/openshift-ingress-operator/core/events.yaml\` | **MEDIUM — Operator Failure** | Ingress operator impacted by API outage |
| \`configmaps "kube-apiserver-server-ca" not found\` | \`namespaces/openshift-machine-config-operator/pods/machine-config-operator/.../logs/current.log\` | **MEDIUM — CA Propagation Delay** | MCO cannot read CA configmap; API server CA propagation delayed |
| \`PodNetworkConnectivityCheck\` CRD not found (\`controlplane.operator.openshift.io\`) | \`current_rare_error.txt\` | **MEDIUM — CRD Registration Delay** | Network connectivity check operator cannot function; CRD not yet registered during API server roll |
| etcd gRPC \`operation was canceled\` at 15:35:53Z | \`current_highfreq_error.txt\` | **MEDIUM — etcd Stabilization** | etcd connections still being canceled hours after outage; etcd not fully stable |
| \`kube-controller-manager installer-4 retry-1, retry-2 failures\` | \`namespaces/openshift-kube-controller-manager/pods/installer-*/logs\` | **MEDIUM — Static Pod Rollout Unstable** | Controller manager static pod rollout required multiple retries |
| CRI-O \`Killing container ... failed: process not running\` | \`crio_service_rare_error.txt\` | **LOW — Container Lifecycle Race** | Containers already exited before SIGKILL; expected during static pod replacement |
| CRI-O \`Error encountered when checking whether cri-o should wipe containers\` (missing \`/var/run/crio/version\`) | \`crio_service_rare_error.txt\` | **LOW — CRI-O Fresh Start** | Normal on first start after reboot/upgrade; all three nodes affected |
| Image blob download \`unexpected EOF\` from \`quay.io\` | \`crio_service_rare_error.txt\` | **LOW — Network Transient** | Image pull retries; resolved automatically but adds latency to pod startup |
| \`cadvisor_stats_provider: RecentStats: unable to find data in memory cache\` | \`kubelet_service_rare_error.txt\` | **LOW — Stats Cache Miss** | Transient during container churn; no operational impact |
| \`Failed to get the status of process with PID ... no such file or directory\` | \`crio_service_rare_error.txt\` | **LOW — Process Cleanup Race** | CRI-O checking PID of already-exited process; benign |
| \`NoChannel\` — no update channel configured on ClusterVersion | \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` | **LOW — Configuration Gap** | Cluster cannot receive automatic update recommendations or rollback guidance |
| \`Failed to get global filesystem information: not implemented\` | \`sysinfo_rare_error.txt\` | **LOW — Benign** | cAdvisor sysinfo limitation on certain container runtimes/kernels; no impact on upgrade |

## Remediation
### 1. [IMMEDIATE — CRITICAL] Resolve the Invalid CRD Payload Resource Blocking the CVO

The \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD in the \`4.21.0-okd-scos.ec.18\` release payload is being rejected by the API server with \`UpdatePayloadResourceInvalid\`. This is the primary upgrade blocker and must be resolved before any other remediation can result in a completed upgrade. Investigate whether the existing CRD has a conflicting schema, conversion webhook, or stored version that is incompatible with the ec.18 payload version.

**Steps:**
\`\`\`bash
# Inspect the current CRD state
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o yaml

# Get the exact CVO failure message for the rejection reason
oc get clusterversion version \\
  -o jsonpath='{.status.conditions[?(@.type=="Failing")].message}'

# Review CVO logs for the specific API server rejection message
oc logs -n openshift-cluster-version \\
  deployment/cluster-version-operator --tail=200 \\
  | grep -i "ipaddressclaim\\|invalid\\|UpdatePayloadResourceInvalid"

# Check for conversion webhooks that may be conflicting
oc get validatingwebhookconfigurations,mutatingwebhookconfigurations \\
  | grep -i "ipam\\|cluster-api\\|capi"

# Check if a stored version mismatch exists
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io \\
  -o jsonpath='{.status.storedVersions}'

# Check if any IPAddressClaim objects exist (must be empty before deletion)
oc get ipaddressclaims --all-namespaces 2>/dev/null | wc -l

# Option A: If no IPAddressClaim objects exist, delete the CRD and allow CVO to recreate it
# WARNING: Only proceed if the count above is 0
oc delete crd ipaddressclaims.ipam.cluster.x-k8s.io

# Option B: If IPAddressClaim objects exist, examine the schema difference
# and manually patch the CRD to match what ec.18 expects
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o json > /tmp/ipaddressclaims-current.json
# Compare with the payload CRD spec and apply the necessary schema changes

# After resolving, force CVO to retry the update
oc patch clusterversion version --type=merge \\
  -p '{"spec":{"desiredUpdate":{"version":"4.21.0-okd-scos.ec.18"}}}'
\`\`\`

**Validation:**
\`\`\`bash
# Confirm CVO is no longer in Failing state
oc get clusterversion version \\
  -o jsonpath='{.status.conditions[?(@.type=="Failing")].status}'
# Expected: False

# Confirm CRD was re-applied and accepted by the API server
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io \\
  -o jsonpath='{.status.conditions[?(@.type=="Established")].status}'
# Expected: True

# Confirm CVO is progressing
oc get clusterversion version -w
# Expected: Progressing=True (healthy progress), Failing=False
\`\`\`

---

### 2. [IMMEDIATE — CRITICAL] Verify etcd Health and Quorum Restoration

All three etcd members were unreachable at different points during the upgrade (14:00–14:11Z), and etcd gRPC cancellation errors persisted as late as 15:35:53Z. Confirm etcd has fully recovered and quorum is stable before proceeding with any further upgrade steps.

**Steps:**
\`\`\`bash
# Check etcd pod status on all master nodes
oc get pods -n openshift-etcd -o wide

# Check etcd cluster health via etcdctl from within the etcd pod
oc -n openshift-etcd exec -it \\
  $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
  -- etcdctl endpoint health \\
  --endpoints=https://10.0.49.48:2379,https://10.0.122.129:2379,https://10.0.97.146:2379 \\
  --cacert=/etc/kubernetes/static-pod-resources/etcd-certs/configmaps/etcd-serving-ca/ca-bundle.crt \\
  --cert=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).crt \\
  --key=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).key

# Check etcd member list to confirm all three members are present and started
oc -n openshift-etcd exec -it \\
  $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
  -- etcdctl member list \\
  --endpoints=https://localhost:2379 \\
  --cacert=/etc/kubernetes/static-pod-resources/etcd-certs/configmaps/etcd-serving-ca/ca-bundle.crt \\
  --cert=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).crt \\
  --key=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).key

# Check etcd cluster operator status
oc get co etcd -o yaml | grep -A 20 "conditions:"

# Check for ongoing gRPC errors in etcd logs
oc logs -n openshift-etcd \\
  $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
  --tail=100 | grep -i "grpc\\|canceled\\|refused\\|error\\|fail\\|panic\\|leader"

# Check etcd guard pods
oc get pods -n openshift-etcd -l app=etcd-guard -o wide
\`\`\`

**Validation:**
\`\`\`bash
# All three endpoints should report "is healthy"
# member list should show 3 members, all "started"
oc get pods -n openshift-etcd
# Expected: all etcd pods Running and Ready (3/3)

# Confirm etcd operator is not degraded
oc get co etcd \\
  -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
# Expected: False

# No recent gRPC transport failures in etcd logs
oc logs -n openshift-etcd -l app=etcd --tail=50 \\
  | grep -i "addrConn\\|connection refused\\|operation was canceled"
# Expected: no recent output
\`\`\`

---

### 3. [IMMEDIATE — CRITICAL] Restore OLM Subsystem and Verify packageserver Recovery

The OLM subsystem was completely non-functional during the upgrade window. The \`packageserver\` CSV was stuck in \`Installing\`, the \`community-operators\` CatalogSource was unreachable via gRPC for 10+ minutes, and all OLM operator pods failed to schedule due to untolerated node taints and missing serving-cert Secrets. While the \`packageserver\` CSV eventually reached \`InstallSucceeded\` after the API server recovered, verify the full OLM stack is healthy and the \`community-operators\` CatalogSource pod has fully recovered before proceeding with the upgrade.

**Steps:**
\`\`\`bash
# Check overall OLM cluster operator health
oc get co operator-lifecycle-manager \\
  operator-lifecycle-manager-catalog \\
  operator-lifecycle-manager-packageserver

# Check packageserver CSV status
oc get csv -n openshift-operator-lifecycle-manager packageserver \\
  -o jsonpath='{.status.phase}'

# Check all OLM pods are running and not in CrashLoopBackOff
oc get pods -n openshift-operator-lifecycle-manager

# Check community-operators CatalogSource pod specifically
oc get pods -n openshift-marketplace | grep community-operators
oc logs -n openshift-marketplace \\
  $(oc get pods -n openshift-marketplace \\
  -l olm.catalogSource=community-operators \\
  -o name | head -1) --tail=50

# Verify the community-operators gRPC endpoint is now reachable
# (172.30.105.7 was the failing IP — check current service IP)
oc get svc -n openshift-marketplace community-operators

# Check if serving-cert Secrets have been provisioned for all OLM components
oc get secret -n openshift-operator-lifecycle-manager \\
  catalog-operator-serving-cert \\
  olm-operator-serving-cert \\
  package-server-manager-serving-cert

# If Secrets are still missing, restart the service-ca operator to re-trigger provisioning
oc rollout restart deployment/service-ca -n openshift-service-ca

# Check for any remaining OLM sync errors
oc logs -n openshift-operator-lifecycle-manager \\
  deployment/olm-operator --tail=100 | grep -i "error\\|failed"
oc logs -n openshift-operator-lifecycle-manager \\
  deployment/catalog-operator --tail=100 | grep -i "error\\|failed"

# Verify the packages.operators.coreos.com API group is available
oc api-resources | grep packages.operators.coreos.com
\`\`\`

**Validation:**
\`\`\`bash
# packageserver CSV should be Succeeded
oc get csv -n openshift-operator-lifecycle-manager packageserver \\
  -o jsonpath='{.status.phase}'
# Expected: Succeeded

# All OLM cluster operators should be Available=True, Degraded=False
oc get co operator-lifecycle-manager \\
  operator-lifecycle-manager-catalog \\
  operator-lifecycle-manager-packageserver \\
  -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="Available")].status,DEGRADED:.status.conditions[?(@.type=="Degraded")].status'
# Expected: all AVAILABLE=True, DEGRADED=False

# community-operators CatalogSource should be READY
oc get catalogsource -n openshift-marketplace community-operators \\
  -o jsonpath='{.status.connectionState.lastObservedState}'
# Expected: READY
\`\`\`

---

### 4. [IMMEDIATE — CRITICAL] Resolve MachineConfigPool \`master\` Degradation

The master MachineConfigPool is degraded with 0/3 nodes updated due to a bootstrap MachineConfig mismatch (\`99-master-generated-registries\`) on \`ip-10-0-49-48\`. This blocks the MCO from completing node configuration and is a prerequisite for the upgrade to proceed. The \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` is a direct symptom of this mismatch.

**Steps:**
\`\`\`bash
# Check current MachineConfigPool status
oc get mcp master -o yaml | grep -A 10 \\
  "degradedMachineCount\\|updatedMachineCount\\|readyMachineCount"

# Identify the mismatched MachineConfig on ip-10-0-49-48
oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/currentConfig}'
oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/desiredConfig}'

# Examine the bootstrap-generated MachineConfig
oc get mc 99-master-generated-registries -o yaml

# Check MCO controller logs for the specific mismatch details
oc logs -n openshift-machine-config-operator \\
  deployment/machine-config-controller --tail=200 \\
  | grep -i "ip-10-0-49-48\\|mismatch\\|bootstrap\\|generated-registries"

# Check kube-rbac-proxy-crio status on the affected nodes
oc get pod -n openshift-machine-config-operator \\
  -l app=kube-rbac-proxy-crio \\
  --field-selector spec.nodeName=ip-10-0-49-48.us-east-2.compute.internal

# Cordon the node before making changes
oc adm cordon ip-10-0-49-48.us-east-2.compute.internal

# Delete the machine-config-daemon pod on that node to force re-sync
oc delete pod -n openshift-machine-config-operator \\
  $(oc get pods -n openshift-machine-config-operator \\
  --field-selector spec.nodeName=ip-10-0-49-48.us-east-2.compute.internal \\
  -l k8s-app=machine-config-daemon -o name)

# Monitor the MCD logs on that node
oc logs -n openshift-machine-config-operator \\
  $(oc get pods -n openshift-machine-config-operator \\
  --field-selector spec.nodeName=ip-10-0-49-48.us-east-2.compute.internal \\
  -l k8s-app=machine-config-daemon -o name) -f

# If MCD re-sync does not resolve the mismatch, force the desired config annotation
DESIRED_MC=$(oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/desiredConfig}')
oc annotate node ip-10-0-49-48.us-east-2.compute.internal \\
  machineconfiguration.openshift.io/currentConfig=\${DESIRED_MC} --overwrite

# Uncordon the node after the MCD has applied the correct config
oc adm uncordon ip-10-0-49-48.us-east-2.compute.internal
\`\`\`

**Validation:**
\`\`\`bash
# MachineConfigPool should show all nodes updated and no degraded nodes
oc get mcp master
# Expected: MACHINECOUNT=3, READYMACHINECOUNT=3, DEGRADEDMACHINECOUNT=0, UPDATEDMACHINECOUNT=3

# Verify kube-rbac-proxy-crio is no longer in CrashLoopBackOff
oc get pod -n openshift-machine-config-operator -l app=kube-rbac-proxy-crio
# Expected: all pods Running

# MCP should no longer be degraded
oc get mcp master \\
  -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
# Expected: False
\`\`\`

---

### 5. [HIGH] Restore Missing Kubelet Server Certificate on All Master Nodes

The kubelet server certificate (\`kubelet-server-current.pem\`) was missing at upgrade start on \`ip-10-0-122-129\`, causing TLS handshake errors that persisted across all three nodes as late as 16:58Z. Pending CSRs must be approved to allow the kubelet to obtain and persist its serving certificate.

**Steps:**
\`\`\`bash
# Check if the certificate now exists on all master nodes
for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
  echo "=== Checking \${node} ==="
  oc debug node/\${node}.us-east-2.compute.internal -- \\
    chroot /host ls -la /var/lib/kubelet/pki/kubelet-server-current.pem 2>/dev/null \\
    || echo "MISSING on \${node}"
done

# Check for pending kubelet serving CSRs
oc get csr | grep -i "kubelet-serving\\|Pending"

# Approve all pending kubelet serving CSRs
oc get csr -o name | xargs oc adm certificate approve

# Check the machine-approver operator status
oc get co machine-approver -o yaml | grep -A 10 "conditions:"

# If the certificate is still missing after CSR approval, restart kubelet on the affected node
oc debug node/ip-10-0-122-129.us-east-2.compute.internal -- \\
  chroot /host systemctl restart kubelet

# Verify kubelet is using the correct certificate after approval on all nodes
for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
  echo "=== \${node} ==="
  oc debug node/\${node}.us-east-2.compute.internal -- \\
    chroot /host openssl x509 \\
      -in /var/lib/kubelet/pki/kubelet-server-current.pem \\
      -noout -dates -subject 2>/dev/null \\
    || echo "Certificate not yet present on \${node}"
done
\`\`\`

**Validation:**
\`\`\`bash
# No pending kubelet serving CSRs
oc get csr | grep Pending
# Expected: no output

# No recent TLS handshake errors in kubelet logs on any master node
for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
  echo "=== \${node} ==="
  oc adm node-logs \${node}.us-east-2.compute.internal \\
    --unit=kubelet --tail=50 | grep "TLS handshake"
done
# Expected


====================================================================================================
LLM API TOKEN & COST SUMMARY — ALL PHASES
====================================================================================================
  Phase 1: Selecting probable/available files   Phase 2: YAML processing   Phase 3: Log processing
  Phase 4: Data aggregation (YAML+LOG)   Phase 5: Root Cause analysis   Phase 6: Cost Calculation

  WITH ML EXTRACTION (current run)
----------------------------------------------------------------------------------------------------
  Phase                              Input tokens  Output tokens     Cost (USD)
  0. Orchestrator ReAct loop              405,752          8,545   $   1.345431
     (8 iterations)
  1. File selection                        39,504          2,649   $   0.158247
  2. YAML processing (local ML)                 —              —              —
  3. Log processing (local Drain3)              —              —              —
  4. Data aggregation (no LLM)                  —              —              —
  5. RCA (extracted payload)              261,917         57,649   $   1.650486
  Total                                                            $   3.154164

  COMPRESSION RATIO (input size vs payload sent to LLM)
----------------------------------------------------------------------------------------------------
  Total input (YAML + logs): 59,820,303 bytes  →  Payload to LLM: 176,012 bytes
  Overall compression ratio: 339.86x

  File                                               Type     Original (B)    Payload (B)      Ratio
----------------------------------------------------------------------------------------------------
  clusteroperators.yaml                              yaml          183,895          2,339      78.62x
  clusterversions.yaml                               yaml            6,985          2,913       2.40x
  events.yaml                                        yaml          260,248         12,080      21.54x
  current_rare_error.txt                             log        10,684,428          2,174    4914.64x
  current_highfreq_error.txt                         log        10,684,428          4,305    2481.86x
  previous_rare_error.txt                            log           204,917            273     750.61x
  current_rare_error.txt                             log           610,129          2,174     280.65x
  current_highfreq_error.txt                         log           610,129          4,305     141.73x
  current_rare_error.txt                             log           130,236          2,174      59.91x
  current_highfreq_error.txt                         log           130,236          4,305      30.25x
  current_rare_error.txt                             log           120,696          2,174      55.52x
  current_highfreq_error.txt                         log           120,696          4,305      28.04x
  current_rare_error.txt                             log         1,982,058          2,174     911.71x
  current_highfreq_error.txt                         log         1,982,058          4,305     460.41x
  current_rare_error.txt                             log         2,172,914          2,174     999.50x
  current_highfreq_error.txt                         log         2,172,914          4,305     504.74x
  current_rare_error.txt                             log         1,960,285          2,174     901.70x
  current_highfreq_error.txt                         log         1,960,285          4,305     455.35x
  current_rare_error.txt                             log           135,300          2,174      62.24x
  current_highfreq_error.txt                         log           135,300          4,305      31.43x
  current_rare_error.txt                             log            27,435          2,174      12.62x
  current_rare_error.txt                             log            45,571          2,174      20.96x
  current_rare_error.txt                             log            27,058          2,174      12.45x
  current_rare_error.txt                             log            46,188          2,174      21.25x
  current_rare_error.txt                             log            47,420          2,174      21.81x
  current_rare_error.txt                             log            29,403          2,174      13.52x
  current_rare_error.txt                             log            52,620          2,174      24.20x
  current_highfreq_error.txt                         log            52,620          4,305      12.22x
  previous_rare_error.txt                            log             1,408            273       5.16x
  previous_rare_error.txt                            log             1,240            273       4.54x
  previous_rare_error.txt                            log             1,240            273       4.54x
  current_rare_error.txt                             log             5,399          2,174       2.48x
  current_rare_error.txt                             log           115,946            387     299.60x
  current_highfreq_error.txt                         log           115,946          3,067      37.80x
  current_rare_error.txt                             log            23,352            387      60.34x
  current_rare_error.txt                             log            22,986            387      59.40x
  current_rare_error.txt                             log            22,986            387      59.40x
  current_rare_error.txt                             log            57,526            387     148.65x
  current_rare_error.txt                             log            57,512            387     148.61x
  current_rare_error.txt                             log            57,519            387     148.63x
  current_rare_error.txt                             log            57,933            387     149.70x
  current_rare_error.txt                             log            57,919            387     149.66x
  current_rare_error.txt                             log            57,926            387     149.68x
  current_rare_error.txt                             log            58,166            387     150.30x
  current_rare_error.txt                             log            58,152            387     150.26x
  current_rare_error.txt                             log            58,159            387     150.28x
  current_rare_error.txt                             log            58,532            387     151.25x
  current_rare_error.txt                             log            58,152            387     150.26x
  current_rare_error.txt                             log            58,159            387     150.28x
  current_rare_error.txt                             log           195,197            387     504.39x
  current_highfreq_error.txt                         log           195,197          3,067      63.64x
  current_rare_error.txt                             log           894,978            387    2312.60x
  current_highfreq_error.txt                         log           894,978          3,067     291.81x
  current_rare_error.txt                             log             2,530            387       6.54x
  current_rare_error.txt                             log            16,605            387      42.91x
  current_rare_error.txt                             log            63,343            387     163.68x
  current_rare_error.txt                             log             2,528            387       6.53x
  current_rare_error.txt                             log            13,805            387      35.67x
  current_rare_error.txt                             log            66,081            387     170.75x
  current_rare_error.txt                             log            12,656            387      32.70x
  current_rare_error.txt                             log            34,972            387      90.37x
  current_rare_error.txt                             log            34,775            387      89.86x
  current_rare_error.txt                             log            34,955            387      90.32x
  current_rare_error.txt                             log            34,941            387      90.29x
  current_rare_error.txt                             log            34,782            387      89.88x
  current_rare_error.txt                             log            35,178            387      90.90x
  current_rare_error.txt                             log            35,164            387      90.86x
  current_rare_error.txt                             log            35,171            387      90.88x
  current_rare_error.txt                             log            35,178            387      90.90x
  current_rare_error.txt                             log            35,164            387      90.86x
  current_rare_error.txt                             log            35,171            387      90.88x
  current_rare_error.txt                             log            73,217            387     189.19x
  current_rare_error.txt                             log             4,434            387      11.46x
  current_rare_error.txt                             log            62,123            387     160.52x
  current_rare_error.txt                             log             2,504            387       6.47x
  current_rare_error.txt                             log            62,122            387     160.52x
  current_rare_error.txt                             log             2,504            387       6.47x
  current_rare_error.txt                             log           250,941            387     648.43x
  current_highfreq_error.txt                         log           250,941          3,067      81.82x
  current_rare_error.txt                             log            15,671            387      40.49x
  current_rare_error.txt                             log           176,747            387     456.71x
  current_highfreq_error.txt                         log           176,747          3,067      57.63x
  current_rare_error.txt                             log            15,673            387      40.50x
  current_rare_error.txt                             log           261,134            387     674.76x
  current_highfreq_error.txt                         log           261,134          3,067      85.14x
  current_rare_error.txt                             log            15,673            387      40.50x
  kubelet_service_rare_error.txt                     log        12,137,055         81,906     148.18x
  kubelet_service_highfreq_error.txt                 log        12,137,055         76,466     158.72x
  crio_service_rare_error.txt                        log         5,341,176         18,676     285.99x
  crio_service_highfreq_error.txt                    log         5,341,176          2,035    2624.66x
  current_highfreq_error.txt                         log           223,600          3,647      61.31x
  current_rare_error.txt                             log             3,086          6,880       0.45x
  current_highfreq_error.txt                         log           199,600          3,647      54.73x
  current_rare_error.txt                             log           527,220          6,880      76.63x
  current_rare_error.txt                             log             2,059          6,880       0.30x
  current_rare_error.txt                             log             2,059          6,880       0.30x
  current_rare_error.txt                             log             2,984          6,880       0.43x
  current_highfreq_error.txt                         log           390,400          3,647     107.05x
  current_rare_error.txt                             log             2,529          6,880       0.37x
  current_highfreq_error.txt                         log           669,041          3,647     183.45x
  current_rare_error.txt                             log             1,040          6,880       0.15x
  current_rare_error.txt                             log             1,039          6,880       0.15x
  current_highfreq_error.txt                         log            65,600          3,647      17.99x
  current_rare_error.txt                             log             5,432          6,880       0.79x
  current_highfreq_error.txt                         log           243,200          3,647      66.68x
  current_rare_error.txt                             log             5,431          6,880       0.79x
  sysinfo_rare_error.txt                             log           404,602            103    3928.17x
  sysinfo_rare_error.txt                             log           404,602            103    3928.17x
  sysinfo_rare_error.txt                             log           404,602            103    3928.17x
  sysinfo_rare_error.txt                             log           413,310            103    4012.72x
  sysinfo_rare_error.txt                             log           413,310            103    4012.72x
  sysinfo_rare_error.txt                             log           404,602            103    3928.17x
  current_rare_error.txt                             log         1,456,216          6,880     211.66x
  current_highfreq_error.txt                         log         1,456,216          3,647     399.29x
  current_rare_error.txt                             log               414          6,880       0.06x
  current_rare_error.txt                             log               414          6,880       0.06x
  current_rare_error.txt                             log               414          6,880       0.06x
  current_rare_error.txt                             log         8,619,192          6,880    1252.79x
  current_highfreq_error.txt                         log         8,619,192          3,647    2363.36x
  current_rare_error.txt                             log            14,997          6,880       2.18x
  current_rare_error.txt                             log            24,227          6,880       3.52x
  current_rare_error.txt                             log            23,613          6,880       3.43x

====================================================================================================
`;

export const mockRCASummary = `User Reported Issue:
  Test cases is failing during openshift updating process.

================================================================================

## User Reported Issue

Test cases are failing during the OpenShift cluster update process. The cluster is attempting to upgrade from OKD/SCOS version \`4.21.0-okd-scos.ec.13\` to \`4.21.0-okd-scos.ec.18\` and has stalled in a \`Partial\` / \`Failing\` state, blocking test suite completion.

## Executive Summary

The cluster update from OKD/SCOS \`4.21.0-okd-scos.ec.13\` → \`4.21.0-okd-scos.ec.18\` is blocked by multiple compounding failures across six distinct failure domains. **The primary cause of the user's problem is:** the Cluster Version Operator (CVO) cannot apply an invalid CRD payload resource (\`ipaddressclaims.ipam.cluster.x-k8s.io\`), triggering \`UpdatePayloadResourceInvalid\` and entering a continuous retry loop that prevents the upgrade from advancing. This primary blocker is compounded by: (1) a cluster-wide API server and etcd quorum loss lasting approximately 20–30 minutes (13:53–14:15Z) during which all three etcd members were simultaneously unreachable and the \`openshift-apiserver\` Deployment cycled through at least 8 ReplicaSet revisions; (2) a fully degraded MachineConfigPool \`master\` (0/3 nodes ready) due to a bootstrap MachineConfig mismatch on node \`ip-10-0-49-48\`; (3) a complete OLM subsystem failure where the \`packageserver\` CSV was stuck in \`Installing\`, the \`community-operators\` CatalogSource was unreachable via gRPC for 10+ minutes, and all OLM operator pods failed scheduling due to untolerated node taints and missing serving-cert Secrets; (4) CNI plugin initialization delay across all three master nodes during the critical early upgrade window; and (5) a missing kubelet server certificate (\`kubelet-server-current.pem\`) causing persistent TLS handshake errors across all nodes for hours. Together, these failures caused the update to stall in a \`Partial\` state with \`Failing=True\`.

## Chronology of Events

- **2026-01-12T13:41:16Z**: Cluster update from \`4.21.0-okd-scos.ec.13\` begins (\`history[1].startedTime\`). Update channel is unconfigured (\`NoChannel\`).
- **2026-01-12T13:41:36Z**: CRI-O starts on all three master nodes (\`ip-10-0-49-48\`, \`ip-10-0-122-129\`, \`ip-10-0-97-146\`) — all report missing \`/var/run/crio/version\` (fresh start) and CNI plugin uninitialized. Kubelet on all nodes immediately begins failing with \`system:anonymous\` forbidden errors — nodes not yet registered with API server.
- **2026-01-12T13:41:36–13:42:22Z**: All three master kubelets report \`NetworkReady=false\` (no CNI config in \`/etc/kubernetes/cni/net.d/\`). Kubelet cannot list nodes, services, CSI drivers, or leases — API server is not yet authenticating node credentials. CSINode publishing fails. Eviction manager cannot find node info.
- **2026-01-12T13:41:46–13:41:52Z**: Kubelet on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` reports \`kube-rbac-proxy-crio\` in CrashLoopBackOff in \`openshift-machine-config-operator\` — events rejected because \`system:anonymous\` cannot create events.
- **2026-01-12T13:41:48Z**: Kubelet certificate reloader fails (\`kubelet-server-current.pem\` missing) on \`ip-10-0-122-129\`. CSINode annotation update times out on all nodes.
- **2026-01-12T13:41:53Z**: \`openshift-apiserver\` pods (\`apiserver-549f454cb4-*\`) begin failing to mount \`audit-0\` ConfigMap and \`serving-cert\` Secret — these resources do not yet exist.
- **2026-01-12T13:42:06–13:44:22Z**: CNI plugin remains uninitialized across all three nodes. CRI-O logs persistent \`CNI plugin not yet initialized\` warnings every ~30 seconds. Pods in \`openshift-network-diagnostics\`, \`openshift-multus\`, \`openshift-e2e-loki\` cannot start due to \`NetworkPluginNotReady\`.
- **2026-01-12T13:43:07–13:43:08Z**: CRI-O on \`ip-10-0-49-48\` stops containers from previous revision; kubelet reports container IDs not found in CRI-O index (stale container references from prior static pod generation).
- **2026-01-12T13:43:36–13:44:22Z**: Kubelet on all nodes reports \`Container runtime network not ready\` — CNI still absent.
- **2026-01-12T13:44:57Z**: Kubelet on \`ip-10-0-122-129\` reports \`cluster-version-operator\` pod cannot mount \`serving-cert\` volume — CVO itself is impacted by the missing secrets.
- **2026-01-12T13:45:07Z**: \`kube-storage-version-migrator\` begins failing to list \`flowcontrol.apiserver.k8s.io/v1\` resources (FlowSchema, PriorityLevelConfiguration) — API server does not yet expose these resources at the expected version during the upgrade transition.
- **2026-01-12T13:45:41–13:45:42Z**: \`openshift-console\` downloads pods fail readiness probes (connection refused) — console not yet serving.
- **2026-01-12T13:45:48Z**: \`insights\` cluster operator transitions to \`Available=True\` — operator is healthy.
- **2026-01-12T13:46:02Z**: MCO cannot find \`kube-apiserver-server-ca\` ConfigMap — API server CA not yet propagated, indicating kube-apiserver is still rolling.
- **2026-01-12T13:46:07–13:46:34Z**: Kubelet on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` reports \`openshift-apiserver\` pods (\`apiserver-549f454cb4-*\`) cannot sync due to unmounted \`audit\` volume — context canceled.
- **2026-01-12T13:46:15Z**: MachineConfigPool \`master\` is fully degraded (0/3 nodes ready, 0 updated). Node \`ip-10-0-49-48\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that conflicts with the in-cluster controller-generated config.
- **2026-01-12T13:46:23Z**: gRPC \`addrConn.createTransport\` connection refused errors begin to \`community-operators.openshift-marketplace.svc:50051\` (\`172.30.105.7:50051\`). The \`community-operators\` CatalogSource pod is completely unreachable. Errors repeat with exponential backoff through 13:47:04Z.
- **2026-01-12T13:46:25–13:46:35Z**: Etcd guard readiness probes on \`ip-10-0-122-129\` begin timing out (\`context deadline exceeded\`).
- **2026-01-12T13:46:26Z**: OLM catalog operator logs \`error getting bundle stream\` from \`community-operators\` source — cache refresh fails due to gRPC connection refused.
- **2026-01-12T13:47:30Z**: CVO attempts to apply the new release payload and encounters \`UpdatePayloadResourceInvalid\` on \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD — the CRD spec in the payload is rejected by the API server.
- **2026-01-12T13:48:00Z**: CVO sets \`Failing=True\` condition. ClusterVersion enters \`Partial\` state. Update is effectively blocked.
- **2026-01-12T13:50:22Z**: \`openshift-apiserver\` pod \`apiserver-5f6f94bfc-lxhv9\` readiness probe fails with \`net/http: request canceled while waiting for connection\` — API server not yet serving on \`10.130.0.55:8443\`.
- **2026-01-12T13:50:29Z**: Kube-apiserver startup probe on \`ip-10-0-122-129\` returns HTTP 403 (\`system:anonymous\` cannot get \`/livez\`) — API server is up but RBAC not yet bootstrapped.
- **2026-01-12T13:50:34Z**: OLM operator begins logging \`could not update install status\` for \`packageserver\` CSV with error \`the server is currently unable to handle the request\` — API server is rejecting OLM status update writes. \`queueinformer_operator.go:312\` logs \`Unhandled Error\` for \`sync "openshift-operator-lifecycle-manager/packageserver"\` repeatedly every 5 seconds.
- **2026-01-12T13:51:10Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container (process already gone) — container lifecycle race during static pod replacement.
- **2026-01-12T13:51:23–13:51:34Z**: Kubelet on \`ip-10-0-49-48\` reports etcd-guard and oauth-apiserver readiness probe failures (\`context deadline exceeded\`).
- **2026-01-12T13:52:04Z**: OLM \`packageserver\` sync failures continue; CSV remains stuck in \`Installing\` phase.
- **2026-01-12T13:53:19–13:53:34Z**: Final burst of OLM \`packageserver\` sync failures before the full API server outage begins. This is the last recorded OLM activity before the etcd/API outage window.
- **2026-01-12T13:53:54–13:55:13Z**: Kube-scheduler static pod on \`ip-10-0-122-129\` is killed and replaced; kubelet cannot delete mirror pod or update pod status (API server timeout). Node lease updates begin failing with \`context deadline exceeded\` across all three nodes — **full API server outage window begins**.
- **2026-01-12T13:55:00Z**: Webhook authorizer request fails: \`client rate limiter Wait returned an error: context canceled\`. Post-timeout activity logged for \`GET /apis/packages.operators.coreos.com/v1\` — the \`packageserver\` API endpoint is completely unavailable. This confirms the \`packages.operators.coreos.com\` API group (served by \`packageserver\`) was down during the outage.
- **2026-01-12T13:55:46–13:55:56Z**: Mass pod sync failures across all nodes: \`openshift-controller-manager\`, \`openshift-route-controller-manager\`, \`openshift-apiserver\` pods all report unmounted volumes (\`client-ca\`, \`serving-cert\`) with \`context canceled\` — API server unreachable.
- **2026-01-12T13:55:50Z**: \`kube-storage-version-migrator-operator\` enters CrashLoopBackOff, consistent with API server being unavailable.
- **2026-01-12T13:55:56Z**: \`openshift-apiserver\` and \`openshift-route-controller-manager\` pods report \`EOF\` and \`connection reset by peer\` on readiness probes — API server actively dropping connections.
- **2026-01-12T13:56:14–13:56:23Z**: Second burst of gRPC connection refused errors to \`community-operators\` (\`172.30.105.7:50051\`) — catalog source pod still unreachable ~10 minutes after first failure. \`error getting package stream\` and \`error getting bundle stream\` logged at 13:56:15Z.
- **2026-01-12T13:57:52Z**: CRI-O on \`ip-10-0-122-129\` fails to kill container (process not running).
- **2026-01-12T13:58:20–13:58:25Z**: Etcd guard on \`ip-10-0-122-129\` fails readiness probes repeatedly (\`context deadline exceeded\`).
- **2026-01-12T13:59:48Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container.
- **2026-01-12T13:59:49Z**: Etcd guard on \`ip-10-0-49-48\` reports \`connection refused\` on port 9980 — etcd guard container is down.
- **2026-01-12T14:00:04–14:00:29Z**: Etcd guard on \`ip-10-0-49-48\` continues failing (\`context deadline exceeded\`).
- **2026-01-12T14:00:41Z**: Etcd startup probe on \`ip-10-0-49-48\` fails with HTTP 503 (\`failed to establish etcd client: giving up getting a cached client after 3 tries\`) — **etcd itself is failing to start on the first member**.
- **2026-01-12T14:02:05Z**: CRI-O on \`ip-10-0-97-146\` fails to kill container.
- **2026-01-12T14:02:26Z**: CRI-O on \`ip-10-0-49-48\` stops container (timeout 30s).
- **2026-01-12T14:02:33–14:02:38Z**: Etcd guard on \`ip-10-0-97-146\` fails readiness probes.
- **2026-01-12T14:03:35–14:04:24Z**: CRI-O on \`ip-10-0-97-146\` and \`ip-10-0-122-129\` reports \`Failed to get the status of process\` (PID no longer exists) — containers being cleaned up.
- **2026-01-12T14:04:25Z**: Kube-apiserver guard on \`ip-10-0-122-129\` readiness probe returns HTTP 403 (\`system:anonymous\` cannot get \`/readyz\`) — kube-apiserver is running but anonymous access blocked (expected behavior).
- **2026-01-12T14:05:08Z**: \`ingress-operator\` enters CrashLoopBackOff — ingress stack impacted by API outage.
- **2026-01-12T14:05:16Z**: **etcd gRPC connections begin failing** — \`grpc: addrConn.createTransport failed\` to \`10.0.122.129:2379\` (connection refused). Multiple channels affected simultaneously. This is the etcd outage peak for the first member.
- **2026-01-12T14:05:46Z**: CRI-O on \`ip-10-0-122-129\` fails to kill container.
- **2026-01-12T14:06:41Z**: Etcd startup probe on \`ip-10-0-122-129\` fails HTTP 503 (\`failed to establish etcd client\`) — second etcd member failing to start.
- **2026-01-12T14:07:55Z**: etcd gRPC connection to \`10.0.49.48:2379\` also fails (connection refused) — second etcd endpoint unreachable.
- **2026-01-12T14:08:02Z**: CRI-O on \`ip-10-0-49-48\` fails to kill container.
- **2026-01-12T14:08:44Z**: Etcd guard on \`ip-10-0-49-48\` fails readiness probe.
- **2026-01-12T14:08:52Z**: \`PodNetworkConnectivityCheck\` CRD not found — network connectivity check operator cannot list its CRD (API server resource not yet registered). This error persists across dozens of log files throughout the incident.
- **2026-01-12T14:08:58Z**: Etcd startup probe on \`ip-10-0-49-48\` fails HTTP 503 again.
- **2026-01-12T14:10:18Z**: CRI-O on \`ip-10-0-97-146\` fails to kill container.
- **2026-01-12T14:10:43–14:11:13Z**: Etcd guard on \`ip-10-0-97-146\` fails readiness probes repeatedly.
- **2026-01-12T14:11:13–14:11:14Z**: etcd gRPC connection to \`10.0.97.146:2379\` fails (connection refused) — **all three etcd members have been unreachable at some point**. Etcd startup probe on \`ip-10-0-97-146\` fails HTTP 503.
- **2026-01-12T14:15Z (approx.)**: API server begins recovering; etcd membership stabilizing.
- **2026-01-12T14:22:36–14:22:47Z**: CRI-O on \`ip-10-0-49-48\` and \`ip-10-0-97-146\` reports image blob download failures (unexpected EOF from \`quay.io\`) — image pulls for new release containers are retrying.
- **2026-01-12T14:22:56Z**: Kubelet TLS handshake errors (\`EOF\`) on \`ip-10-0-49-48\` and \`ip-10-0-97-146\` — kubelet serving certificate issues persist.
- **2026-01-12T14:30:10Z**: Kube-apiserver guard on \`ip-10-0-122-129\` readiness probe returns HTTP 403 — kube-apiserver still running but guard probe using anonymous access.
- **2026-01-12T14:34:11Z**: Kube-apiserver guard on \`ip-10-0-49-48\` readiness probe returns HTTP 403.
- **2026-01-12T14:37:47–14:38:09Z**: Kube-apiserver guard on \`ip-10-0-97-146\` fails readiness probes (HTTP 500, then HTTP 500 with \`shutdown failed\`). Kube-apiserver startup probe on \`ip-10-0-97-146\` fails with \`poststarthook/rbac/bootstrap-roles failed\` — RBAC bootstrap still in progress.
- **2026-01-12T14:43:14Z**: Kube-scheduler guard on \`ip-10-0-97-146\` fails readiness probe (connection refused on port 10259) — scheduler not yet running.
- **2026-01-12T15:35:53Z**: etcd gRPC connections to \`10.0.97.146:2379\` and \`10.0.49.48:2379\` fail with \`operation was canceled\` — etcd connectivity issues persist hours after initial outage; etcd membership/leader election still stabilizing.
- **2026-01-12T15:40:56Z**: Kubelet TLS handshake error on \`ip-10-0-122-129\` — certificate issue still unresolved.
- **2026-01-12T16:56:16–16:58:58Z**: CRI-O on multiple nodes fails to kill containers (process not running) — ongoing container lifecycle cleanup from the upgrade.
- **2026-01-12T16:59:24Z**: E2E pod network disruption test pods fail readiness probes (HTTP 503) — test infrastructure detecting network disruption.
- **2026-01-12T16:59:56Z**: CRI-O stops containers on all three nodes simultaneously — likely another static pod replacement wave.
- **2026-01-12T17:02:34Z**: \`sysinfo_rare_error.txt\` logs \`Failed to get global filesystem information: not implemented\` across all nodes — benign cAdvisor/sysinfo limitation, unrelated to upgrade failure.

## Primary Root Cause(s)

1. **Invalid CRD Payload Resource — \`ipaddressclaims.ipam.cluster.x-k8s.io\` (Primary Upgrade Blocker)**
   - The CVO reports \`UpdatePayloadResourceInvalid\` when attempting to apply the CRD \`ipaddressclaims.ipam.cluster.x-k8s.io\` from the \`4.21.0-okd-scos.ec.18\` release payload. The API server rejects the CRD spec — likely due to a structural schema validation failure, a conversion webhook conflict, or an incompatible field change between the ec.13 and ec.18 CRD versions.
   - The CVO cannot proceed past this resource application step and enters a continuous retry loop, keeping the cluster in \`Partial\` / \`Failing=True\` state indefinitely.
   - The broader CRD registration disruption is confirmed by the \`PodNetworkConnectivityCheck\` CRD also being absent (\`controlplane.operator.openshift.io\` API group not found at 14:08:52Z, repeated across dozens of log files), consistent with the API server being unable to serve all registered resource types while rolling.
   - Evidence: \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` shows \`Failing=True\` with reason \`UpdatePayloadResourceInvalid\`; CVO pod logs (\`namespaces/openshift-cluster-version/pods/.../cluster-version-operator/logs/current.log\`) show repeated failed attempts to apply this specific CRD; \`current_rare_error.txt\` confirms \`PodNetworkConnectivityCheck\` CRD not found at 14:08:52Z.

2. **Cluster-Wide API Server and etcd Quorum Loss (~13:53–14:15Z)**
   - All three master nodes lost API server connectivity simultaneously for approximately 20 minutes, confirmed by kubelet node lease update failures (\`context deadline exceeded\`) on all three nodes starting at ~13:53Z.
   - Etcd startup probes failed on all three members with HTTP 503 (\`failed to establish etcd client: giving up getting a cached client after 3 tries\`) at 14:00:41Z (\`ip-10-0-49-48\`), 14:06:41Z (\`ip-10-0-122-129\`), and 14:11:14Z (\`ip-10-0-97-146\`).
   - etcd gRPC transport failures confirmed to all three endpoints: \`10.0.122.129:2379\` (connection refused, 14:05:16Z), \`10.0.49.48:2379\` (connection refused, 14:07:55Z), \`10.0.97.146:2379\` (connection refused, 14:11:13Z) — all three etcd members were unreachable at different points, indicating the cluster lost quorum during the upgrade.
   - The \`openshift-apiserver\` Deployment cycled through at least 8 ReplicaSet revisions (\`apiserver-549f454cb4\`, \`apiserver-5f6f94bfc\`, \`apiserver-5fb49db689\`, \`apiserver-66f8cd4fdb\`, \`apiserver-68858b79fc\`, \`apiserver-76c476bdc6\`, \`apiserver-7bddf9bcb5\`, \`apiserver-9f5b9f9d4\`) with persistent \`FailedMount\`, \`FailedScheduling\`, and readiness probe failures.
   - OLM evidence confirms the API server was already degraded before the full outage: OLM was receiving \`the server is currently unable to handle the request\` errors from 13:50Z — 3 minutes before the documented outage start — indicating the API server entered an overloaded/unavailable state progressively, not instantaneously.
   - etcd gRPC \`operation was canceled\` errors persist at 15:35:53Z — hours after the initial outage — indicating etcd membership and leader election were still stabilizing well into the upgrade window.
   - Evidence: \`current_highfreq_error.txt\` shows gRPC \`addrConn.createTransport failed\` to all three etcd endpoints; \`kubelet_service_rare_error.txt\` shows etcd guard readiness probe failures (\`context deadline exceeded\`) on all three nodes; \`openshift-apiserver/core/events.yaml\` shows all ReplicaSets failing with \`serving-cert\` Secret not found, \`audit-0\` ConfigMap not found, \`FailedScheduling\` (anti-affinity), and readiness probe HTTP 500 with \`[-]shutdown failed: reason withheld\`; OLM \`current_highfreq_error.txt\` shows \`the server is currently unable to handle the request\` from 13:50:34Z.

3. **OLM Subsystem Complete Failure — packageserver CSV Stuck and CatalogSource Unreachable**
   - The \`packageserver\` ClusterServiceVersion was stuck in \`Installing\` phase throughout the upgrade window. OLM could not update its install status because the API server was rejecting writes (\`the server is currently unable to handle the request\`) from 13:50Z onward.
   - The \`community-operators\` CatalogSource pod at \`172.30.105.7:50051\` was completely unreachable via gRPC from 13:46:23Z through at least 13:56:23Z (10+ minutes), causing OLM's catalog cache refresh to fail continuously for both bundle and package streams.
   - All four key OLM operator pods (\`catalog-operator-7c455f5695-gcg6p\`, \`olm-operator-6bd44c8847-5mdrd\`, \`package-server-manager-769cdb658c-kvrck\`, \`collect-profiles-29470425-g9kcc\`) failed scheduling due to untolerated node taints (all nodes tainted during upgrade) and failed to mount required serving-cert Secrets (\`catalog-operator-serving-cert\`, \`olm-operator-serving-cert\`, \`package-server-manager-serving-cert\` — all not found, 7 occurrences each).
   - The \`packages.operators.coreos.com/v1\` API endpoint (served by \`packageserver\`) was completely unavailable at 13:55Z, confirmed by the webhook authorizer timeout with \`context canceled\`.
   - The \`packageserver\` CSV did eventually reach \`InstallSucceeded\` after the API server recovered, confirming this was a transient failure caused by the API outage rather than a permanent OLM bug.
   - Evidence: \`openshift-operator-lifecycle-manager/core/events.yaml\` (\`FailedScheduling\` for all OLM pods; \`FailedMount\` for serving-cert Secrets; \`InstallSucceeded\` and \`InstallWaiting\` for \`packageserver\`); \`current_highfreq_error.txt\` (OLM \`queueinformer_operator.go:312\` errors 13:50–13:53Z); \`current_rare_error.txt\` (gRPC connection refused to \`172.30.105.7:50051\` 13:46–13:56Z; webhook authorizer \`context canceled\` at 13:55Z).

4. **Degraded MachineConfigPool \`master\` — Bootstrap MachineConfig Mismatch on \`ip-10-0-49-48\`**
   - Node \`ip-10-0-49-48.us-east-2.compute.internal\` is stuck with a bootstrap-generated MachineConfig (\`99-master-generated-registries\`) that does not match the in-cluster controller-generated equivalent. The MachineConfigPool \`master\` shows 0/3 nodes updated and 0/3 nodes ready, blocking the MCO from completing its node configuration rollout.
   - \`kube-rbac-proxy-crio\` on \`ip-10-0-49-48\` is in CrashLoopBackOff from the very start of the upgrade (13:41:46Z), directly related to the MCO's inability to apply the correct MachineConfig.
   - Evidence: \`namespaces/openshift-machine-config-operator/core/events.yaml\` shows \`MachineConfigPool master is degraded\`; \`namespaces/openshift-machine-config-operator/pods/machine-config-controller/.../logs/current.log\` shows the config mismatch on \`ip-10-0-49-48\`; \`kubelet_service_highfreq_error.txt\` shows \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` from 13:41:46Z.

5. **CNI Plugin Initialization Delay — Network Not Ready on All Nodes**
   - All three master nodes started with no CNI configuration in \`/etc/kubernetes/cni/net.d/\` from 13:41:36Z through at least 13:44:22Z (~3 minutes). CRI-O logged \`CNI plugin not yet initialized\` every 30 seconds on all three nodes. Kubelet reported \`NetworkReady=false\` and \`NetworkPluginNotReady\`.
   - This prevented any new pods from starting network interfaces during the critical early upgrade window, compounding the API server recovery delay, contributing to the cascade of pod failures, and directly contributing to the OLM pod scheduling failures.
   - Evidence: \`crio_service_rare_error.txt\` shows continuous CNI warnings from 13:41:36Z to 13:44:22Z on all three nodes; \`kubelet_service_highfreq_error.txt\` shows \`pod_workers.go\` errors for multiple pods with \`network is not ready\`.

6. **Missing Kubelet Server Certificate (\`kubelet-server-current.pem\`)**
   - During the early phase of the upgrade, the kubelet on \`ip-10-0-122-129\` could not initialize its certificate reloader because \`/var/lib/kubelet/pki/kubelet-server-current.pem\` was absent. This caused transient API server connectivity issues and delayed the kube-apiserver static pod rollout.
   - Kubelet TLS handshake errors (\`EOF\`) persist at 14:22:56Z, 15:40:56Z, and 16:58:56Z across all three nodes, indicating the certificate issue was not immediately self-resolved and continued to impact cluster stability throughout the upgrade window.
   - Evidence: \`namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-122-129/.../logs/current.log\` shows \`failed to initialize certificate reloader\` with the missing PEM path; \`kubelet_service_highfreq_error.txt\` shows \`http: TLS handshake error from <node-IP>: EOF\` at multiple timestamps hours after the initial failure.

## Secondary Causes / Contributing Factors

- **\`system:anonymous\` authentication during early boot**: All three kubelets started authenticating as \`system:anonymous\` for the first ~5–10 minutes of the upgrade (13:41:36–~13:42:30Z), indicating the API server's bootstrap RBAC was not yet applied when the kubelets first connected. This caused cascading failures: node registration failed, CSINode publishing failed, lease creation failed, and events could not be posted.
- **\`openshift-apiserver\` anti-affinity scheduling failures**: Multiple \`openshift-apiserver\` ReplicaSets failed scheduling with \`0/3 nodes are available: 3 node(s) didn't match pod anti-affinity rules\`, indicating that during the rolling update, the scheduler could not place new pods while old pods were still terminating on the same nodes.
- **Missing secrets/configmaps for \`openshift-apiserver\`**: The \`serving-cert\` Secret and \`audit-0\` ConfigMap were not found during the early upgrade phase, causing \`FailedMount\` events on multiple \`openshift-apiserver\` pods. These are created by the \`openshift-apiserver-operator\` and their absence indicates the operator itself was delayed.
- **Node Taints Blocking OLM Pod Scheduling**: All three master nodes carried untolerated taints during the upgrade window, causing \`FailedScheduling\` for \`catalog-operator\`, \`olm-operator\`, \`package-server-manager\`, and \`collect-profiles\` pods. This is expected behavior during a master node upgrade but compounded the OLM recovery delay.
- **Missing OLM Serving-Cert Secrets**: \`catalog-operator-serving-cert\`, \`olm-operator-serving-cert\`, and \`package-server-manager-serving-cert\` Secrets were not found during pod startup (7 \`FailedMount\` events each), indicating the service-ca operator had not yet provisioned these Secrets when the pods were first scheduled.
- **Secret Cache Sync Timeout**: \`collect-profiles-29470425-g9kcc\` pod failed with \`MountVolume.SetUp failed for volume "secret-volume": failed to sync secret cache: timed out waiting for the condition\` — confirming the API server's secret informer cache was not synchronized during the outage window.
- **Webhook Authorizer Rate Limiter Exhaustion**: At 13:55:00Z, the webhook authorizer's client rate limiter was exhausted (\`context canceled\`), indicating the API server was under extreme load or had lost connectivity to the webhook backend during the outage transition.
- **\`community-operators\` CatalogSource Pod Restart**: The \`community-operators\` pod at \`172.30.105.7:50051\` was restarted or rescheduled during the upgrade (connection refused from 13:46Z), likely due to the node taint/CNI issues, causing a 10+ minute gap in operator catalog availability.
- **\`packageserver\` APIService Registration Delay**: The \`InstallWaiting: apiServices not installed\` event (count=2) for \`packageserver\` confirms the \`packages.operators.coreos.com\` APIService registration was delayed, consistent with the API server being unable to register aggregated API services during the etcd outage.
- **Storage Version Migrator Failures**: The \`kube-storage-version-migrator\` repeatedly failed to list \`flowcontrol.apiserver.k8s.io\` resources (FlowSchema, PriorityLevelConfiguration) during the upgrade window. The \`kube-storage-version-migrator-operator\` entered CrashLoopBackOff at 13:55:50Z. While expected during API server rollout, this can delay upgrade completion if the migrator is blocked for extended periods.
- **Missing \`kube-apiserver-server-ca\` ConfigMap**: The MCO could not find this ConfigMap during the upgrade, indicating the kube-apiserver CA propagation was delayed. This is a transient condition but contributed to the MCO degradation window.
- **\`ingress-operator\` CrashLoopBackOff**: The ingress operator entered CrashLoopBackOff at ~14:05:08Z, indicating the ingress stack was also impacted by the API outage.
- **Multiple kube-controller-manager installer retries**: Installer pods for the kube-controller-manager (revisions 4, 4-retry-1, 4-retry-2) show repeated retry attempts, indicating the controller manager static pod rollout was unstable during the upgrade.
- **Image pull interruptions from \`quay.io\`**: CRI-O reported multiple \`unexpected EOF\` errors when downloading image blobs from \`quay.io\` (13:43:37Z, 13:46:07Z, 14:22:36–14:22:47Z). These retried successfully but added latency to new pod startup during the upgrade.
- **Stale container references**: Kubelet and CRI-O reported numerous \`container with ID ... not found\` errors as containers from the previous static pod generation were cleaned up. This is expected during static pod replacement but contributed to log noise and minor delays.
- **No Update Channel Configured**: The ClusterVersion shows \`NoChannel\` for the update channel. While not a direct blocker, this means the cluster cannot receive automatic update recommendations or rollback guidance from the Cincinnati update service.
- **cadvisor stats cache misses**: Multiple kubelet \`cadvisor_stats_provider.go\` errors (\`RecentStats: unable to find data in memory cache\`) across all nodes from 13:44:12Z through 16:59:56Z — transient and expected during container churn but indicate high container lifecycle activity throughout the incident.
- **Benign sysinfo Error**: \`Failed to get global filesystem information: not implemented\` in \`sysinfo_rare_error.txt\` at 17:02:34Z across all nodes is a known cAdvisor limitation on certain container runtimes/kernels and has no impact on the upgrade.

## Aggregated Error Patterns

| Pattern | Source | Classification | Significance |
|---------|--------|----------------|--------------|
| \`UpdatePayloadResourceInvalid: unable to apply CRD ipaddressclaims.ipam.cluster.x-k8s.io\` | \`clusterversions.yaml\`, CVO \`current.log\` | **CRITICAL — Primary Blocker** | Prevents upgrade from completing; CVO cannot apply CRD from ec.18 payload; continuous retry loop |
| \`Failing=True, Progressing=True (Partial)\` on ClusterVersion | \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` | **CRITICAL — Upgrade Stalled** | Upgrade is actively failing and stalled in partial state |
| \`grpc: addrConn.createTransport failed\` to all three etcd endpoints (\`:2379\`) | \`current_highfreq_error.txt\` | **CRITICAL — etcd Quorum Loss** | All three etcd members unreachable at different points; cluster lost quorum during upgrade |
| Etcd startup probe HTTP 503 (\`failed to establish etcd client: giving up after 3 tries\`) on all three master nodes | \`kubelet_service_highfreq_error.txt\` | **CRITICAL — etcd Startup Failure** | etcd containers failed to start on all three nodes during static pod replacement |
| Etcd guard readiness probe \`context deadline exceeded\` on all three nodes | \`kubelet_service_rare_error.txt\` | **CRITICAL — etcd Unavailability** | etcd guard confirms etcd not serving on port 9980 across all masters |
| Kubelet node lease update \`context deadline exceeded\` / \`request canceled\` on all three nodes | \`kubelet_service_rare_error.txt\` | **CRITICAL — API Server Outage** | All three nodes unable to update leases for ~20 minutes; API server unreachable |
| \`the server is currently unable to handle the request\` (packageserver CSV sync) | \`current_highfreq_error.txt\` (OLM) | **HIGH — API Server Degradation Indicator** | OLM cannot update packageserver install status; confirms API server overload began at 13:50Z, 3 min before documented outage |
| \`grpc: addrConn.createTransport failed\` to \`172.30.105.7:50051\` (community-operators) | \`current_rare_error.txt\` (OLM catalog) | **HIGH — CatalogSource Unavailable** | community-operators catalog pod unreachable for 10+ minutes; OLM cache refresh fails; operator updates blocked |
| \`sync "openshift-operator-lifecycle-manager/packageserver" failed\` (queueinformer) | \`current_highfreq_error.txt\` | **HIGH — OLM Reconciliation Failure** | packageserver CSV stuck in Installing; OLM operator reconciliation loop failing repeatedly |
| \`error getting bundle stream\` / \`error getting package stream\` (community-operators) | \`current_rare_error.txt\` | **HIGH — Catalog Cache Stale** | OLM catalog cache cannot refresh; operator bundle/package metadata unavailable |
| \`Failed to make webhook authorizer request: context canceled\` | \`current_rare_error.txt\` | **HIGH — Webhook/Auth Failure** | Webhook authorizer exhausted during API outage; packages.operators.coreos.com/v1 endpoint timed out |
| \`FailedScheduling: 0/3 nodes available: untolerated taint(s)\` (OLM pods) | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — OLM Pod Scheduling Blocked** | All OLM operator pods unschedulable during master node upgrade taint window |
| \`FailedMount: secret "catalog-operator-serving-cert" not found\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Missing TLS Secrets** | OLM operator pods cannot start without serving-cert Secrets; cert provisioning lagging |
| \`FailedMount: secret "olm-operator-serving-cert" not found\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Missing TLS Secrets** | OLM operator pod startup blocked |
| \`FailedMount: secret "package-server-manager-serving-cert" not found\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Missing TLS Secrets** | Package server manager pod startup blocked |
| \`failed to sync secret cache: timed out waiting for the condition\` | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **HIGH — Secret Informer Cache Timeout** | API server informer cache not synchronized during outage; pod volume mount fails |
| \`MachineConfigPool master is degraded\` (0/3 nodes ready, 0/3 updated) | \`namespaces/openshift-machine-config-operator/core/events.yaml\` | **HIGH — MCO Blocker** | Blocks node configuration rollout; bootstrap MachineConfig mismatch on \`ip-10-0-49-48\` |
| \`Node ip-10-0-49-48 has bootstrap-generated MachineConfig mismatch (99-master-generated-registries)\` | \`namespaces/openshift-machine-config-operator/pods/machine-config-controller/.../logs/current.log\` | **HIGH — MCO Config Mismatch** | Specific node stuck with conflicting bootstrap config; prevents MCO rollout |
| \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\`, \`ip-10-0-122-129\` | \`kubelet_service_highfreq_error.txt\` | **HIGH — MCO Component Failure** | MCO proxy sidecar failing from 13:41:46Z; related to MachineConfig mismatch |
| \`ClusterOperator machine-config-operator: Degraded=True\` | \`cluster-scoped-resources/config.openshift.io/clusteroperators.yaml\` | **HIGH — Operator Degraded** | MCO operator itself reporting degraded state during upgrade |
| \`openshift-apiserver\` pods: \`FailedMount\` (\`serving-cert\` Secret, \`audit-0\` ConfigMap not found) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — API Server Rollout Blocked** | Required secrets/configmaps not yet created; 8+ ReplicaSet revisions affected |
| \`openshift-apiserver\` pods: \`FailedScheduling\` (anti-affinity — 0/3 nodes available) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — Scheduling Failure** | Anti-affinity prevents placement while old pods still terminating |
| \`openshift-apiserver\` readiness probe HTTP 500 (\`[-]shutdown failed: reason withheld\`) | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — API Server Unhealthy** | Pods in shutdown state still receiving readiness checks; 10+ occurrences per pod |
| \`[-]poststarthook/authorization.openshift.io-bootstrapclusterroles failed\` | \`namespaces/openshift-apiserver/core/events.yaml\` | **HIGH — RBAC Bootstrap Incomplete** | RBAC bootstrap not complete during openshift-apiserver startup |
| \`poststarthook/rbac/bootstrap-roles failed\` on kube-apiserver \`ip-10-0-97-146\` | \`kubelet_service_rare_error.txt\` | **HIGH — RBAC Bootstrap Incomplete** | Kube-apiserver RBAC bootstrap still in progress at 14:37:47Z |
| \`system:anonymous\` forbidden errors (nodes, services, leases, CSI drivers) | \`kubelet_service_rare_error.txt\` | **HIGH — Auth Bootstrap Delay** | Kubelets not yet authenticated; RBAC not bootstrapped at upgrade start |
| \`CNI plugin not yet initialized\` / \`NetworkPluginNotReady\` / \`NetworkReady=false\` | \`crio_service_rare_error.txt\`, \`kubelet_service_highfreq_error.txt\` | **HIGH — Network Initialization Delay** | All three nodes had no CNI config for ~3 minutes; pods could not start networking |
| \`failed to initialize certificate reloader\` (\`kubelet-server-current.pem\` missing) | \`namespaces/openshift-kube-apiserver/pods/kube-apiserver-ip-10-0-122-129/.../logs/current.log\` | **HIGH — PKI Bootstrap Issue** | Missing kubelet server cert delays kube-apiserver static pod rollout |
| \`http: TLS handshake error from <node-IP>: EOF\` | \`kubelet_service_highfreq_error.txt\` | **HIGH — Kubelet TLS Issue** | Kubelet serving certificate problems persist at 14:22Z, 15:40Z, 16:58Z |
| \`InstallWaiting: apiServices not installed\` (packageserver) | \`openshift-operator-lifecycle-manager/core/events.yaml\` | **MEDIUM — APIService Registration Delay** | packages.operators.coreos.com APIService not registered; aggregated API unavailable |
| \`failed to list flowcontrol.apiserver.k8s.io/v1 FlowSchema: the server could not find the requested resource\` | \`namespaces/openshift-kube-storage-version-migrator/pods/.../logs/current.log\` | **MEDIUM — Storage Migrator Blocked** | Storage migrator blocked on missing API resource during rollout |
| \`kube-storage-version-migrator-operator\` CrashLoopBackOff | \`namespaces/openshift-kube-storage-version-migrator/core/events.yaml\` | **MEDIUM — Operator Failure** | Storage version migrator operator down during API outage |
| \`ingress-operator\` CrashLoopBackOff | \`namespaces/openshift-ingress-operator/core/events.yaml\` | **MEDIUM — Operator Failure** | Ingress operator impacted by API outage |
| \`configmaps "kube-apiserver-server-ca" not found\` | \`namespaces/openshift-machine-config-operator/pods/machine-config-operator/.../logs/current.log\` | **MEDIUM — CA Propagation Delay** | MCO cannot read CA configmap; API server CA propagation delayed |
| \`PodNetworkConnectivityCheck\` CRD not found (\`controlplane.operator.openshift.io\`) | \`current_rare_error.txt\` | **MEDIUM — CRD Registration Delay** | Network connectivity check operator cannot function; CRD not yet registered during API server roll |
| etcd gRPC \`operation was canceled\` at 15:35:53Z | \`current_highfreq_error.txt\` | **MEDIUM — etcd Stabilization** | etcd connections still being canceled hours after outage; etcd not fully stable |
| \`kube-controller-manager installer-4 retry-1, retry-2 failures\` | \`namespaces/openshift-kube-controller-manager/pods/installer-*/logs\` | **MEDIUM — Static Pod Rollout Unstable** | Controller manager static pod rollout required multiple retries |
| CRI-O \`Killing container ... failed: process not running\` | \`crio_service_rare_error.txt\` | **LOW — Container Lifecycle Race** | Containers already exited before SIGKILL; expected during static pod replacement |
| CRI-O \`Error encountered when checking whether cri-o should wipe containers\` (missing \`/var/run/crio/version\`) | \`crio_service_rare_error.txt\` | **LOW — CRI-O Fresh Start** | Normal on first start after reboot/upgrade; all three nodes affected |
| Image blob download \`unexpected EOF\` from \`quay.io\` | \`crio_service_rare_error.txt\` | **LOW — Network Transient** | Image pull retries; resolved automatically but adds latency to pod startup |
| \`cadvisor_stats_provider: RecentStats: unable to find data in memory cache\` | \`kubelet_service_rare_error.txt\` | **LOW — Stats Cache Miss** | Transient during container churn; no operational impact |
| \`Failed to get the status of process with PID ... no such file or directory\` | \`crio_service_rare_error.txt\` | **LOW — Process Cleanup Race** | CRI-O checking PID of already-exited process; benign |
| \`NoChannel\` — no update channel configured on ClusterVersion | \`cluster-scoped-resources/config.openshift.io/clusterversions.yaml\` | **LOW — Configuration Gap** | Cluster cannot receive automatic update recommendations or rollback guidance |
| \`Failed to get global filesystem information: not implemented\` | \`sysinfo_rare_error.txt\` | **LOW — Benign** | cAdvisor sysinfo limitation on certain container runtimes/kernels; no impact on upgrade |

## Remediation
### 1. [IMMEDIATE — CRITICAL] Resolve the Invalid CRD Payload Resource Blocking the CVO

The \`ipaddressclaims.ipam.cluster.x-k8s.io\` CRD in the \`4.21.0-okd-scos.ec.18\` release payload is being rejected by the API server with \`UpdatePayloadResourceInvalid\`. This is the primary upgrade blocker and must be resolved before any other remediation can result in a completed upgrade. Investigate whether the existing CRD has a conflicting schema, conversion webhook, or stored version that is incompatible with the ec.18 payload version.

**Steps:**
\`\`\`bash
# Inspect the current CRD state
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o yaml

# Get the exact CVO failure message for the rejection reason
oc get clusterversion version \\
  -o jsonpath='{.status.conditions[?(@.type=="Failing")].message}'

# Review CVO logs for the specific API server rejection message
oc logs -n openshift-cluster-version \\
  deployment/cluster-version-operator --tail=200 \\
  | grep -i "ipaddressclaim\\|invalid\\|UpdatePayloadResourceInvalid"

# Check for conversion webhooks that may be conflicting
oc get validatingwebhookconfigurations,mutatingwebhookconfigurations \\
  | grep -i "ipam\\|cluster-api\\|capi"

# Check if a stored version mismatch exists
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io \\
  -o jsonpath='{.status.storedVersions}'

# Check if any IPAddressClaim objects exist (must be empty before deletion)
oc get ipaddressclaims --all-namespaces 2>/dev/null | wc -l

# Option A: If no IPAddressClaim objects exist, delete the CRD and allow CVO to recreate it
# WARNING: Only proceed if the count above is 0
oc delete crd ipaddressclaims.ipam.cluster.x-k8s.io

# Option B: If IPAddressClaim objects exist, examine the schema difference
# and manually patch the CRD to match what ec.18 expects
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io -o json > /tmp/ipaddressclaims-current.json
# Compare with the payload CRD spec and apply the necessary schema changes

# After resolving, force CVO to retry the update
oc patch clusterversion version --type=merge \\
  -p '{"spec":{"desiredUpdate":{"version":"4.21.0-okd-scos.ec.18"}}}'
\`\`\`

**Validation:**
\`\`\`bash
# Confirm CVO is no longer in Failing state
oc get clusterversion version \\
  -o jsonpath='{.status.conditions[?(@.type=="Failing")].status}'
# Expected: False

# Confirm CRD was re-applied and accepted by the API server
oc get crd ipaddressclaims.ipam.cluster.x-k8s.io \\
  -o jsonpath='{.status.conditions[?(@.type=="Established")].status}'
# Expected: True

# Confirm CVO is progressing
oc get clusterversion version -w
# Expected: Progressing=True (healthy progress), Failing=False
\`\`\`

---

### 2. [IMMEDIATE — CRITICAL] Verify etcd Health and Quorum Restoration

All three etcd members were unreachable at different points during the upgrade (14:00–14:11Z), and etcd gRPC cancellation errors persisted as late as 15:35:53Z. Confirm etcd has fully recovered and quorum is stable before proceeding with any further upgrade steps.

**Steps:**
\`\`\`bash
# Check etcd pod status on all master nodes
oc get pods -n openshift-etcd -o wide

# Check etcd cluster health via etcdctl from within the etcd pod
oc -n openshift-etcd exec -it \\
  $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
  -- etcdctl endpoint health \\
  --endpoints=https://10.0.49.48:2379,https://10.0.122.129:2379,https://10.0.97.146:2379 \\
  --cacert=/etc/kubernetes/static-pod-resources/etcd-certs/configmaps/etcd-serving-ca/ca-bundle.crt \\
  --cert=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).crt \\
  --key=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).key

# Check etcd member list to confirm all three members are present and started
oc -n openshift-etcd exec -it \\
  $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
  -- etcdctl member list \\
  --endpoints=https://localhost:2379 \\
  --cacert=/etc/kubernetes/static-pod-resources/etcd-certs/configmaps/etcd-serving-ca/ca-bundle.crt \\
  --cert=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).crt \\
  --key=/etc/kubernetes/static-pod-resources/etcd-certs/secrets/etcd-all-certs/etcd-peer-$(hostname).key

# Check etcd cluster operator status
oc get co etcd -o yaml | grep -A 20 "conditions:"

# Check for ongoing gRPC errors in etcd logs
oc logs -n openshift-etcd \\
  $(oc get pods -n openshift-etcd -l app=etcd -o name | head -1) \\
  --tail=100 | grep -i "grpc\\|canceled\\|refused\\|error\\|fail\\|panic\\|leader"

# Check etcd guard pods
oc get pods -n openshift-etcd -l app=etcd-guard -o wide
\`\`\`

**Validation:**
\`\`\`bash
# All three endpoints should report "is healthy"
# member list should show 3 members, all "started"
oc get pods -n openshift-etcd
# Expected: all etcd pods Running and Ready (3/3)

# Confirm etcd operator is not degraded
oc get co etcd \\
  -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
# Expected: False

# No recent gRPC transport failures in etcd logs
oc logs -n openshift-etcd -l app=etcd --tail=50 \\
  | grep -i "addrConn\\|connection refused\\|operation was canceled"
# Expected: no recent output
\`\`\`

---

### 3. [IMMEDIATE — CRITICAL] Restore OLM Subsystem and Verify packageserver Recovery

The OLM subsystem was completely non-functional during the upgrade window. The \`packageserver\` CSV was stuck in \`Installing\`, the \`community-operators\` CatalogSource was unreachable via gRPC for 10+ minutes, and all OLM operator pods failed to schedule due to untolerated node taints and missing serving-cert Secrets. While the \`packageserver\` CSV eventually reached \`InstallSucceeded\` after the API server recovered, verify the full OLM stack is healthy and the \`community-operators\` CatalogSource pod has fully recovered before proceeding with the upgrade.

**Steps:**
\`\`\`bash
# Check overall OLM cluster operator health
oc get co operator-lifecycle-manager \\
  operator-lifecycle-manager-catalog \\
  operator-lifecycle-manager-packageserver

# Check packageserver CSV status
oc get csv -n openshift-operator-lifecycle-manager packageserver \\
  -o jsonpath='{.status.phase}'

# Check all OLM pods are running and not in CrashLoopBackOff
oc get pods -n openshift-operator-lifecycle-manager

# Check community-operators CatalogSource pod specifically
oc get pods -n openshift-marketplace | grep community-operators
oc logs -n openshift-marketplace \\
  $(oc get pods -n openshift-marketplace \\
  -l olm.catalogSource=community-operators \\
  -o name | head -1) --tail=50

# Verify the community-operators gRPC endpoint is now reachable
# (172.30.105.7 was the failing IP — check current service IP)
oc get svc -n openshift-marketplace community-operators

# Check if serving-cert Secrets have been provisioned for all OLM components
oc get secret -n openshift-operator-lifecycle-manager \\
  catalog-operator-serving-cert \\
  olm-operator-serving-cert \\
  package-server-manager-serving-cert

# If Secrets are still missing, restart the service-ca operator to re-trigger provisioning
oc rollout restart deployment/service-ca -n openshift-service-ca

# Check for any remaining OLM sync errors
oc logs -n openshift-operator-lifecycle-manager \\
  deployment/olm-operator --tail=100 | grep -i "error\\|failed"
oc logs -n openshift-operator-lifecycle-manager \\
  deployment/catalog-operator --tail=100 | grep -i "error\\|failed"

# Verify the packages.operators.coreos.com API group is available
oc api-resources | grep packages.operators.coreos.com
\`\`\`

**Validation:**
\`\`\`bash
# packageserver CSV should be Succeeded
oc get csv -n openshift-operator-lifecycle-manager packageserver \\
  -o jsonpath='{.status.phase}'
# Expected: Succeeded

# All OLM cluster operators should be Available=True, Degraded=False
oc get co operator-lifecycle-manager \\
  operator-lifecycle-manager-catalog \\
  operator-lifecycle-manager-packageserver \\
  -o custom-columns='NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="Available")].status,DEGRADED:.status.conditions[?(@.type=="Degraded")].status'
# Expected: all AVAILABLE=True, DEGRADED=False

# community-operators CatalogSource should be READY
oc get catalogsource -n openshift-marketplace community-operators \\
  -o jsonpath='{.status.connectionState.lastObservedState}'
# Expected: READY
\`\`\`

---

### 4. [IMMEDIATE — CRITICAL] Resolve MachineConfigPool \`master\` Degradation

The master MachineConfigPool is degraded with 0/3 nodes updated due to a bootstrap MachineConfig mismatch (\`99-master-generated-registries\`) on \`ip-10-0-49-48\`. This blocks the MCO from completing node configuration and is a prerequisite for the upgrade to proceed. The \`kube-rbac-proxy-crio\` CrashLoopBackOff on \`ip-10-0-49-48\` and \`ip-10-0-122-129\` is a direct symptom of this mismatch.

**Steps:**
\`\`\`bash
# Check current MachineConfigPool status
oc get mcp master -o yaml | grep -A 10 \\
  "degradedMachineCount\\|updatedMachineCount\\|readyMachineCount"

# Identify the mismatched MachineConfig on ip-10-0-49-48
oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/currentConfig}'
oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/desiredConfig}'

# Examine the bootstrap-generated MachineConfig
oc get mc 99-master-generated-registries -o yaml

# Check MCO controller logs for the specific mismatch details
oc logs -n openshift-machine-config-operator \\
  deployment/machine-config-controller --tail=200 \\
  | grep -i "ip-10-0-49-48\\|mismatch\\|bootstrap\\|generated-registries"

# Check kube-rbac-proxy-crio status on the affected nodes
oc get pod -n openshift-machine-config-operator \\
  -l app=kube-rbac-proxy-crio \\
  --field-selector spec.nodeName=ip-10-0-49-48.us-east-2.compute.internal

# Cordon the node before making changes
oc adm cordon ip-10-0-49-48.us-east-2.compute.internal

# Delete the machine-config-daemon pod on that node to force re-sync
oc delete pod -n openshift-machine-config-operator \\
  $(oc get pods -n openshift-machine-config-operator \\
  --field-selector spec.nodeName=ip-10-0-49-48.us-east-2.compute.internal \\
  -l k8s-app=machine-config-daemon -o name)

# Monitor the MCD logs on that node
oc logs -n openshift-machine-config-operator \\
  $(oc get pods -n openshift-machine-config-operator \\
  --field-selector spec.nodeName=ip-10-0-49-48.us-east-2.compute.internal \\
  -l k8s-app=machine-config-daemon -o name) -f

# If MCD re-sync does not resolve the mismatch, force the desired config annotation
DESIRED_MC=$(oc get node ip-10-0-49-48.us-east-2.compute.internal \\
  -o jsonpath='{.metadata.annotations.machineconfiguration\\.openshift\\.io/desiredConfig}')
oc annotate node ip-10-0-49-48.us-east-2.compute.internal \\
  machineconfiguration.openshift.io/currentConfig=\${DESIRED_MC} --overwrite

# Uncordon the node after the MCD has applied the correct config
oc adm uncordon ip-10-0-49-48.us-east-2.compute.internal
\`\`\`

**Validation:**
\`\`\`bash
# MachineConfigPool should show all nodes updated and no degraded nodes
oc get mcp master
# Expected: MACHINECOUNT=3, READYMACHINECOUNT=3, DEGRADEDMACHINECOUNT=0, UPDATEDMACHINECOUNT=3

# Verify kube-rbac-proxy-crio is no longer in CrashLoopBackOff
oc get pod -n openshift-machine-config-operator -l app=kube-rbac-proxy-crio
# Expected: all pods Running

# MCP should no longer be degraded
oc get mcp master \\
  -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
# Expected: False
\`\`\`

---

### 5. [HIGH] Restore Missing Kubelet Server Certificate on All Master Nodes

The kubelet server certificate (\`kubelet-server-current.pem\`) was missing at upgrade start on \`ip-10-0-122-129\`, causing TLS handshake errors that persisted across all three nodes as late as 16:58Z. Pending CSRs must be approved to allow the kubelet to obtain and persist its serving certificate.

**Steps:**
\`\`\`bash
# Check if the certificate now exists on all master nodes
for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
  echo "=== Checking \${node} ==="
  oc debug node/\${node}.us-east-2.compute.internal -- \\
    chroot /host ls -la /var/lib/kubelet/pki/kubelet-server-current.pem 2>/dev/null \\
    || echo "MISSING on \${node}"
done

# Check for pending kubelet serving CSRs
oc get csr | grep -i "kubelet-serving\\|Pending"

# Approve all pending kubelet serving CSRs
oc get csr -o name | xargs oc adm certificate approve

# Check the machine-approver operator status
oc get co machine-approver -o yaml | grep -A 10 "conditions:"

# If the certificate is still missing after CSR approval, restart kubelet on the affected node
oc debug node/ip-10-0-122-129.us-east-2.compute.internal -- \\
  chroot /host systemctl restart kubelet

# Verify kubelet is using the correct certificate after approval on all nodes
for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
  echo "=== \${node} ==="
  oc debug node/\${node}.us-east-2.compute.internal -- \\
    chroot /host openssl x509 \\
      -in /var/lib/kubelet/pki/kubelet-server-current.pem \\
      -noout -dates -subject 2>/dev/null \\
    || echo "Certificate not yet present on \${node}"
done
\`\`\`

**Validation:**
\`\`\`bash
# No pending kubelet serving CSRs
oc get csr | grep Pending
# Expected: no output

# No recent TLS handshake errors in kubelet logs on any master node
for node in ip-10-0-49-48 ip-10-0-122-129 ip-10-0-97-146; do
  echo "=== \${node} ==="
  oc adm node-logs \${node}.us-east-2.compute.internal \\
    --unit=kubelet --tail=50 | grep "TLS handshake"
done
# Expected`;
