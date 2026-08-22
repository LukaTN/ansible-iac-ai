"""Validator detects collection modules from short task keys (not only FQCN in raw text)."""

from __future__ import annotations

import os
import sys

_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_BASE, "backend", "pipeline"))

from validator import ValidationResult, check_module_present


def test_check_module_present_matches_short_task_key():
    r = ValidationResult("/tmp/playbook.yml")
    r.raw_yaml = """
- hosts: localhost
  tasks:
    - name: create aks
      azure_rm_aks:
        name: mycluster
        resource_group: rg1
"""
    kb = {
        "azure_rm_aks_module": {"module": "azure.azcollection.azure_rm_aks"},
    }
    check_module_present(r, kb)
    assert r._detected_module == "azure.azcollection.azure_rm_aks"


def test_check_module_present_prefers_fqcn_when_both_appear():
    r = ValidationResult("/tmp/playbook.yml")
    r.raw_yaml = """
- hosts: localhost
  tasks:
    - azure.azcollection.azure_rm_aks:
        name: x
"""
    kb = {
        "azure_rm_aks_module": {"module": "azure.azcollection.azure_rm_aks"},
    }
    check_module_present(r, kb)
    assert r._detected_module == "azure.azcollection.azure_rm_aks"


def test_gather_facts_play_key_does_not_override_ec2_module():
    """Play-level ``gather_facts: no`` must not match ansible.builtin.gather_facts."""
    r = ValidationResult("/tmp/playbook.yml")
    r.raw_yaml = """
---
- name: Create 2 Amazon EC2 Instances
  hosts: localhost
  connection: local
  gather_facts: no
  collections:
    - amazon.aws
  tasks:
    - name: Create first EC2 instance
      amazon.aws.ec2_instance:
        state: present
        name: "instance-1"
"""
    kb = {
        "ec2_instance_module": {"module": "amazon.aws.ec2_instance"},
        "gather_facts_module": {"module": "ansible.builtin.gather_facts"},
    }
    check_module_present(r, kb)
    assert r._detected_module == "amazon.aws.ec2_instance"


_AZURE_VM_PLAYBOOK = """
---
- name: Create an Azure virtual machine
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    resource_group: "rg-production"
    vm_name: "vm-api"
  tasks:
    - name: Create virtual machine
      azure.azcollection.azure_rm_virtualmachine:
        resource_group: "{{ resource_group }}"
        name: "{{ vm_name }}"
        state: present
"""


def test_azure_vm_fqcn_detected_from_parsed_kb_even_if_cwd_is_wrong(tmp_path, monkeypatch):
    """Regression: after the backend/ layout move, validator chdir'd into
    backend/ and kb_store looked for relative data/parsed there. The gate
    then failed with "No known collection module detected" on a correct
    azure.azcollection.azure_rm_virtualmachine playbook (ansible-lint still
    passed because Galaxy collections were installed).
    """
    from kb_store import load_knowledge_base

    monkeypatch.chdir(tmp_path)
    kb = load_knowledge_base()
    modules = {
        entry.get("module")
        for entry in (kb.get("modules") or {}).values()
        if entry.get("module")
    }
    if "azure.azcollection.azure_rm_virtualmachine" not in modules:
        import pytest

        pytest.skip("data/parsed does not include azure.azcollection.azure_rm_virtualmachine")

    r = ValidationResult("/tmp/playbook.yml")
    r.raw_yaml = _AZURE_VM_PLAYBOOK
    check_module_present(r, kb["modules"])
    assert r._detected_module == "azure.azcollection.azure_rm_virtualmachine"
    assert r.errors == []
