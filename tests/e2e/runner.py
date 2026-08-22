"""
Sequential E2E runner: one query at a time, wait for completion, then evaluate.

Modes:
  api      — POST /api/chat (requires Flask app + DB + Ollama)
  pipeline — in-process LangGraph agent (no HTTP; needs Ollama + Chroma)
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.e2e.dataset import GoldenCase, iter_cases, layer_weights, load_dataset
from tests.e2e.layers import evaluate_case

sys.path.insert(0, os.path.join(BACKEND, "pipeline"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def health_check(base_url: str, timeout: float = 5.0) -> bool:
    try:
        r = requests.get(f"{base_url.rstrip('/')}/stats", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def run_case_api(
    case: GoldenCase,
    base_url: str,
    timeout_sec: float,
) -> dict[str, Any]:
    """POST /api/chat and return raw run payload."""
    started = time.perf_counter()
    started_at = _utc_now()
    out: dict[str, Any] = {
        "mode": "api",
        "case_id": case.id,
        "query": case.query,
        "started_at": started_at,
        "http_status": None,
        "error": None,
    }
    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={"message": case.query},
            timeout=timeout_sec,
        )
        out["http_status"] = r.status_code
        elapsed = time.perf_counter() - started
        out["duration_sec"] = round(elapsed, 2)
        out["ended_at"] = _utc_now()

        if r.status_code != 200:
            try:
                out["error"] = r.json().get("error", r.text[:500])
            except Exception:
                out["error"] = r.text[:500]
            return out

        data = r.json()
        am = data.get("assistant_message") or {}
        out["playbook"] = am.get("playbook") or ""
        out["filename"] = am.get("filename")
        out["detected_module"] = am.get("module")
        out["validation"] = am.get("validation") or {}
        out["retrieval_meta"] = am.get("rag_meta") or {}
        out["thread_id"] = (data.get("thread") or {}).get("id")
    except requests.RequestException as e:
        out["duration_sec"] = round(time.perf_counter() - started, 2)
        out["ended_at"] = _utc_now()
        out["error"] = str(e)
    return out


def run_case_pipeline(case: GoldenCase) -> dict[str, Any]:
    """In-process agent run (same LangGraph path as Flask without HTTP)."""
    started = time.perf_counter()
    started_at = _utc_now()
    out: dict[str, Any] = {
        "mode": "pipeline",
        "case_id": case.id,
        "query": case.query,
        "started_at": started_at,
        "error": None,
    }
    try:
        from agent import handle_message
        from app import app

        # pgvector / Flask-SQLAlchemy need an app context. The HTTP path
        # has one; this in-process runner did not, which crashed every case.
        with app.app_context():
            resp = handle_message(
                thread_id=0,
                user_message=case.query,
                history=[],
            )

        validation = resp.validation or {}
        out["playbook"] = resp.playbook or ""
        out["filename"] = resp.filename
        out["detected_module"] = resp.module
        out["validation"] = {
            "is_valid": bool(validation.get("is_valid")),
            "errors": list(validation.get("errors") or []),
            "warnings": list(validation.get("warnings") or []),
            "passed": validation.get("passed", 0),
            "ansible_lint": validation.get("ansible_lint"),
            "module": resp.module,
        }
        out["retrieval_meta"] = resp.rag_meta or {}
        out["http_status"] = 200
    except Exception as e:
        out["error"] = str(e)
        out["http_status"] = 500
    out["duration_sec"] = round(time.perf_counter() - started, 2)
    out["ended_at"] = _utc_now()
    return out


def run_suite(
    *,
    mode: str = "api",
    base_url: str = "http://127.0.0.1:5000",
    timeout_sec: float = 900.0,
    suite: str | None = None,
    collection_filter: str | None = None,
    case_ids: list[str] | None = None,
    on_case_done: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """
    Run all matching golden cases sequentially; evaluate each; return full report dict.
    """
    dataset = load_dataset()
    weights = layer_weights(dataset)
    cases = iter_cases(dataset, suite=suite, collection_filter=collection_filter)
    if case_ids:
        allow = set(case_ids)
        cases = [c for c in cases if c.id in allow]

    if mode == "api" and not health_check(base_url):
        raise RuntimeError(
            f"Backend not reachable at {base_url}/stats — start `py app.py` first."
        )

    results: list[dict[str, Any]] = []
    for i, case in enumerate(cases, start=1):
        print(f"[{i}/{len(cases)}] {case.id} ({case.expected_collection})...", flush=True)

        if mode == "api":
            raw = run_case_api(case, base_url, timeout_sec)
        elif mode == "pipeline":
            raw = run_case_pipeline(case)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        evaluation = None
        if raw.get("error") is None and raw.get("playbook") is not None:
            evaluation = evaluate_case(
                case,
                query=case.query,
                playbook=raw.get("playbook") or "",
                retrieval_meta=raw.get("retrieval_meta"),
                validation=raw.get("validation"),
                detected_module=raw.get("detected_module"),
                weights=weights,
            )

        row = {
            **raw,
            "evaluation": evaluation,
            "expected": {
                "collection": case.expected_collection,
                "modules": case.expected_modules,
            },
        }
        results.append(row)
        if on_case_done:
            on_case_done(row)

        sc = evaluation["overall_score"] if evaluation else 0
        err = raw.get("error")
        print(
            f"    done {raw.get('duration_sec')}s score={sc}"
            + (f" ERROR={err[:80]}" if err else ""),
            flush=True,
        )

    return build_summary(results, mode=mode, base_url=base_url, weights=weights)


def build_summary(
    results: list[dict[str, Any]],
    *,
    mode: str,
    base_url: str,
    weights: dict[str, float],
) -> dict[str, Any]:
    """Aggregate per-collection and global stats."""
    by_collection: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        coll = (r.get("evaluation") or {}).get("collection") or r.get("expected", {}).get(
            "collection", "unknown"
        )
        by_collection.setdefault(coll, []).append(r)

    def _coll_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        scores = [
            r["evaluation"]["overall_score"]
            for r in rows
            if r.get("evaluation")
        ]
        layer_avgs: dict[str, list[float]] = {}
        for r in rows:
            ev = r.get("evaluation")
            if not ev:
                continue
            for ln, ld in ev.get("layers", {}).items():
                layer_avgs.setdefault(ln, []).append(ld["score"])
        return {
            "total": len(rows),
            "completed": sum(1 for r in rows if not r.get("error")),
            "errors": sum(1 for r in rows if r.get("error")),
            "avg_overall_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "avg_layer_scores": {
                k: round(sum(v) / len(v), 1) for k, v in layer_avgs.items()
            },
            "pass_rate_70": round(
                100 * sum(1 for s in scores if s >= 70) / len(scores), 1
            )
            if scores
            else 0,
        }

    summary_coll = {k: _coll_stats(v) for k, v in by_collection.items()}
    all_scores = [
        r["evaluation"]["overall_score"] for r in results if r.get("evaluation")
    ]

    return {
        "generated_at": _utc_now(),
        "mode": mode,
        "base_url": base_url if mode == "api" else None,
        "sequential": True,
        "layer_weights": weights,
        "total_cases": len(results),
        "global": {
            "avg_overall_score": round(sum(all_scores) / len(all_scores), 1)
            if all_scores
            else 0,
            "pass_rate_70": round(
                100 * sum(1 for s in all_scores if s >= 70) / len(all_scores), 1
            )
            if all_scores
            else 0,
        },
        "by_collection": summary_coll,
        "results": results,
    }
