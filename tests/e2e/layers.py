"""
Five-layer evaluation for Ansible playbook generation agents.

Layers (AI agent testing best practice):
  1. Intent understanding   — query aligns with target cloud/module intent
  2. Retrieval quality      — RAG returns the right collection/module in top ranks
  3. Module correctness     — generated YAML uses expected collection modules
  4. Playbook quality       — validator + golden structural expectations
  5. Runtime behavior       — YAML parses; optional ansible-playbook syntax-check
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any

import yaml

from tests.e2e.dataset import GoldenCase

# Cloud keyword → expected collection prefix (intent layer)
_CLOUD_HINTS: dict[str, str] = {
    "ec2": "amazon.aws",
    "s3": "amazon.aws",
    "rds": "amazon.aws",
    "aws": "amazon.aws",
    "amazon": "amazon.aws",
    "azure": "azure.azcollection",
    "aks": "azure.azcollection",
    "azurerm": "azure.azcollection",
    "kubernetes": "kubernetes.core",
    "k8s": "kubernetes.core",
    "helm": "kubernetes.core",
    "namespace": "kubernetes.core",
}


def _norm(s: str) -> str:
    return (s or "").lower()


def _playbook_task_count(parsed: Any) -> int:
    if not isinstance(parsed, list):
        return 0
    n = 0
    for play in parsed:
        if isinstance(play, dict):
            tasks = play.get("tasks") or []
            if isinstance(tasks, list):
                n += sum(1 for t in tasks if isinstance(t, dict))
    return n


def _strip_header_comments(yaml_text: str) -> str:
    return "\n".join(line for line in yaml_text.splitlines() if not line.startswith("#")).strip()


def score_intent(case: GoldenCase, query: str) -> dict[str, Any]:
    """Layer 1: Does the query express the right cloud/module intent?"""
    q = _norm(query)
    signals = case.intent_signals or []
    hits = sum(1 for s in signals if _norm(s) in q)
    signal_ratio = hits / len(signals) if signals else 1.0

    cloud_hit = False
    for token, coll in _CLOUD_HINTS.items():
        if token in q and coll == case.expected_collection:
            cloud_hit = True
            break
    if case.expected_collection.split(".")[0] in q:
        cloud_hit = True

    score = int(round(100 * (0.6 * signal_ratio + 0.4 * (1.0 if cloud_hit else 0.0))))
    return {
        "score": min(100, max(0, score)),
        "signal_hits": hits,
        "signal_total": len(signals),
        "cloud_hint_matched": cloud_hit,
    }


def score_retrieval(
    case: GoldenCase,
    retrieval_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Layer 2: RAG primary + ranked modules vs expected."""
    meta = retrieval_meta or {}
    primary = meta.get("primary_module") or ""
    primary_coll = meta.get("primary_collection") or ""
    ranked = meta.get("ranked_modules") or []

    coll_ok = primary_coll == case.expected_collection or primary.startswith(
        case.expected_collection + "."
    )

    mod_rank = None
    mod_ok = False
    expected_set = {_norm(m) for m in case.expected_modules}
    for i, entry in enumerate(ranked[:8], start=1):
        mod = entry.get("module") if isinstance(entry, dict) else ""
        if _norm(mod) in expected_set or any(
            _norm(mod).endswith("." + m.split(".")[-1]) for m in case.expected_modules
        ):
            mod_rank = i
            mod_ok = True
            break
        short = mod.split(".")[-1] if mod else ""
        if any(short == m.split(".")[-1] for m in case.expected_modules):
            mod_rank = i
            mod_ok = True
            break

    primary_mod_ok = _norm(primary) in expected_set or any(
        primary.startswith(m.split(".")[0]) for m in case.expected_modules
    )

    parts = [coll_ok, mod_ok or primary_mod_ok]
    score = int(round(100 * sum(parts) / len(parts))) if parts else 0
    if mod_rank == 1:
        score = min(100, score + 10)

    return {
        "score": min(100, score),
        "primary_module": primary,
        "primary_collection": primary_coll,
        "collection_match": coll_ok,
        "expected_module_in_ranked": mod_ok,
        "expected_module_rank": mod_rank,
        "primary_module_match": primary_mod_ok,
    }


def _playbook_uses_collection(playbook: str, collection: str) -> bool:
    if collection in playbook:
        return True
    short = collection.split(".")[-1]
    return f"{collection}." in playbook or f"  {short}:" in playbook


