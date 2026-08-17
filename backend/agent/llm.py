"""
=============================================================
  AnsibleAI Agent — LLM client

  Provider-agnostic wrapper for the LLM used by the AGENT
  (planner + clarifier + synthesizer + playbook YAML generation).

  Playbook generation uses the same stack by default (`AGENT_MODEL`).
  Override with PLAYBOOK_MODEL / PLAYBOOK_MAX_TOKENS / PLAYBOOK_TEMPERATURE
  for a dedicated code model without changing the planner.

  Supported providers (chosen via AGENT_LLM_PROVIDER env var):
    - "openrouter" (default)  →  OpenAI-compatible API
    - "ollama"                 →  local Ollama

  Env vars:
    AGENT_LLM_PROVIDER   openrouter | ollama          (default: openrouter)
    AGENT_MODEL          model id                      (default: google/gemma-4-31b-it:free)
    PLAYBOOK_MODEL       optional; defaults to AGENT_MODEL
    PLAYBOOK_MAX_TOKENS  default: 3500
    OPENROUTER_API_KEY   required for openrouter
    OPENROUTER_BASE_URL  default: https://openrouter.ai/api/v1
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

PROVIDER     = (os.getenv("AGENT_LLM_PROVIDER", "openrouter") or "openrouter").lower()
AGENT_MODEL  = os.getenv("AGENT_MODEL", "google/gemma-4-31b-it:free")

OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

# How long Ollama holds the weights in memory after a call. The default of
# 5 minutes means an idle user pays a full cold load (45 s for a 12B on a
# 6 GB card) on their next message. Use "-1" to pin the model permanently.
OLLAMA_KEEP_ALIVE = (os.getenv("OLLAMA_KEEP_ALIVE") or "30m").strip()

REFERER  = os.getenv("OPENROUTER_REFERER",  "http://localhost:5000")
APP_NAME = os.getenv("OPENROUTER_APP_NAME", "AnsibleAI")

# Ask responses to be shorter by default; planner/synthesizer override this as needed.
DEFAULT_MAX_TOKENS = 900

REQUEST_TIMEOUT = int(os.getenv("AGENT_REQUEST_TIMEOUT", "300"))

# ── Fallback chain for OpenRouter ──────────────────────────────────
#
# Free-tier OpenRouter models frequently 429 when the upstream provider
# throttles. We try the primary AGENT_MODEL first, then walk this list
# in order until one succeeds. Override via AGENT_FALLBACK_MODELS in
# .env (comma-separated slugs).
#
_DEFAULT_FALLBACKS = (
    "google/gemma-3-27b-it:free",                # Gemma 3 — same family, different quota
    "qwen/qwen3-next-80b-a3b-instruct:free",     # non-Google, 80B MoE
    "meta-llama/llama-3.3-70b-instruct:free",    # non-Google, 70B dense
    "nvidia/nemotron-3-super-120b-a12b:free",    # high-quality last resort
)


def _parse_fallback_env() -> tuple[str, ...]:
    raw = os.getenv("AGENT_FALLBACK_MODELS", "").strip()
    if not raw:
        return _DEFAULT_FALLBACKS
    return tuple(m.strip() for m in raw.split(",") if m.strip())


AGENT_FALLBACK_MODELS = _parse_fallback_env()

# HTTP statuses that mean "try a different model" rather than fail hard.
#  404 — OpenRouter often returns 404 when a :free slug has rotated off
#        the free tier; the next model in the chain may still work.
#  408 / 5xx — transient capacity / upstream failures.
#  429 — rate limit on the specific model (very common on :free tier).
_FALLBACK_STATUSES = (404, 408, 429, 500, 502, 503, 504)


class LLMError(RuntimeError):
    """Raised when the agent LLM call fails across ALL fallbacks."""


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
    `expect_json` : hints the provider to return JSON (when supported).
    """
    # Cooperative cancel: stop before starting a long LLM round-trip.
    from .cancel import check as check_cancelled
    check_cancelled()

    provider = PROVIDER
    model    = (model or AGENT_MODEL).strip()
    t0 = time.perf_counter()
    status = "ok"
    content: str | None = None
    usage: dict[str, int | None] | None = None

    try:
        if provider == "openrouter":
            if not OPENROUTER_API_KEY:
                raise LLMError(
                    "OPENROUTER_API_KEY is not set. Add it to your .env or set "
                    "AGENT_LLM_PROVIDER=ollama to use a local model instead."
                )
            content, usage = _call_openrouter_with_fallback(
                prompt, system=system, temperature=temperature,
                max_tokens=max_tokens, primary_model=model, expect_json=expect_json,
            )
            return content

        if provider == "ollama":
            content, usage = _call_ollama(
                prompt, system=system, temperature=temperature,
                max_tokens=max_tokens, model=model,
            )
            return content

        raise LLMError(f"Unknown AGENT_LLM_PROVIDER: {provider!r}")
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
                provider=provider,
                model=model,
                status=status,
                duration_s=duration_s,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            observe_llm_call(
                model=model,
                provider=provider,
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


# ─────────────────────────────────────────────
#  OpenRouter (OpenAI-compatible)
# ─────────────────────────────────────────────

class _TransientLLMError(Exception):
    """Retryable error: same model couldn't answer, try the next one."""


def _build_model_chain(primary: str) -> list[str]:
    """Primary + fallbacks, deduplicated while preserving order."""
    chain: list[str] = []
    for m in (primary, *AGENT_FALLBACK_MODELS):
        if m and m not in chain:
            chain.append(m)
    return chain


def _call_openrouter_with_fallback(
    prompt: str, *, system: str | None, temperature: float,
    max_tokens: int, primary_model: str, expect_json: bool,
) -> tuple[str, dict[str, int | None]]:
    """
    Try the primary model first. On transient failure (429 / 5xx / network),
    walk the configured fallback chain. If EVERY model fails, raise LLMError
    with the final error so the caller can decide what to do.
    """
    chain = _build_model_chain(primary_model)
    last_error: str = ""

    for idx, model in enumerate(chain):
        try:
            if idx > 0:
                # Brief backoff between provider switches.
                time.sleep(min(1.0 * idx, 3.0))
                log.warning("agent.llm.fallback", model=model, attempt=idx + 1)
            return _call_openrouter_once(
                prompt, system=system, temperature=temperature,
                max_tokens=max_tokens, model=model, expect_json=expect_json,
            )
        except _TransientLLMError as e:
            last_error = str(e)
            log.warning("agent.llm.transient_error", model=model, error=str(e))
            continue
        except LLMError:
            # Non-transient (auth, bad request, etc.) — don't retry elsewhere.
            raise

    raise LLMError(
        f"All {len(chain)} OpenRouter models were unavailable. "
        f"Last error: {last_error or 'unknown'}"
    )


def _supports_response_format(model: str) -> bool:
    """Providers known to reject `response_format: json_object` on the free tier."""
    m = (model or "").lower()
    if "gemma" in m:
        return False
    return True


def _supports_system_message(model: str) -> bool:
    """
    Gemma 3 on Google AI Studio's free tier rejects OpenAI-style `system`
    messages with `"Developer instruction is not enabled"`. We merge the
    system text into the first user message for any Gemma model so the
    call still goes through.
    """
    m = (model or "").lower()
    if "gemma" in m:
        return False
    return True


def _call_openrouter_once(
    prompt: str, *, system: str | None, temperature: float,
    max_tokens: int, model: str, expect_json: bool,
) -> tuple[str, dict[str, int | None]]:
    messages: list[dict] = []
    if system and _supports_system_message(model):
        messages.append({"role": "system", "content": system})
        messages.append({"role": "user",   "content": prompt})
    elif system:
        messages.append({
            "role"   : "user",
            "content": f"{system.strip()}\n\n---\n\n{prompt}",
        })
    else:
        messages.append({"role": "user", "content": prompt})

    payload: dict = {
        "model"      : model,
        "messages"   : messages,
        "temperature": temperature,
        "max_tokens" : max_tokens,
    }
    # `response_format: json_object` is silently rejected or 400'd by
    # several providers on the :free tier (Gemma in particular). Our
    # planner parser (`_parse_plan`) already tolerates JSON wrapped in
    # prose or ```json fences, so we only set it where it's known to work.
    if expect_json and _supports_response_format(model):
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type" : "application/json",
        # OpenRouter asks for these two for attribution / rate-limit tiering.
        "HTTP-Referer" : REFERER,
        "X-Title"      : APP_NAME,
    }

    try:
        r = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers, json=payload, timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise _TransientLLMError(f"network error: {e}") from e

    if r.status_code in _FALLBACK_STATUSES:
        snippet = (r.text or "").strip()[:300]
        raise _TransientLLMError(f"{r.status_code} {snippet}")

    if r.status_code >= 400:
        snippet = (r.text or "").strip()[:400]
        raise LLMError(f"OpenRouter {r.status_code}: {snippet}")

    try:
        data = r.json()
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, ValueError) as e:
        raise _TransientLLMError(f"malformed response: {e}") from e

    # OpenRouter occasionally returns an empty message on provider hiccups.
    # Treat that as transient so we fall through to the next model instead
    # of surfacing an empty string to the orchestrator.
    if not content:
        raise _TransientLLMError("empty response body")

    usage = data.get("usage") or {}
    return content, {
        "input": usage.get("prompt_tokens"),
        "output": usage.get("completion_tokens"),
        "total": usage.get("total_tokens"),
    }


# ─────────────────────────────────────────────
#  Ollama fallback
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
#  Introspection
# ─────────────────────────────────────────────

def current_config() -> dict:
    """Return a small summary for debugging (no secrets)."""
    return {
        "provider" : PROVIDER,
        "model"    : AGENT_MODEL,
        "fallbacks": list(AGENT_FALLBACK_MODELS) if PROVIDER == "openrouter" else [],
        "has_key"  : bool(OPENROUTER_API_KEY) if PROVIDER == "openrouter" else True,
        "base_url" : OPENROUTER_BASE_URL if PROVIDER == "openrouter" else OLLAMA_BASE_URL,
        "keep_alive": OLLAMA_KEEP_ALIVE if PROVIDER == "ollama" else None,
    }


def warm_up(timeout: float = 120.0) -> dict:
    """
    Load the agent + playbook models into memory ahead of the first request.

    Ollama loads weights lazily, so without this the first user of the day
    waits out the cold load inside their own request. No-op for remote
    providers, and never raises — a failed warm-up just means the first
    request pays the usual price.
    """
    if PROVIDER != "ollama":
        return {"warmed": [], "skipped": "provider is not ollama"}

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
