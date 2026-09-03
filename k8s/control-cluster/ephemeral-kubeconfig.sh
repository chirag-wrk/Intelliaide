#!/bin/bash
NAMESPACE="${1:?Usage: $0 <namespace>}"

kubectl apply -f - <<RESOURCES
apiVersion: v1
kind: ServiceAccount
metadata:
  name: external-jobs-manager
  namespace: $NAMESPACE
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: external-workload-admin-binding
  namespace: $NAMESPACE
subjects:
- kind: ServiceAccount
  name: external-jobs-manager
  namespace: $NAMESPACE
roleRef:
  kind: ClusterRole
  name: admin
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Secret
metadata:
  name: external-workload-token-secret
  namespace: $NAMESPACE
  annotations:
    kubernetes.io/service-account.name: external-jobs-manager
type: kubernetes.io/service-account-token
RESOURCES

CLUSTER_API=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
TOKEN=$(kubectl get secret external-workload-token-secret -n "$NAMESPACE" -o jsonpath='{.data.token}' | base64 -d)

cat <<EOF > ./disposable-kubeconfig.yaml
apiVersion: v1
kind: Config
clusters:
- name: ephemeral-cluster
  cluster:
    server: $CLUSTER_API
    # certificate-authority-data: $CA_CERT
contexts:
- name: ephemeral-context
  context:
    cluster: ephemeral-cluster
    namespace: $NAMESPACE
    user: external-jobs-manager
current-context: ephemeral-context
users:
- name: external-jobs-manager
  user:
    token: $TOKEN
EOF

echo "Kubeconfig written to ./disposable-kubeconfig.yaml"
