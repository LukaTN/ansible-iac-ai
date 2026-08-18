"""Guards for the Phase 4 cluster bootstrap Ansible project."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = ROOT / "deploy" / "ansible"


def test_ansible_project_layout_exists() -> None:
    required = [
        ANSIBLE / "ansible.cfg",
        ANSIBLE / "requirements.yml",
        ANSIBLE / "playbooks" / "ping.yml",
        ANSIBLE / "playbooks" / "site.yml",
        ANSIBLE / "playbooks" / "verify.yml",
        ANSIBLE / "playbooks" / "reset.yml",
        ANSIBLE / "inventories" / "lab" / "hosts.yml",
        ANSIBLE / "inventories" / "lab" / "group_vars" / "all.yml",
        ANSIBLE / "roles" / "common" / "tasks" / "main.yml",
        ANSIBLE / "roles" / "containerd" / "tasks" / "main.yml",
        ANSIBLE / "roles" / "kubernetes" / "tasks" / "main.yml",
        ANSIBLE / "roles" / "k8s_control_plane" / "tasks" / "main.yml",
        ANSIBLE / "roles" / "k8s_cni" / "tasks" / "main.yml",
        ANSIBLE / "roles" / "k8s_worker" / "tasks" / "main.yml",
        ANSIBLE / "roles" / "k8s_kubeconfig" / "tasks" / "main.yml",
        ANSIBLE / "roles" / "k8s_addons" / "tasks" / "main.yml",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    assert missing == []


def test_lab_inventory_is_two_node_cluster() -> None:
    inventory = yaml.safe_load((ANSIBLE / "inventories" / "lab" / "hosts.yml").read_text(encoding="utf-8"))
    children = inventory["all"]["children"]["k8s_cluster"]["children"]
    master = children["k8s_control_plane"]["hosts"]["k8s-master"]
    worker = children["k8s_workers"]["hosts"]["k8s-worker"]
    assert master["ansible_host"] == "192.168.1.18"
    assert worker["ansible_host"] == "192.168.1.12"
    assert master["ansible_host"] != worker["ansible_host"]
    assert inventory["all"]["vars"]["ansible_control_ip"] == "192.168.1.19"


def test_kubernetes_version_is_pinned() -> None:
    group_vars = yaml.safe_load(
        (ANSIBLE / "inventories" / "lab" / "group_vars" / "all.yml").read_text(encoding="utf-8")
    )
    assert group_vars["kubernetes_version"].startswith("1.")
    assert "latest" not in group_vars["kubernetes_version"]
    assert group_vars["k8s_pod_subnet"] == "10.244.0.0/16"
    assert not group_vars["k8s_pod_subnet"].startswith("192.168.")
    assert group_vars["calico_version"].startswith("v")


def test_site_playbook_declares_roles() -> None:
    site = yaml.safe_load((ANSIBLE / "playbooks" / "site.yml").read_text(encoding="utf-8"))
    role_names = []
    for play in site:
        for role in play.get("roles") or []:
            role_names.append(role["role"] if isinstance(role, dict) else role)
    assert role_names == [
        "common",
        "containerd",
        "kubernetes",
        "k8s_control_plane",
        "k8s_kubeconfig",
        "k8s_worker",
        "k8s_cni",
        "k8s_addons",
    ]


def test_requirements_pin_collections() -> None:
    req = yaml.safe_load((ANSIBLE / "requirements.yml").read_text(encoding="utf-8"))
    names = {item["name"] for item in req["collections"]}
    assert names == {"ansible.posix", "community.general", "kubernetes.core"}
    for item in req["collections"]:
        assert "version" in item
