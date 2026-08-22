"""Phase 6b — Langfuse prompt registry falls back to git; never compile()."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.prompt_registry import _raw_text, get_prompt_text
from agent.prompts import AGENT_SYSTEM, agent_system_prompt, build_playbook_system_prompt


def test_git_fallback_when_langfuse_disabled(monkeypatch):
    import agent.prompt_registry as reg

    monkeypatch.setattr(reg, "get_client", lambda: None)
    assert get_prompt_text("ansibleai-agent-system", "GIT") == "GIT"
    ref = reg.last_prompt_ref()
    assert ref is not None
    assert ref["prompt_name"] == "ansibleai-agent-system"
    assert ref["prompt_source"] == "git"


def test_uses_langfuse_raw_blob_and_never_compiles(monkeypatch):
    import agent.prompt_registry as reg

    obj = MagicMock()
    obj.prompt = "FROM LANGFUSE — keep {{ ansible_var }}"
    obj.compile = MagicMock(side_effect=AssertionError("must not compile"))
    client = MagicMock()
    client.get_prompt.return_value = obj
    monkeypatch.setattr(reg, "get_client", lambda: client)

    text = get_prompt_text("ansibleai-playbook-system", "GIT")
    assert text == "FROM LANGFUSE — keep {{ ansible_var }}"
    obj.compile.assert_not_called()
    client.get_prompt.assert_called_once_with(
        "ansibleai-playbook-system", label="production"
    )


def test_fallback_on_fetch_error(monkeypatch):
    import agent.prompt_registry as reg

    client = MagicMock()
    client.get_prompt.side_effect = RuntimeError("timeout")
    monkeypatch.setattr(reg, "get_client", lambda: client)
    assert get_prompt_text("missing", "GIT") == "GIT"


def test_empty_langfuse_prompt_falls_back(monkeypatch):
    import agent.prompt_registry as reg

    obj = MagicMock()
    obj.prompt = "   "
    client = MagicMock()
    client.get_prompt.return_value = obj
    monkeypatch.setattr(reg, "get_client", lambda: client)
    assert get_prompt_text("empty", "GIT") == "GIT"


def test_raw_text_prefers_system_message_in_chat_prompt():
    obj = MagicMock()
    obj.prompt = [
        {"role": "system", "content": "SYSTEM {{ var_region }}"},
        {"role": "user", "content": "hello"},
    ]
    assert _raw_text(obj) == "SYSTEM {{ var_region }}"


def test_raw_text_joins_chat_messages_when_no_system():
    obj = MagicMock()
    obj.prompt = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]
    assert _raw_text(obj) == "one\n\ntwo"


def test_chat_prompt_from_langfuse_does_not_compile(monkeypatch):
    import agent.prompt_registry as reg

    obj = MagicMock()
    obj.prompt = [{"role": "system", "content": "Keep {{ hosts }}"}]
    obj.compile = MagicMock(side_effect=AssertionError("must not compile"))
    client = MagicMock()
    client.get_prompt.return_value = obj
    monkeypatch.setattr(reg, "get_client", lambda: client)
    assert get_prompt_text("chat", "GIT") == "Keep {{ hosts }}"
    obj.compile.assert_not_called()


def test_agent_system_prompt_defaults_to_git():
    assert agent_system_prompt() == AGENT_SYSTEM


def test_playbook_prompt_still_preserves_jinja():
    text = build_playbook_system_prompt("amazon.aws")
    assert "{{ var_image_id }}" in text
    assert "{collection_rules}" not in text
