"""
=============================================================
  AnsibleAI Agent — LLM client

  Ollama wrapper for the agent (planner + clarifier + synthesizer)
  and playbook YAML generation.

  Playbook generation uses the same model by default (`AGENT_MODEL`).
  Override with PLAYBOOK_MODEL / PLAYBOOK_MAX_TOKENS / PLAYBOOK_TEMPERATURE
  for a dedicated code model without changing the planner.

  Env vars:
    AGENT_MODEL          Ollama model tag           (default: qwen2.5-coder:7b)
    PLAYBOOK_MODEL       optional; defaults to AGENT_MODEL
    PLAYBOOK_MAX_TOKENS  default: 3500
    OLLAMA_BASE_URL      default: http://localhost:11434
=============================================================
"""

from __future__ import annotations

import os
import time

import requests
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from logging_setup import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────

AGENT_MODEL = os.getenv("AGENT_MODEL", "qwen2.5-coder:7b")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

# How long Ollama holds the weights in memory after a call. The default of
# 5 minutes means an idle user pays a full cold load (45 s for a 12B on a
# 6 GB card) on their next message. Use "-1" to pin the model permanently.
OLLAMA_KEEP_ALIVE = (os.getenv("OLLAMA_KEEP_ALIVE") or "30m").strip()

# Ask responses to be shorter by default; planner/synthesizer override this as needed.
DEFAULT_MAX_TOKENS = 900

REQUEST_TIMEOUT = int(os.getenv("AGENT_REQUEST_TIMEOUT", "300"))


class LLMError(RuntimeError):
    """Raised when the Ollama call fails."""


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────

def chat(
    prompt: str,
    *,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    model: str | None = None,
    expect_json: bool = False,
) -> str:
    """
    Send a single-turn prompt to the agent LLM and return the text response.

    `system` : optional system message.
    `expect_json` : caller hint; the planner parser already accepts JSON in
    prose or fenced blocks, so Ollama is not asked for a JSON response_format.
    """
    # Cooperative cancel: stop before starting a long LLM round-trip.
    from .cancel import check as check_cancelled
    check_cancelled()

    model = (model or AGENT_MODEL).strip()
    t0 = time.perf_counter()
    status = "ok"
    content: str | None = None
    usage: dict[str, int | None] | None = None

    try:
        content, usage = _call_ollama(
            prompt, system=system, temperature=temperature,
            max_tokens=max_tokens, model=model,
        )
        return content
    except Exception:
        status = "error"
        raise
    finally:
        duration_s = time.perf_counter() - t0
        try:
            from observability.metrics import record_llm_call
            from observability.tracing import observe_llm_call

            prompt_tokens = (usage or {}).get("input") if usage else None
            completion_tokens = (usage or {}).get("output") if usage else None
            record_llm_call(
                provider="ollama",
                model=model,
                status=status,
                duration_s=duration_s,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            observe_llm_call(
                model=model,
                provider="ollama",
                prompt=prompt,
                system=system,
                output=content,
                duration_s=duration_s,
                status=status,
                usage={
                    k: int(v)
                    for k, v in (usage or {}).items()
                    if v is not None
                } or None,
            )
            total_tokens = 0
            if usage:
                total_tokens = int(usage.get("total") or 0)
                if not total_tokens:
                    total_tokens = int(usage.get("input") or 0) + int(usage.get("output") or 0)
            if total_tokens:
                from auth.budgets import add_usage

                add_usage(total_tokens)
        except Exception:  # noqa: BLE001
            pass


def _call_ollama(
    prompt: str, *, system: str | None, temperature: float,
    max_tokens: int, model: str,
) -> tuple[str, dict[str, int | None]]:
    payload = {
        "model"     : model,
        "prompt"    : prompt,
        "system"    : system or "",
        "stream"    : False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options"   : {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    try:
        log.info(
            "agent.llm.ollama.request",
            model=model,
            max_tokens=max_tokens,
            timeout=REQUEST_TIMEOUT,
            prompt_chars=len(prompt),
        )
        # Celery prefork children often hide structlog; this line is what
        # shows up in `docker compose logs worker` while a call is in flight.
        print(
            f"[ollama] calling {model} (timeout={REQUEST_TIMEOUT}s) …",
            flush=True,
        )
        started = time.perf_counter()
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload, timeout=REQUEST_TIMEOUT,
        )
        print(
            f"[ollama] {model} -> HTTP {r.status_code} in {time.perf_counter() - started:.1f}s",
            flush=True,
        )
    except requests.RequestException as e:
        print(f"[ollama] {model} FAILED: {e}", flush=True)
        raise LLMError(f"Ollama request failed: {e}") from e

    if r.status_code >= 400:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:300]}")

    try:
        data = r.json()
        content = (data.get("response") or "").strip()
    except ValueError as e:
        raise LLMError(f"Ollama returned invalid JSON: {e}") from e

    return content, {
        "input": data.get("prompt_eval_count"),
        "output": data.get("eval_count"),
    }


def current_config() -> dict:
    """Return a small summary for debugging (no secrets)."""
    return {
        "provider" : "ollama",
        "model"    : AGENT_MODEL,
        "base_url" : OLLAMA_BASE_URL,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }


def warm_up(timeout: float = 120.0) -> dict:
    """
    Load the agent + playbook models into memory ahead of the first request.

    Ollama loads weights lazily, so without this the first user of the day
    waits out the cold load inside their own request. Never raises — a failed
    warm-up just means the first request pays the usual price.
    """
    models: list[str] = []
    for m in (AGENT_MODEL, (os.getenv("PLAYBOOK_MODEL") or "").strip()):
        if m and m not in models:
            models.append(m)

    warmed: list[str] = []
    for m in models:
        started = time.perf_counter()
        try:
            # num_predict=0 loads the weights without generating anything.
            r = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": m, "prompt": "", "stream": False,
                      "keep_alive": OLLAMA_KEEP_ALIVE, "options": {"num_predict": 0}},
                timeout=timeout,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("agent.llm.warm_up.failed", model=m, error=str(e))
            continue
        warmed.append(m)
        log.info("agent.llm.warm_up.ok", model=m, seconds=round(time.perf_counter() - started, 1))

    return {"warmed": warmed}
