"""Langfuse prompt fetch with a git fallback (Phase 6b).

Playbook text is returned as a raw blob. Never call ``.compile()`` —
Ansible Jinja ``{{ var }}`` would be treated as Langfuse variables.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import structlog
from observability.tracing import get_client

log = structlog.get_logger(__name__)

PRODUCTION_LABEL = "production"

AGENT_SYSTEM_NAME = "ansibleai-agent-system"
PLAYBOOK_SYSTEM_NAME = "ansibleai-playbook-system"


@dataclass(frozen=True)
class PromptRef:
    name: str
    version: str
    source: str


_last_ref: ContextVar[PromptRef | None] = ContextVar("ansibleai_prompt_ref", default=None)


def last_prompt_ref() -> dict[str, str] | None:
    """Metadata for the last get_prompt_text() in this context (trace tags)."""
    ref = _last_ref.get()
    if ref is None:
        return None
    return {
        "prompt_name": ref.name,
        "prompt_version": ref.version,
        "prompt_source": ref.source,
    }


def _remember(name: str, version: str, source: str) -> None:
    _last_ref.set(PromptRef(name=name, version=version, source=source))


def _raw_text(obj: Any) -> str:
    raw = getattr(obj, "prompt", None)
    if isinstance(raw, str) and raw.strip():
        return raw
    if isinstance(raw, list):
        for msg in raw:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "system" and str(msg.get("content") or "").strip():
                return str(msg["content"])
        parts = []
        for msg in raw:
            if isinstance(msg, dict) and msg.get("content"):
                parts.append(str(msg["content"]))
        if parts:
            return "\n\n".join(parts)
    return ""


def get_prompt_text(
    name: str,
    git_fallback: str,
    *,
    label: str = PRODUCTION_LABEL,
) -> str:
    """Return Langfuse text labeled ``production``, else ``git_fallback``.

    Failures (disabled client, missing prompt, network) never raise.
    """
    client = get_client()
    if client is None:
        _remember(name, "git", "git")
        return git_fallback
    try:
        obj = client.get_prompt(name, label=label)
        text = _raw_text(obj)
        if text:
            version = str(getattr(obj, "version", None) or label)
            _remember(name, version, "langfuse")
            log.debug("prompt_registry.hit", name=name, label=label, version=version)
            return text
        log.warning("prompt_registry.empty", name=name, label=label)
    except Exception as exc:
        log.warning("prompt_registry.fetch_failed", name=name, error=str(exc))
    _remember(name, "git", "git")
    return git_fallback
