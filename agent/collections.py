"""
=============================================================
  AnsibleAI Agent — Collection allow-list

  The planner LLM is unreliable at choosing a `collection` filter:
  it regularly hallucinates names that don't exist in the KB, or
  picks the wrong cloud (e.g. `azure.azcollection` for an AWS
  CloudWatch request). This module derives the TRUE set of
  collections from `knowledge_base.json` at startup and exposes
  a small helper to sanitize any planner-provided collection.

  The source of truth is always your actual indexed KB — if you
  add a new collection later and rebuild the KB, the allow-list
  picks it up automatically on next process start.
=============================================================
"""

from __future__ import annotations

import os
import json
from functools import lru_cache


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@lru_cache(maxsize=1)
def get_collection_allowlist() -> frozenset[str]:
    """
    Scan the knowledge base once and return the set of collections
    that are actually indexed. Cached for the life of the process;
    call `reload_collection_allowlist()` after a re-index.
    """
    try:
        kb_path = os.path.join(PROJECT_ROOT, "data", "knowledge_base.json")
        with open(kb_path, "r", encoding="utf-8") as f:
            kb = json.load(f) or {}
    except Exception as exc:
        print(f"  [Agent] Could not load KB for allow-list: {exc}")
        return frozenset()

    modules = (kb.get("modules") or {}) if isinstance(kb, dict) else {}
    names: set[str] = set()
    for mod in modules.values():
        if not isinstance(mod, dict):
            continue
        coll = (mod.get("collection") or "").strip()
        # "unknown.collection" is a catch-all bucket, not a real
        # collection the user can target.
        if coll and coll.lower() != "unknown.collection":
            names.add(coll)
    return frozenset(names)


def reload_collection_allowlist() -> frozenset[str]:
    """Clear the cache (call after a KB rebuild) and return the fresh set."""
    get_collection_allowlist.cache_clear()
    return get_collection_allowlist()


def is_known_collection(name: str | None) -> bool:
    """True iff `name` is a real collection that exists in the KB."""
    if not name:
        return False
    return name.strip() in get_collection_allowlist()


def sanitize_collection(name: str | None) -> str | None:
    """
    Return the collection name iff it's in the allow-list, else None.
    Used to discard hallucinated collection names from the planner.
    """
    if not name:
        return None
    cleaned = name.strip()
    return cleaned if cleaned in get_collection_allowlist() else None
