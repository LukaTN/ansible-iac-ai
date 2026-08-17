"""Per-user daily LLM token budgets (Phase 5)."""

from __future__ import annotations

import pytest

from auth.budgets import (
    BudgetExceeded,
    add_usage,
    bind_user,
    check_budget,
    reset_for_tests,
    reset_user,
    snapshot,
)


@pytest.fixture(autouse=True)
def _reset_budgets():
    reset_for_tests()
    yield
    reset_for_tests()


def test_unlimited_when_budget_is_zero(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "user_daily_token_budget", 0)
    check_budget(1)
    token = bind_user(1)
    add_usage(50_000)
    reset_user(token)
    check_budget(1)
    snap = snapshot(1)
    assert snap["token_budget_limit"] == 0
    assert snap["token_budget_used"] == 50_000
    assert snap["token_budget_remaining"] == -1


def test_check_rejects_when_counter_meets_limit(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "user_daily_token_budget", 10)
    token = bind_user(7)
    check_budget(7)
    add_usage(10)
    with pytest.raises(BudgetExceeded) as exc:
        check_budget(7)
    assert exc.value.limit == 10
    assert exc.value.used == 10
    reset_user(token)


def test_usage_is_scoped_to_bound_user(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "user_daily_token_budget", 5)
    token = bind_user(1)
    add_usage(5)
    reset_user(token)
    check_budget(2)


def test_snapshot_reports_remaining(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "user_daily_token_budget", 100)
    token = bind_user(3)
    add_usage(40)
    reset_user(token)
    snap = snapshot(3)
    assert snap["token_budget_limit"] == 100
    assert snap["token_budget_used"] == 40
    assert snap["token_budget_remaining"] == 60
