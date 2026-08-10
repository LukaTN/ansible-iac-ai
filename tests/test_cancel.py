"""Unit tests for cooperative generation cancellation."""

from __future__ import annotations

import pytest

from agent.cancel import (
    GenerationCancelled,
    begin,
    check,
    end,
    is_cancelled,
    request_cancel,
    reset_active_thread,
    set_active_thread,
)


def test_request_cancel_sets_flag_and_check_raises():
    begin(42)
    assert is_cancelled(42) is False
    assert request_cancel(42) is True
    assert is_cancelled(42) is True
    with pytest.raises(GenerationCancelled):
        check(42)
    end(42)
    assert is_cancelled(42) is False


def test_cancel_unknown_thread_is_noop():
    assert request_cancel(999001) is False
    check(999001)  # must not raise


def test_active_thread_context_used_by_check():
    begin(7)
    token = set_active_thread(7)
    try:
        request_cancel(7)
        with pytest.raises(GenerationCancelled):
            check()  # uses ContextVar
    finally:
        reset_active_thread(token)
        end(7)


def test_end_clears_registration():
    begin(3)
    request_cancel(3)
    end(3)
    assert request_cancel(3) is False
