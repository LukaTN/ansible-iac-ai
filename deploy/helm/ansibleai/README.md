# AnsibleAI Helm chart (Phase 4b)

Packages the Compose stack for the kubeadm lab: API, Celery worker, pgvector,
Redis, MinIO, and a Service+Endpoints object for **host Ollama**. Keycloak and
kube-prometheus-stack stay off until you flip their flags.

## Design (scalability, availability, security)


| Concern         | Choice                                                                                                                                                                                               |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Availability    | RollingUpdate `maxUnavailable: 0` on API; PDB when replicas ≥ 2; sticky Ingress cookie `ansibleai-upstream` so Socket.IO stays on one pod                                                            |
| Scalability     | Worker replicas are interchangeable; KEDA ScaledObject is **off** until the operator exists; HPA optional                                                                                            |
| Performance     | Requests **and** limits on every container; Redis AOF + `noeviction`; gunicorn workers stay `1` until sticky sessions are proven                                                                     |
| Security        | Dedicated SAs `*-api` / `*-worker` / `*-migrate` (never `default`); uid **10001**; drop ALL caps; read-only root + tmpfs; secrets not ConfigMaps; default-deny NetworkPolicy + DNS/data-plane allows |
| Maintainability | One image, role via `args`: `api` / `worker` / `migrate`; overlays `values-staging.yaml` / `values-prod.yaml`; migrate Job is per Helm revision (not a pre-install hook)                             |
| Inference       | Ollama on the operator laptop (`lab` / `ollama.endpoint.ip`, default `192.168.1.14:11434`). Not a cluster GPU workload                                                                               |


Lab HTTP has no TLS, so `app.env` stays `development`. `APP_ENV=staging|production` forces secure cookies and HSTS and will break NodePort HTTP (`lab.masterIp`:`lab.ingressNodePort`).

## Prerequisites

1. Phase 4a cluster (`.18` master, `.12` worker, ingress-nginx NodePort **30080/30443**).
2. Image present on **both** nodes (no registry yet):

```bash
docker build -t ansibleai/app:dev .
docker save ansibleai/app:dev | ssh master sudo ctr -n k8s.io images import -
docker save ansibleai/app:dev | ssh worker sudo ctr -n k8s.io images import -
```

1. Ollama listening on `ollama.endpoint.ip` (`192.168.1.14:11434` in this lab) with `qwen2.5-coder:7b` (and optionally `qwen2.5-coder:14b`, `nomic-embed-text`). Bind `0.0.0.0:11434` so cluster nodes can reach the laptop.
2. Helm 3.14+ and kubeconfig from `deploy/ansible/artifacts/kubeconfig`.



## Install (lab)

```bash
export KUBECONFIG=deploy/ansible/artifacts/kubeconfig

helm upgrade --install ansibleai deploy/helm/ansibleai \
  -n ansibleai --create-namespace \
  -f deploy/helm/ansibleai/values-staging.yaml \
  --timeout 15m --wait

kubectl rollout status deployment/ansibleai-api -n ansibleai --timeout=10m
kubectl get pods -n ansibleai
```

Open `http://192.168.1.18:30080` (staging sets `ingress.defaultBackendToApi` so a raw IP works). `http://ansibleai.lab:30080` still needs `/etc/hosts`. Override `lab.*` and `ollama.endpoint.ip` if the LAN map differs. Ollama must bind `0.0.0.0:11434`. Reserve `.14` / `.18` / `.12` / `.19` so DHCP cannot steal the API address.

First vector index (optional; empty index does not fail `/readyz`):

```bash
kubectl create job --from=cronjob/ansibleai-reindex ansibleai-reindex-now -n ansibleai
```

Helm smoke test (same image, hits in-cluster `/healthz`):

```bash
helm test ansibleai -n ansibleai
```



## Rollback

Documented before go-live, as required for this environment:

```bash
helm history ansibleai -n ansibleai
helm rollback ansibleai -n ansibleai
kubectl rollout status deployment/ansibleai-api -n ansibleai

# Workload-only undo
kubectl rollout undo deployment/ansibleai-api -n ansibleai
kubectl rollout undo deployment/ansibleai-worker -n ansibleai

# GitOps (once Argo CD owns the release — deploy/gitops/README.md)
argocd app history ansibleai-staging
argocd app rollback ansibleai-staging

curl -fsS -H "Host: ansibleai.lab" http://192.168.1.18:30080/healthz
```

Do not deploy `values-prod.yaml` without explicit approval, TLS, and a real Secret (`secrets.existingSecret`).

## SLO (lab)

- **SLI:** ratio of non-5xx API responses (`ansibleai_http_requests_total`).
- **SLO:** 99.5% availability over 30 days (error budget ≈ 3.6 hours). Tighten to 99.9% when production traffic exists.
- Scrape annotations are on API pods (`/metrics`). `PrometheusRule` stays disabled until the operator CRDs exist.



## What this chart does not install

- Argo CD itself (manifests are under `deploy/gitops/`; install is a lab step)
- Vault / Sealed Secrets / ESO (Phase 8)
- kube-prometheus-stack, Loki, Tempo (Phase 6c)
- vLLM / GPU Operator
- oauth2-proxy on the member Ingress (members keep in-app login)

Keycloak: set `identity.enabled=true` only after copying the realm and creating the `keycloak` database. Lab default is `AUTH_MODE=local`.