def score_module_correctness(
    case: GoldenCase,
    playbook: str,
    detected_module: str | None,
) -> dict[str, Any]:
    """Layer 3: Validator-detected module + YAML module usage."""
    pb = playbook or ""
    det = detected_module or ""

    det_ok = False
    for exp in case.expected_modules:
        if det == exp or det.endswith("." + exp.split(".")[-1]):
            det_ok = True
            break
        if exp.split(".")[-1] in det:
            det_ok = True
            break

    yaml_mod_ok = any(_norm(m) in _norm(pb) for m in case.expected_modules)
    coll_ok = _playbook_uses_collection(pb, case.expected_collection)

    wrong_cloud = False
    for coll in ("amazon.aws", "azure.azcollection", "kubernetes.core"):
        if coll != case.expected_collection and _playbook_uses_collection(pb, coll):
            # another cloud's modules present
            if case.expected_collection in ("amazon.aws", "azure.azcollection", "kubernetes.core"):
                wrong_cloud = True

    checks = [det_ok or yaml_mod_ok, coll_ok or yaml_mod_ok, not wrong_cloud]
    score = int(round(100 * sum(1 for c in checks if c) / len(checks)))

    return {
        "score": min(100, max(0, score)),
        "detected_module": det,
        "detected_match": det_ok,
        "yaml_module_match": yaml_mod_ok,
        "collection_in_yaml": coll_ok,
        "wrong_cloud_module": wrong_cloud,
    }


def score_playbook_quality(
    case: GoldenCase,
    playbook: str,
    validation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Layer 4: Validator result + golden yaml_contains rules."""
    v = validation or {}
    pb_raw = playbook or ""
    pb = _strip_header_comments(pb_raw)

    valid = bool(v.get("is_valid"))
    contains_all = all(s in pb for s in case.yaml_contains)
    contains_any = True
    if case.yaml_contains_any:
        contains_any = any(s in pb for s in case.yaml_contains_any)
    # Check the raw YAML so a secret hidden in a `#` comment still fails.
    not_forbidden = all(s not in pb_raw for s in case.yaml_must_not_contain)

    task_count = 0
    try:
        parsed = yaml.safe_load(pb)
        task_count = _playbook_task_count(parsed)
    except yaml.YAMLError:
        parsed = None

    min_tasks_ok = task_count >= case.min_tasks

    checks = [valid, contains_all, contains_any, not_forbidden, min_tasks_ok]
    weights = [0.35, 0.25, 0.15, 0.10, 0.15]
    score = int(round(100 * sum(w for c, w in zip(checks, weights) if c) / sum(weights)))

    return {
        "score": min(100, max(0, score)),
        "validator_valid": valid,
        "validation_errors": list(v.get("errors") or []),
        "yaml_contains_all": contains_all,
        "yaml_contains_any": contains_any,
        "yaml_forbidden_absent": not_forbidden,
        "min_tasks_ok": min_tasks_ok,
        "task_count": task_count,
        "missing_contains": [s for s in case.yaml_contains if s not in pb],
    }


def score_runtime(playbook: str) -> dict[str, Any]:
    """Layer 5: Parse YAML + optional ansible-playbook --syntax-check."""
    pb = _strip_header_comments(playbook or "")
    parse_ok = False
    has_hosts = False
    parse_error = ""

    try:
        parsed = yaml.safe_load(pb)
        parse_ok = parsed is not None
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            has_hosts = "hosts" in parsed[0]
    except yaml.YAMLError as e:
        parse_error = str(e)

    syntax_ok = None
    syntax_detail = "not_run"
    if parse_ok and pb:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yml", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(pb)
                path = tmp.name
            proc = subprocess.run(
                ["ansible-playbook", "--syntax-check", path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            syntax_ok = proc.returncode == 0
            syntax_detail = (proc.stderr or proc.stdout or "")[:300]
            os.unlink(path)
        except FileNotFoundError:
            syntax_detail = "ansible-playbook not installed"
        except subprocess.TimeoutExpired:
            syntax_detail = "syntax-check timeout"
        except OSError as e:
            syntax_detail = str(e)

    parts = [parse_ok, has_hosts]
    if syntax_ok is True:
        parts.append(True)
    elif syntax_ok is None:
        # no ansible-playbook: score parse + hosts only
        pass
    else:
        parts.append(False)

    score = int(round(100 * sum(1 for p in parts if p) / len(parts))) if parts else 0

    return {
        "score": min(100, max(0, score)),
        "yaml_parse_ok": parse_ok,
        "has_hosts": has_hosts,
        "parse_error": parse_error,
        "syntax_check": syntax_ok,
        "syntax_detail": syntax_detail,
    }


def evaluate_case(
    case: GoldenCase,
    *,
    query: str,
    playbook: str,
    retrieval_meta: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    detected_module: str | None,
    weights: dict[str, float],
) -> dict[str, Any]:
    """Run all five layers and compute weighted overall score."""
    layers = {
        "intent_understanding": score_intent(case, query),
        "retrieval_quality": score_retrieval(case, retrieval_meta),
        "module_correctness": score_module_correctness(case, playbook, detected_module),
        "playbook_quality": score_playbook_quality(case, playbook, validation),
        "runtime_behavior": score_runtime(playbook),
    }

    overall = 0.0
    for name, w in weights.items():
        overall += w * layers[name]["score"]

    return {
        "case_id": case.id,
        "suite": case.suite,
        "collection": case.collection_key or case.expected_collection,
        "layers": layers,
        "overall_score": round(overall, 1),
        "weights": weights,
    }
