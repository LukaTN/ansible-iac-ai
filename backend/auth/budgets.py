"""
=============================================================
  AnsibleAI — per-user daily LLM token budgets

  Enforced in the Celery worker (the only place that spends tokens).
  Counters live in Redis so every replica sees the same remaining
  budget. When Redis is unreachable the check is fail-open and usage
  is tracked in-process so tests still exercise the cap.

  0 (the default) means unlimited.
=============================================================
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from config import settings
from logging_setup import get_logger

log = get_logger(__name__)

_active_user_id: ContextVar[int | None] = ContextVar("token_budget_user", default=None)
_memory: dict[str, int] = {}
_redis: Any | None = None
_redis_failed = False

KEY_PREFIX = "ansibleai:budget:"


class BudgetExceeded(Exception):
    """The user has no remaining daily token budget."""

    def __init__(self, used: int, limit: int) -> None:
        super().__init__("Daily generation budget reached.")
        self.used = used
        self.limit = limit


def bind_user(user_id: int) -> Token[int | None]:
    return _active_user_id.set(int(user_id))


def reset_user(token: Token[int | None]) -> None:
    _active_user_id.reset(token)


def check_budget(user_id: int) -> None:
    limit = settings.user_daily_token_budget
    if limit <= 0:
        return
    used = _get(user_id)
    if used >= limit:
        raise BudgetExceeded(used=used, limit=limit)


def add_usage(tokens: int) -> None:
    """Increment the bound user's daily counter. No-op when unbound."""
    if tokens <= 0:
        return
    user_id = _active_user_id.get()
    if user_id is None:
        return
    _incr(user_id, tokens)


def snapshot(user_id: int) -> dict[str, int]:
    """Today's spend, plus remaining when a daily cap is configured."""
    limit = int(settings.user_daily_token_budget)
    used = _get(user_id)
    remaining = max(limit - used, 0) if limit > 0 else -1
    return {
        "token_budget_limit": limit,
        "token_budget_used": used,
        "token_budget_remaining": remaining,
    }


def _key(user_id: int) -> str:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{KEY_PREFIX}{user_id}:{day}"


def _client() -> Any | None:
    global _redis, _redis_failed
    if _redis_failed:
        return None
    if _redis is not None:
        return _redis
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        import redis

        _redis = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        _redis.ping()
        return _redis
    except Exception:
        _redis_failed = True
        log.warning("auth.budget.redis_unavailable")
        return None


def _get(user_id: int) -> int:
    key = _key(user_id)
    client = _client()
    if client is not None:
        try:
            raw = client.get(key)
            return int(raw) if raw else 0
        except Exception:
            log.warning("auth.budget.redis_get_failed", user_id=user_id)
    return int(_memory.get(key, 0))


def _incr(user_id: int, tokens: int) -> None:
    key = _key(user_id)
    client = _client()
    if client is not None:
        try:
            value = int(client.incrby(key, int(tokens)))
            if value == int(tokens):
                client.expire(key, 60 * 60 * 48)
            return
        except Exception:
            log.warning("auth.budget.redis_incr_failed", user_id=user_id)
    _memory[key] = int(_memory.get(key, 0)) + int(tokens)


def reset_for_tests() -> None:
    """Drop in-process counters and the Redis client (unit tests)."""
    global _redis, _redis_failed
    _memory.clear()
    _redis = None
    _redis_failed = False
    _active_user_id.set(None)


__all__ = [
    "BudgetExceeded",
    "add_usage",
    "bind_user",
    "check_budget",
    "reset_for_tests",
    "reset_user",
    "snapshot",
]
