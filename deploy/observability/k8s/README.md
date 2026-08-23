# Phase 6c — Observability on kubeadm

Compose (6a/6b) stays the laptop inner loop. This directory is the **cluster**
stack: scrape `GET /metrics`, pod logs, a Tempo OTLP sink, and optional Langfuse.

| Piece | Where | Sync |
|-------|--------|------|
| kube-prometheus-stack 88.5.2 | `observability` | automated (Argo) or `lab_install_observability.sh` |
| Loki 6.29.0 (single binary) | `observability` | same |
| Tempo 1.23.2 | `observability` | same |
| Grafana Alloy (logs → Loki) | `observability` DaemonSet | same |
| Langfuse v3 | `langfuse` | **manual** (`--with-langfuse`) |
| AnsibleAI ServiceMonitor + PrometheusRule + celery-exporter | `ansibleai` chart (staging on) | after CRDs exist |

Never `:latest`. Storage is `local-path`. Grafana NodePort **30300**.
Langfuse NodePort **30301** — not the member Ingress (`:30080`).

## Install from `.19` (recommended on this lab)

Argo’s repo-server often cannot finish `git fetch` or Helm-repo HTTPS
(same worker egress as ImagePullBackOff). Helm on `.19` uses that VM’s internet.

```bash
export KUBECONFIG=deploy/ansible/artifacts/kubeconfig
bash scripts/lab_install_observability.sh
# optional, heavy:
# bash scripts/lab_install_observability.sh --with-langfuse
```

Then apply the GitOps Applications (values-only if Helm already owns the release):

```bash
kubectl apply -f deploy/gitops/applications/kube-prometheus.yaml
kubectl apply -f deploy/gitops/applications/loki.yaml
kubectl apply -f deploy/gitops/applications/tempo.yaml
kubectl apply -f deploy/gitops/applications/alloy.yaml
# kubectl apply -f deploy/gitops/applications/langfuse.yaml
```

If Argo ComparisonError is still GitHub, point `spec.sources[1].repoURL` at the
lab git daemon (`git://192.168.1.19/ansible-iac-ai.git`) the same way as staging.

## Order

1. This stack (CRDs for `ServiceMonitor` / `PrometheusRule`).
2. Sideload images on **both** `.18` and `.12` if pulls reset — list in [images.txt](images.txt).
3. Sync `ansibleai-staging` (staging values enable ServiceMonitor, rules, celery-exporter).
4. Open `http://192.168.1.18:30300` → **AnsibleAI overview**. Prometheus targets should show the API and celery-exporter.

## Langfuse (optional)

After `--with-langfuse`, create a project + API keys in the Langfuse UI, then
set on the app (do not commit real keys):

```yaml
# values overlay or --set
app:
  langfuseEnabled: "true"
  langfuseHost: "http://langfuse.langfuse.svc.cluster.local:3000"
observability:
  langfuseEgress: true
secrets:
  langfusePublicKey: pk-lf-...
  langfuseSecretKey: sk-lf-...
```

Members still do not see Langfuse. GPU / DCGM panels stay out until 4-gpu.

## What this does not install

- Alertmanager paging (Alertmanager is in-cluster only)
- oauth2-proxy in front of Grafana (lab password in values)
- Harbor, Velero, Kyverno (Phase 8)
