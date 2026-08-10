"""Ansible Jinja var_* placeholder format."""

from __future__ import annotations

from rag.generator import ansible_jinja_var


def test_ansible_jinja_var_produces_braced_var():
    assert ansible_jinja_var("allocated_storage") == "{{ var_allocated_storage }}"
