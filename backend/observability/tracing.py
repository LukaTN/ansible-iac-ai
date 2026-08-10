"""
Langfuse tracing helpers (Python SDK v3).

Best practices (https://langfuse.com/docs/observability/best-practices):
  - One trace per chat turn; session_id = thread_id for multi-turn
  - Verb-first, stable observation names (no dynamic IDs in names)
  - Typed observations: agent / generation / retriever / evaluator / tool
  - Explicit, truncated I/O — never full playbook YAML or secrets
  - Model + usage on generations when the provider reports them
  - Environment via LANGFUSE_TRACING_ENVIRONMENT

No-ops when LANGFUSE_ENABLED is false or keys are missing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from config import settings

log = structlog.get_logger(__name__)

_client: Any | None = None
_client_failed = False

_MAX_PROMPT_CHARS = 4000
_MAX_OUTPUT_CHARS = 4000
_MAX_META_VALUE = 200


def _enabled() -> bool:
    return bool(
        settings.langfuse_enabled
        and settings.langfuse_public_key.strip()
        and settings.langfuse_secret_key.strip()
    )


def _sync_env() -> None:
    """Point the SDK at our settings (SDK prefers LANGFUSE_* env vars)."""
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    base = (settings.langfuse_base_url or settings.langfuse_host or "").rstrip("/")
    if base:
        os.environ["LANGFUSE_BASE_URL"] = base
        # Older docs / tools still read HOST
        os.environ.setdefault("LANGFUSE_HOST", base)
    env_name = settings.langfuse_tracing_environment or _env_from_app()
    if env_name:
        os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", env_name)


def _env_from_app() -> str:
    raw = (getattr(settings, "env", None) or "development")
    if hasattr(raw, "value"):
        raw = raw.value
    name = str(raw).strip().lower().replace(" ", "-")
    # Langfuse env regex: ^(?!langfuse)[a-z0-9-_]+$
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:40]
    if cleaned.startswith("langfuse") or not cleaned:
        return "development"
    return cleaned


def get_client() -> Any | None:
    """Lazy Langfuse client, or None when disabled / unavailable."""
    global _client, _client_failed
    if not _enabled() or _client_failed:
        return None
    if _client is not None:
        return _client
    try:
        _sync_env()
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=(settings.langfuse_base_url or settings.langfuse_host).rstrip("/"),
            environment=_env_from_app(),
            tracing_enabled=True,
        )
        log.info(
            "langfuse.client_ready",
            host=settings.langfuse_base_url or settings.langfuse_host,
            environment=_env_from_app(),
        )
        return _client
    except Exception as exc:  # noqa: BLE001
        _client_failed = True
        log.warning("langfuse.client_init_failed", error=str(exc))
        return None


def _clip(text: str | None, limit: int) -> str:
    raw = text or ""
    if len(raw) <= limit:
        return raw
    return raw[: limit - 20] + "\n…[truncated]"


def _str_meta(metadata: dict[str, Any] | None) -> dict[str, str]:
    """SDK v3/v4 prefer string metadata; keep values short."""
    out: dict[str, str] = {}
    for key, value in (metadata or {}).items():
        if value is None:
            continue
        s = str(value)
        if len(s) > _MAX_META_VALUE:
            s = s[: _MAX_META_VALUE - 1] + "…"
        out[str(key)] = s
    return out


def langchain_callback() -> Any | None:
    """
    LangGraph/LangChain CallbackHandler nested under the current agent span.

    Why: captures graph node structure automatically. LLM calls still use
    manual generations because we call Ollama/OpenRouter via raw HTTP.
    """
    if get_client() is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse.callback_init_failed", error=str(exc))
        return None


@contextmanager
def generation_trace(
    *,
    thread_id: int,
    user_id: int,
    message: str,
    task_id: str | None = None,
) -> Iterator[Any | None]:
    """
    Root observation for one chat turn (Celery generation).

    Why session_id=thread_id: groups multi-turn conversations in Sessions.
    Why as_type=agent: this orchestrates retrieve → draft → gate → respond.
    """
    client = get_client()
    if client is None:
        yield None
        return

    from langfuse import propagate_attributes

    root = None
    try:
        with client.start_as_current_observation(
            as_type="agent",
            name="generate-playbook",
            input={"message": _clip(message, 500)},
            metadata=_str_meta({
                "thread_id": thread_id,
                "task_id": task_id or "",
                "feature": "chat",
            }),
        ) as root:
            with propagate_attributes(
                user_id=str(user_id),
                session_id=str(thread_id),
                tags=["ansibleai", "chat", "generation"],
                metadata=_str_meta({
                    "thread_id": thread_id,
                    "task_id": task_id or "",
                }),
            ):
                yield root
    except Exception as exc:  # noqa: BLE001
        log.warning("langfuse.generation_trace_failed", error=str(exc))
        yield None
    finally:
        try:
            client.flush()
        except Exception:  # noqa: BLE001
            pass


def finish_generation_trace(
    root: Any | None,
    *,
    status: str,
    output_preview: str | None = None,
) -> None:
    """Set root observation output (becomes the readable trace summary)."""
    if root is None:
        return
    try:
        root.update(
            output={
                "status": status,
                "preview": _clip(output_preview, 500),
            },
            metadata=_str_meta({"status": status}),
            level="ERROR" if status in {"failed", "timeout"} else "DEFAULT",
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse.finish_trace_failed", error=str(exc))


@contextmanager
def observe(
    name: str,
    *,
    as_type: str = "span",
    input: Any | None = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
) -> Iterator[Any | None]:
    """Nested observation under the current generation (no-op when disabled)."""
    client = get_client()
    if client is None:
        yield None
        return
    kwargs: dict[str, Any] = {
        "as_type": as_type,
        "name": name,
        "metadata": _str_meta(metadata),
    }
    if input is not None:
        kwargs["input"] = input
    if model and as_type == "generation":
        kwargs["model"] = model
    try:
        with client.start_as_current_observation(**kwargs) as obs:
            yield obs
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse.observe_failed", name=name, error=str(exc))
        yield None


def observe_llm_call(
    *,
    model: str,
    provider: str,
    prompt: str,
    system: str | None,
    output: str | None,
    duration_s: float,
    status: str = "ok",
    usage: dict[str, int] | None = None,
) -> None:
    """
    Record one LLM completion as a generation.

    Input uses OpenAI message shape so the Langfuse UI renders a chat view.
    """
    client = get_client()
    if client is None:
        return

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": _clip(system, 1000)})
    messages.append({"role": "user", "content": _clip(prompt, _MAX_PROMPT_CHARS)})

    usage_details = None
    if usage:
        usage_details = {
            k: int(v)
            for k, v in usage.items()
            if v is not None and k in {"input", "output", "total", "prompt_tokens", "completion_tokens"}
        }
        # Normalize legacy keys → Langfuse usage_details
        if "prompt_tokens" in usage_details and "input" not in usage_details:
            usage_details["input"] = usage_details.pop("prompt_tokens")
        if "completion_tokens" in usage_details and "output" not in usage_details:
            usage_details["output"] = usage_details.pop("completion_tokens")

    try:
        with client.start_as_current_observation(
            as_type="generation",
            name="generate-response",
            model=model,
            input=messages,
            metadata=_str_meta({
                "provider": provider,
                "status": status,
                "duration_s": f"{duration_s:.3f}",
            }),
        ) as gen:
            update_kwargs: dict[str, Any] = {
                "output": _clip(output, _MAX_OUTPUT_CHARS) if output else None,
                "level": "ERROR" if status != "ok" else "DEFAULT",
            }
            if usage_details:
                update_kwargs["usage_details"] = usage_details
            gen.update(**update_kwargs)
    except Exception as exc:  # noqa: BLE001
        log.debug("langfuse.generation_failed", error=str(exc))


# ── Back-compat aliases used by earlier Phase 6a wiring ─────────

def start_generation_trace(
    *,
    thread_id: int,
    user_id: int,
    message: str,
    task_id: str | None = None,
) -> Any | None:
    """Deprecated: prefer `generation_trace` context manager."""
    return None


def end_trace(
    *,
    status: str,
    output_preview: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Deprecated: prefer `finish_generation_trace`."""
    _ = (status, output_preview, metadata)
    client = get_client()
    if client is not None:
        try:
            client.flush()
        except Exception:  # noqa: BLE001
            pass


@contextmanager
def trace_span(name: str, metadata: dict[str, Any] | None = None) -> Iterator[Any | None]:
    with observe(name, as_type="span", metadata=metadata) as obs:
        yield obs
