"""Phase 6a — Prometheus metrics + Langfuse no-op when disabled."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_metrics_endpoint_is_public(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "ansibleai_http_requests_total" in body or "python_info" in body


def test_render_metrics_returns_prometheus_text():
    from observability.metrics import render_metrics

    body, content_type = render_metrics()
    assert b"#" in body or b"ansibleai_" in body
    assert "text/plain" in content_type or "openmetrics" in content_type


def test_record_helpers_do_not_raise():
    from observability.metrics import (
        record_gate_result,
        record_generation,
        record_llm_call,
    )

    record_generation(status="ok", started=True)
    record_generation(status="ok", duration_s=1.2)
    record_gate_result(passed=True, environmental=False, iteration=1)
    record_gate_result(passed=False, environmental=True, iteration=2)
    record_llm_call(
        provider="ollama",
        model="test",
        status="ok",
        duration_s=0.5,
        prompt_tokens=10,
        completion_tokens=20,
    )


def test_langfuse_disabled_is_noop(monkeypatch):
    import observability.tracing as tracing

    monkeypatch.setattr(tracing.settings, "langfuse_enabled", False)
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_client_failed", False)

    assert tracing.get_client() is None
    with tracing.generation_trace(thread_id=1, user_id=2, message="hi") as root:
        assert root is None
    tracing.observe_llm_call(
        model="m",
        provider="ollama",
        prompt="p",
        system=None,
        output="o",
        duration_s=0.1,
    )
    with tracing.observe("noop", as_type="span"):
        pass
    assert tracing.langchain_callback() is None


def test_langfuse_missing_keys_is_noop(monkeypatch):
    import observability.tracing as tracing

    monkeypatch.setattr(tracing.settings, "langfuse_enabled", True)
    monkeypatch.setattr(tracing.settings, "langfuse_public_key", "")
    monkeypatch.setattr(tracing.settings, "langfuse_secret_key", "")
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_client_failed", False)

    assert tracing.get_client() is None


def test_generation_trace_sets_output(monkeypatch):
    import observability.tracing as tracing

    root = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = root
    cm.__exit__.return_value = False

    prop_cm = MagicMock()
    prop_cm.__enter__.return_value = None
    prop_cm.__exit__.return_value = False

    fake = MagicMock()
    fake.start_as_current_observation.return_value = cm

    monkeypatch.setattr(tracing.settings, "langfuse_enabled", True)
    monkeypatch.setattr(tracing.settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(tracing.settings, "langfuse_secret_key", "sk")
    monkeypatch.setattr(tracing, "_client", fake)
    monkeypatch.setattr(tracing, "_client_failed", False)

    with patch("langfuse.propagate_attributes", return_value=prop_cm):
        with tracing.generation_trace(thread_id=9, user_id=1, message="hello") as r:
            assert r is root
        tracing.finish_generation_trace(root, status="ok", output_preview="done")

    root.update.assert_called()
    fake.flush.assert_called()
    call_kwargs = fake.start_as_current_observation.call_args.kwargs
    assert call_kwargs["as_type"] == "agent"
    assert call_kwargs["name"] == "generate-playbook"
