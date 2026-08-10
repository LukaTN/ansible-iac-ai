"""Validator detects collection modules from short task keys (not only FQCN in raw text)."""

from __future__ import annotations

import os
import sys

_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_BASE, "pipeline"))

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
