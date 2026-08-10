"""Guards for agent prompt templates (v2 prompt-engineer pass)."""

from __future__ import annotations

import re

from agent.prompts import (
    PROMPT_VERSION,
    REASON_PROMPT,
    REPAIR_PROMPT,
    RESPOND_PROMPT,
    build_playbook_system_prompt,
)


def test_prompt_version_is_v2():
    assert PROMPT_VERSION == "v2"


def test_playbook_system_preserves_ansible_jinja():
    """str.format used to collapse ``{{ var }}`` → ``{ var }`` — must not regress."""
    text = build_playbook_system_prompt("amazon.aws")
    assert "{{ var_image_id }}" in text
    assert "{{ var_aws_region }}" in text
    # Single-brace Jinja is invalid for Ansible templating.
    assert not re.search(r"(?<!\{)\{ var_[a-z0-9_]+ \}", text)


def test_playbook_system_includes_pre_emit_checklist():
    text = build_playbook_system_prompt("ansible.builtin")
    assert "Pre-emit checklist" in text
    assert "FQCN" in text


def test_reason_prompt_formats_and_keeps_jinja_guidance():
    prompt = REASON_PROMPT.format(
        history="u: hi",
        message="create an rds instance",
        pinned_collection="none",
        known_collections="amazon.aws",
        intent_guess="generate",
    )
    assert "create an rds instance" in prompt
    assert "{{ var_x }}" in prompt
    assert "{history}" not in prompt


def test_repair_and_respond_format_keys():
    repair = REPAIR_PROMPT.format(
        message="m",
        primary_module="amazon.aws.rds_instance",
        primary_collection="amazon.aws",
        draft_yaml="---\n- hosts: localhost\n",
        failures="- fqcn missing",
    )
    assert "fqcn missing" in repair
    respond = RESPOND_PROMPT.format(
        history="",
        message="hello",
        intent="chat",
        generated_flag="false",
        gate_summary="n/a",
        primary_module="none",
        tool_results="none",
    )
    assert "hello" in respond
