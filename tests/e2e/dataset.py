"""Load and flatten golden E2E cases from YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_DATASET = os.path.join(os.path.dirname(__file__), "golden_dataset.yaml")


@dataclass
class GoldenCase:
    id: str
    query: str
    expected_collection: str
    expected_modules: list[str] = field(default_factory=list)
    intent_signals: list[str] = field(default_factory=list)
    yaml_contains: list[str] = field(default_factory=list)
    yaml_contains_any: list[str] = field(default_factory=list)
    yaml_must_not_contain: list[str] = field(default_factory=list)
    min_tasks: int = 1
    suite: str = "core"  # core | collection
    collection_key: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, suite: str, collection_key: str = "") -> GoldenCase:
        coll = data.get("expected_collection") or collection_key
        mods = list(data.get("expected_modules") or [])
        if not mods and coll:
            # allow single-module shorthand later
            pass
        return cls(
            id=str(data["id"]),
            query=str(data["query"]).strip(),
            expected_collection=coll,
            expected_modules=mods,
            intent_signals=list(data.get("intent_signals") or []),
            yaml_contains=list(data.get("yaml_contains") or []),
            yaml_contains_any=list(data.get("yaml_contains_any") or []),
            yaml_must_not_contain=list(data.get("yaml_must_not_contain") or []),
            min_tasks=int(data.get("min_tasks") or 1),
            suite=suite,
            collection_key=collection_key or coll,
        )


def load_dataset(path: str | None = None) -> dict[str, Any]:
    p = path or DEFAULT_DATASET
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def iter_cases(
    dataset: dict[str, Any] | None = None,
    *,
    suite: str | None = None,
    collection_filter: str | None = None,
) -> list[GoldenCase]:
    """Flatten core_cases + safety_cases + collections.* into one list."""
    data = dataset or load_dataset()
    out: list[GoldenCase] = []

    if suite in (None, "core"):
        for item in data.get("core_cases") or []:
            out.append(GoldenCase.from_dict(item, suite="core"))

    if suite in (None, "core", "safety"):
        for item in data.get("safety_cases") or []:
            out.append(GoldenCase.from_dict(item, suite="safety"))

    if suite in (None, "collections"):
        for coll_name, items in (data.get("collections") or {}).items():
            if collection_filter and coll_name != collection_filter:
                continue
            for item in items or []:
                case = GoldenCase.from_dict(item, suite="collections", collection_key=coll_name)
                if not case.expected_collection:
                    case.expected_collection = coll_name
                out.append(case)

    return out


def layer_weights(dataset: dict[str, Any] | None = None) -> dict[str, float]:
    data = dataset or load_dataset()
    w = data.get("layer_weights") or {}
    defaults = {
        "intent_understanding": 0.20,
        "retrieval_quality": 0.20,
        "module_correctness": 0.25,
        "playbook_quality": 0.25,
        "runtime_behavior": 0.10,
    }
    return {k: float(w.get(k, defaults[k])) for k in defaults}
