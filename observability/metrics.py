"""
Prometheus metrics for the API and generation pipeline.

Scrape: GET /metrics (public). Domain metrics below are what Grafana
dashboards and alerts should prefer over raw HTTP RED alone.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "ansibleai_http_requests_total",
    "HTTP requests handled by the Flask API",
    ["method", "endpoint", "status"],
)
HTTP_DURATION = Histogram(
    "ansibleai_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

GENERATION_STARTED = Counter(
    "ansibleai_generation_started_total",
    "Agent generations started",
)
GENERATION_COMPLETED = Counter(
    "ansibleai_generation_completed_total",
    "Agent generations finished",
    ["status"],
)
GENERATION_DURATION = Histogram(
    "ansibleai_generation_duration_seconds",
    "End-to-end agent generation latency",
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800),
)

GATE_RESULTS = Counter(
    "ansibleai_gate_result_total",
    "Production-gate outcomes",
    ["result"],
)
REPAIR_ITERATIONS = Histogram(
    "ansibleai_repair_iterations",
    "Repair-loop iteration index when the gate ran (1 = first draft)",
    buckets=(1, 2, 3, 4, 5, 6, 8, 10),
)

LLM_CALLS = Counter(
    "ansibleai_llm_calls_total",
    "LLM chat completions",
    ["provider", "model", "status"],
)
LLM_DURATION = Histogram(
    "ansibleai_llm_call_duration_seconds",
    "Single LLM call latency",
    ["provider", "model"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
LLM_TOKENS = Counter(
    "ansibleai_llm_tokens_total",
    "Approximate token usage (prompt/completion when reported)",
    ["model", "direction"],
)


def observe_http_request(*, method: str, endpoint: str, status: int, duration_s: float) -> None:
    ep = endpoint or "unknown"
    HTTP_REQUESTS.labels(method=method, endpoint=ep, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, endpoint=ep).observe(duration_s)


def record_generation(*, status: str, duration_s: float | None = None, started: bool = False) -> None:
    if started:
        GENERATION_STARTED.inc()
        return
    GENERATION_COMPLETED.labels(status=status).inc()
    if duration_s is not None:
        GENERATION_DURATION.observe(duration_s)


def record_gate_result(*, passed: bool, environmental: bool, iteration: int) -> None:
    if passed:
        result = "passed"
    elif environmental:
        result = "environment"
    else:
        result = "failed"
    GATE_RESULTS.labels(result=result).inc()
    record_repair_iteration(iteration)


def record_repair_iteration(iteration: int) -> None:
    REPAIR_ITERATIONS.observe(max(1, int(iteration or 1)))


def record_llm_call(
    *,
    provider: str,
    model: str,
    status: str,
    duration_s: float,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    safe_model = (model or "unknown")[:80]
    LLM_CALLS.labels(provider=provider or "unknown", model=safe_model, status=status).inc()
    LLM_DURATION.labels(provider=provider or "unknown", model=safe_model).observe(duration_s)
    if prompt_tokens:
        LLM_TOKENS.labels(model=safe_model, direction="prompt").inc(prompt_tokens)
    if completion_tokens:
        LLM_TOKENS.labels(model=safe_model, direction="completion").inc(completion_tokens)


@contextmanager
def timed() -> Iterator[list[float]]:
    """Yield a one-element list that receives elapsed seconds on exit."""
    box: list[float] = [0.0]
    t0 = time.perf_counter()
    try:
        yield box
    finally:
        box[0] = time.perf_counter() - t0


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
