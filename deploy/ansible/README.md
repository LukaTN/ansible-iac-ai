# Cluster bootstrap (Ansible)

Provisions a **two-node kubeadm Kubernetes cluster** from a dedicated **Ansible
control VM**. It does **not** deploy AnsibleAI (Helm comes in Phase 4).

## Your lab topology

| IP | Host | Role |
|----|------|------|
| **192.168.1.19** | `ansible-control` | Run `ansible-playbook` here (not a K8s node) |
| **192.168.1.18** | `k8s-master` | Control plane (`kubeadm init`) |
| **192.168.1.12** | `k8s-worker` | Worker (`kubeadm join`) |

```
 192.168.1.19                    192.168.1.18              192.168.1.12
┌──────────────────┐   SSH    ┌─────────────────┐  6443  ┌─────────────────┐
│ ansible-control  │────────►│   k8s-master    │◄───────│   k8s-worker    │
│ (this repo)      │         │  control plane  │        │  worker         │
└──────────────────┘         └─────────────────┘        └─────────────────┘
        │                              ▲
        │   kubeconfig copied          │ kubectl / Helm addons
        └──────────────────────────────┘
              artifacts/kubeconfig
```

Ollama stays **off** the cluster (host or another URL), per the production plan.

## Stack (full Kubernetes, not k3s)

| Layer | Component |
|-------|-----------|
| CRI | containerd (SystemdCgroup) |
| Control plane | kubeadm init on `.18` |
| Node agent | kubelet |
| CNI | Calico (pinned manifest) |
| Ingress | ingress-nginx (Helm) |
| TLS automation | cert-manager (Helm) |

## Layout

```
deploy/ansible/
├── ansible.cfg
├── requirements.yml
├── inventories/lab/
│   ├── hosts.yml              # master .18 + worker .12
│   ├── group_vars/all.yml
│   └── host_vars/
├── playbooks/
│   ├── ping.yml               # SSH check from .19
│   ├── site.yml               # create cluster
│   ├── verify.yml
│   └── reset.yml
├── roles/
│   ├── common/
│   ├── containerd/
│   ├── kubernetes/
│   ├── k8s_control_plane/
│   ├── k8s_cni/
│   ├── k8s_worker/
│   ├── k8s_kubeconfig/
│   └── k8s_addons/
└── artifacts/                 # kubeconfig on .19 (gitignored)
```

## Prerequisites on 192.168.1.19 (Ansible control)

```bash
sudo apt-get update
sudo apt-get install -y python3-pip git openssh-client
pip3 install --user 'ansible-core>=2.16,<2.19'
ansible-galaxy collection install -r requirements.yml -p collections
```

Clone this repo on **192.168.1.19**, then work in `deploy/ansible`.

## Prerequisites on .18 and .12 (cluster nodes)

- Ubuntu 22.04/24.04 or Debian 12
- SSH access from **192.168.1.19** as a sudo user (`ubuntu` by default in inventory)
- Python 3
- Worker must reach master on TCP **6443**
- No GPU required
- **2 GB+ RAM** on control plane recommended for kubeadm + Calico

Passwordless SSH from .19 (example):

```bash
# On 192.168.1.19
ssh-copy-id ubuntu@192.168.1.18
ssh-copy-id ubuntu@192.168.1.12
```

Override `ansible_user` or `ansible_ssh_private_key_file` in
`inventories/lab/host_vars/k8s-master.yml` or `k8s-worker.yml` if needed.

## Configure

1. IPs are already set in `inventories/lab/hosts.yml` (`.18` master, `.12` worker).
2. Pin versions in `inventories/lab/group_vars/all.yml` (`kubernetes_version`, `calico_version`).
3. Keep `k8s_pod_subnet` off the lab LAN. `10.244.0.0/16` is the lab default; do **not** use Calico's `192.168.0.0/16` on a `192.168.1.0/24` network.
4. Optional: add extra API names to `k8s_api_server_cert_sans`.

## Run (on 192.168.1.19)

```bash
cd deploy/ansible

ansible-playbook playbooks/ping.yml
ansible-playbook playbooks/site.yml --syntax-check
ansible-playbook playbooks/site.yml
ansible-playbook playbooks/verify.yml
```

After success, **two** Ready nodes:

```bash
export KUBECONFIG="$PWD/artifacts/kubeconfig"
kubectl get nodes
# k8s-master   Ready   control-plane
# k8s-worker   Ready   <none>
```

Skip ingress-nginx / cert-manager:

```bash
ansible-playbook playbooks/site.yml --skip-tags addons
```

Tear down kubeadm on `.18` and `.12`:

```bash
ansible-playbook playbooks/reset.yml
```

## What gets installed

| Component | Where | Notes |
|-----------|--------|--------|
| containerd + kubelet | `.18`, `.12` | Pinned from pkgs.k8s.io |
| kubeadm control plane | 192.168.1.18 | API on `:6443` |
| Calico CNI | `.18` + `.12` | Pod network `10.244.0.0/16` (does not overlap `192.168.1.0/24`) |
| kubeadm worker join | 192.168.1.12 | Joins `https://192.168.1.18:6443` |
| ingress-nginx | `.18` / `.12` | NodePort **30080** (HTTP) and **30443** (HTTPS); no cloud LoadBalancer |
| cert-manager | `.18` | Pinned chart version |
| kubeconfig | 192.168.1.19 | `artifacts/kubeconfig` |

## Windows laptop

Use **192.168.1.19** as the Ansible machine, or SSH into it from your PC. Do not
run `ansible-playbook` from native Windows Python against the cluster.
