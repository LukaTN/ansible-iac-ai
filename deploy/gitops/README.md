# Phase 7 — GitOps (Argo CD)

Delivery is git, not `kubectl apply` of the app on a laptop. The Helm chart
stays the source of truth; these Applications point Argo at it.

| Application | Namespace | Sync | Values |
|-------------|-----------|------|--------|
| `ansibleai-staging` | `ansibleai` | automated prune + selfHeal | `values-staging.yaml` + `values-gitops-image.yaml` |
| `ansibleai-production` | `ansibleai-prod` | **manual** | `values-prod.yaml` + `values-gitops-image.yaml` |

Image tags come from [../helm/ansibleai/values-gitops-image.yaml](../helm/ansibleai/values-gitops-image.yaml).
Never `latest`. Bump after CI:

```bash
python scripts/set_gitops_image.py --tag "$GIT_SHA" --repository ghcr.io/lukatn/ansible-iac-ai
```

The lab can keep `ansibleai/app:<sha>` and `ctr -n k8s.io images import` on
**both** `.18` and `.12` until a pull-secret for GHCR exists.

## Install Argo CD (kubeadm lab, from `.19`)

Pin the install manifest. Do not use an unversioned URL.

```bash
export KUBECONFIG=deploy/ansible/artifacts/kubeconfig

kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.14.11/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=10m

# UI (do not publish this NodePort on an untrusted LAN)
kubectl -n argocd patch svc argocd-server -p '{"spec":{"type":"NodePort"}}'
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

If the app is already a Helm release named `ansibleai` in `ansibleai`, the
staging Application uses `helm.releaseName: ansibleai` so Argo can adopt it.

```bash
kubectl apply -f deploy/gitops/applications/staging.yaml
# Production Application is registered but not auto-synced:
# kubectl apply -f deploy/gitops/applications/production.yaml
```

`repoURL` is `https://github.com/LukaTN/ansible-iac-ai.git`. Change it if you
fork. Argo needs read access to that repo (public, or a repo credential).

## Promote

1. `ci.yml` + `image.yml` go green; image is `ghcr.io/<owner>/<repo>:<git-sha>`.
2. Load or pull that SHA onto both kubeadm nodes (or add an `imagePullSecret`).
3. `python scripts/set_gitops_image.py --tag <git-sha> …` and push to `main`.
4. Staging self-heals. Watch: `argocd app wait ansibleai-staging --health`.
5. Eval gate (not on GitHub-hosted runners):

```bash
# Compose (already proven in Phase 6b)
python scripts/lab_eval_gate.py --live --mode pipeline --suite core

# Cluster Ingress (self-hosted runner label "lab", or from the laptop)
python scripts/lab_eval_gate.py --live --mode api \
  --base-url http://192.168.1.18:30080 --suite core
```

6. Production stays **manual** (`argocd app sync ansibleai-production`) until
   those scores beat `evals/baselines/golden.json`. Do not apply
   `values-prod.yaml` on HTTP NodePort (secure cookies / HSTS).

## Rollback

```bash
# Argo (preferred once GitOps owns the release)
argocd app history ansibleai-staging
argocd app rollback ansibleai-staging

# Helm still works if you have not moved off it
helm history ansibleai -n ansibleai
helm rollback ansibleai -n ansibleai
kubectl rollout undo deployment/ansibleai-api -n ansibleai
kubectl rollout status deployment/ansibleai-api -n ansibleai
```

Rolling is the only strategy (`maxUnavailable: 0` on the API). No canary,
no service mesh.

## What this does not install

- Harbor (GHCR is the default registry)
- Argo Rollouts / Image Updater
- Preview ApplicationSets for PRs
- Loki / Tempo (Phase 6c)
- Vault / Sealed Secrets (Phase 8)
