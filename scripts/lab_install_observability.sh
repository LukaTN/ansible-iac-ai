#!/usr/bin/env bash
# Phase 6c — install cluster observability from .19 (has internet).
# Prefer this when Argo cannot fetch Helm repos / GitHub (same egress as repo-server).
# Usage:
#   export KUBECONFIG=deploy/ansible/artifacts/kubeconfig
#   bash scripts/lab_install_observability.sh
#   bash scripts/lab_install_observability.sh --with-langfuse

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=observability
WITH_LANGFUSE=0

for arg in "$@"; do
  case "$arg" in
    --with-langfuse) WITH_LANGFUSE=1 ;;
    -h|--help)
      echo "Usage: $0 [--with-langfuse]"
      exit 0
      ;;
    *)
      echo "unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

command -v helm >/dev/null
command -v kubectl >/dev/null

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
# node-exporter / Alloy need hostPath. The app chart used to stamp baseline
# on this namespace, which blocks kube-prometheus-stack.
kubectl label namespace "$NS" \
  pod-security.kubernetes.io/enforce=privileged \
  pod-security.kubernetes.io/audit=privileged \
  pod-security.kubernetes.io/warn=privileged \
  --overwrite

if helm status kube-prometheus -n "$NS" 2>/dev/null | grep -Eq 'pending-install|pending-upgrade|failed'; then
  echo ">> leftover kube-prometheus release — uninstalling so Helm can retry"
  helm uninstall kube-prometheus -n "$NS" --wait --timeout 5m || true
fi

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add langfuse https://langfuse.github.io/langfuse-k8s
helm repo update

echo ">> kube-prometheus-stack 88.5.2"
if ! helm upgrade --install kube-prometheus prometheus-community/kube-prometheus-stack \
  --version 88.5.2 \
  --namespace "$NS" \
  --create-namespace \
  -f "$ROOT/deploy/observability/k8s/values/kube-prometheus-lab.yaml" \
  --timeout 10m
then
  echo "helm failed. pods / recent events:" >&2
  kubectl -n "$NS" get pods -o wide || true
  kubectl -n "$NS" get events --sort-by=.lastTimestamp | tail -n 30 || true
  echo "If ImagePullBackOff: sideload deploy/observability/k8s/images.txt on .18 and .12" >&2
  echo "If Helm says pending-install: helm uninstall kube-prometheus -n $NS" >&2
  exit 1
fi
kubectl wait --for=condition=Established \
  crd/prometheuses.monitoring.coreos.com \
  crd/servicemonitors.monitoring.coreos.com \
  --timeout=180s
echo ">> kube-prometheus submitted (images may still be pulling — not a Helm wait)"

echo ">> Loki 6.29.0"
helm upgrade --install loki grafana/loki \
  --version 6.29.0 \
  --namespace "$NS" \
  -f "$ROOT/deploy/observability/k8s/values/loki-lab.yaml" \
  --wait --timeout 10m

echo ">> Tempo 1.23.2"
helm upgrade --install tempo grafana/tempo \
  --version 1.23.2 \
  --namespace "$NS" \
  -f "$ROOT/deploy/observability/k8s/values/tempo-lab.yaml" \
  --wait --timeout 10m

echo ">> Alloy 1.0.3"
helm upgrade --install alloy grafana/alloy \
  --version 1.0.3 \
  --namespace "$NS" \
  -f "$ROOT/deploy/observability/k8s/values/alloy-lab.yaml" \
  --wait --timeout 10m

DASH="$ROOT/deploy/observability/grafana/provisioning/dashboards/json/ansibleai-overview.json"
kubectl -n "$NS" create configmap ansibleai-overview-dashboard \
  --from-file=ansibleai-overview.json="$DASH" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" label configmap ansibleai-overview-dashboard \
  grafana_dashboard=1 --overwrite

if [[ "$WITH_LANGFUSE" -eq 1 ]]; then
  echo ">> Langfuse (manual / optional)"
  helm upgrade --install langfuse langfuse/langfuse \
    --version 1.5.1 \
    --namespace langfuse \
    --create-namespace \
    -f "$ROOT/deploy/observability/k8s/values/langfuse-lab.yaml" \
    --timeout 15m
fi

echo
echo "Grafana NodePort 30300  (admin / lab-only-grafana)"
echo "  http://192.168.1.18:30300"
echo "Langfuse (if --with-langfuse): NodePort 30301"
echo "  http://192.168.1.18:30301"
echo
echo "Then sync ansibleai-staging so ServiceMonitor + PrometheusRule apply"
echo "(CRDs now exist). Sideload images from deploy/observability/k8s/images.txt"
echo "if pods stay ImagePullBackOff."
