#!/usr/bin/env bash
# Lab Keycloak on kubeadm (namespace identity). Run from .19 with cluster kubeconfig.
# Do not `helm upgrade ansibleai`: Argo-applied objects lack Helm ownership.
# Usage:
#   export KUBECONFIG=deploy/ansible/artifacts/kubeconfig
#   bash scripts/lab_install_keycloak.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$ROOT/deploy/helm/ansibleai"
APP_NS=ansibleai
ID_NS=identity
MASTER_IP=192.168.1.18
NODEPORT=30808
KC_IMAGE="quay.io/keycloak/keycloak:26.2.5"

command -v helm >/dev/null
command -v kubectl >/dev/null

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: $0"
  echo "Renders templates/keycloak.yaml + templates/networkpolicy-keycloak.yaml and kubectl apply."
  echo "Keeps AUTH_MODE=local. Sideload $KC_IMAGE on .18 and .12 if ImagePullBackOff."
  exit 0
fi

kubectl get ns "$APP_NS" >/dev/null

kubectl create namespace "$ID_NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace "$ID_NS" \
  pod-security.kubernetes.io/enforce=baseline \
  pod-security.kubernetes.io/audit=restricted \
  --overwrite

HELM_SET=()
if kubectl -n "$APP_NS" get secret ansibleai-app >/dev/null 2>&1; then
  PG_PASS="$(kubectl -n "$APP_NS" get secret ansibleai-app -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
  if [[ -n "$PG_PASS" ]]; then
    HELM_SET+=(--set "secrets.postgresPassword=${PG_PASS}")
  fi
fi

echo ">> rendering Keycloak + identity NetworkPolicies (not a Helm release)"
helm template ansibleai "$CHART" \
  --namespace "$APP_NS" \
  -f "$CHART/values-staging.yaml" \
  ${HELM_SET[@]+"${HELM_SET[@]}"} \
  --show-only templates/keycloak.yaml \
  --show-only templates/networkpolicy-keycloak.yaml \
  | kubectl apply -f -

echo
echo ">> identity pods / service (Ready comes after the image is present)"
kubectl -n "$ID_NS" get pods,svc -o wide || true
kubectl -n "$ID_NS" get events --sort-by=.lastTimestamp | tail -n 20 || true

echo
echo "Admin console (not the member Ingress :30080):"
echo "  http://${MASTER_IP}:${NODEPORT}/admin"
echo "  user admin / password from values-staging secrets.keycloakAdminPassword"
echo "    (lab default: lab-only-keycloak-admin)"
echo
echo "If ImagePullBackOff, from .19:"
echo "  docker pull ${KC_IMAGE}"
echo "  docker save ${KC_IMAGE} | ssh 192.168.1.18 sudo ctr -n k8s.io images import -"
echo "  docker save ${KC_IMAGE} | ssh 192.168.1.12 sudo ctr -n k8s.io images import -"
echo
echo "App login stays local until you patch AUTH_MODE=hybrid (plus OIDC_* on"
echo "ConfigMap ansibleai-config and OIDC_CLIENT_SECRET on Secret ansibleai-app)."
echo "Do not helm upgrade ansibleai. Keep ansibleai-staging manual while patched."
