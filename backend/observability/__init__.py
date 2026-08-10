"""
=============================================================
  AnsibleAI — Observability (Phase 6a)

  Prometheus metrics + optional Langfuse tracing.
  Both are no-ops when disabled / unconfigured so local
  development without the observability stack still works.
=============================================================
"""

from observability.metrics import (
    observe_http_request,
    record_gate_result,
    record_generation,
    record_llm_call,
    record_repair_iteration,
    render_metrics,
)
from observability.tracing import (
    end_trace,
    finish_generation_trace,
    generation_trace,
    get_client,
    langchain_callback,
    observe,
    observe_llm_call,
    start_generation_trace,
    trace_span,
)

__all__ = [
    "end_trace",
    "finish_generation_trace",
    "generation_trace",
    "get_client",
    "langchain_callback",
    "observe",
    "observe_http_request",
    "observe_llm_call",
    "record_gate_result",
    "record_generation",
    "record_llm_call",
    "record_repair_iteration",
    "render_metrics",
    "start_generation_trace",
    "trace_span",
]
