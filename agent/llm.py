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
import json
import requests
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────

PROVIDER     = (os.getenv("AGENT_LLM_PROVIDER", "openrouter") or "openrouter").lower()
AGENT_MODEL  = os.getenv("AGENT_MODEL", "google/gemma-4-31b-it:free")

OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

REFERER  = os.getenv("OPENROUTER_REFERER",  "http://localhost:5000")
APP_NAME = os.getenv("OPENROUTER_APP_NAME", "AnsibleAI")

# Ask responses to be shorter by default; planner/synthesizer override this as needed.
DEFAULT_MAX_TOKENS = 900

REQUEST_TIMEOUT = 120

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
    provider = PROVIDER
    model    = (model or AGENT_MODEL).strip()

    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise LLMError(
                "OPENROUTER_API_KEY is not set. Add it to your .env or set "
                "AGENT_LLM_PROVIDER=ollama to use a local model instead."
            )
        return _call_openrouter_with_fallback(
            prompt, system=system, temperature=temperature,
            max_tokens=max_tokens, primary_model=model, expect_json=expect_json,
        )

    if provider == "ollama":
        return _call_ollama(
            prompt, system=system, temperature=temperature,
            max_tokens=max_tokens, model=model,
        )

    raise LLMError(f"Unknown AGENT_LLM_PROVIDER: {provider!r}")


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
) -> str:
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
                print(f"  [Agent] LLM fallback -> {model}")
            return _call_openrouter_once(
                prompt, system=system, temperature=temperature,
                max_tokens=max_tokens, model=model, expect_json=expect_json,
            )
        except _TransientLLMError as e:
            last_error = str(e)
            print(f"  [Agent] LLM model {model} transient error: {e}")
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
) -> str:
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

    return content


# ─────────────────────────────────────────────
#  Ollama fallback
# ─────────────────────────────────────────────

def _call_ollama(
    prompt: str, *, system: str | None, temperature: float,
    max_tokens: int, model: str,
) -> str:
    payload = {
        "model"  : model,
        "prompt" : prompt,
        "system" : system or "",
        "stream" : False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload, timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise LLMError(f"Ollama request failed: {e}") from e

    if r.status_code >= 400:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:300]}")

    try:
        return (r.json().get("response") or "").strip()
    except ValueError as e:
        raise LLMError(f"Ollama returned invalid JSON: {e}") from e


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
    }
