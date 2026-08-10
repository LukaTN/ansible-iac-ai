"""
Guard the stage-2 routing keyword table against drifting away from the corpus.

Routing is a hard filter: a keyword that wins outright sends a Chroma ``$eq`` on
one collection, so if that collection has nothing to offer, the query cannot
reach the right module no matter how good the rest of the pipeline is. This is
how "docker" used to pin every container query to community.general, which
contains no docker modules.

These tests read the parsed corpus rather than the vector store, so they run
without Ollama or a built index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.retriever import COLLECTION_KEYWORDS, route_collections

PARSED_DIR = Path(__file__).resolve().parent.parent / "data" / "parsed"

# Concepts served by a general-purpose module rather than a module of that name.
# kubernetes.core.k8s manages any resource kind, so "configmap" is a legitimate
# routing signal even though no module is called "configmap".
GENERIC_MODULE_CONCEPTS = {
    "kubernetes.core": {
        "configmap", "secret", "ingress", "daemonset", "statefulset", "cronjob",
        "persistentvolume", "pvc", "serviceaccount", "rbac", "clusterrole",
        "role", "rollout", "replica", "job", "replicaset",
    },
    # Azure concepts whose module is named after the Azure resource type.
    "azure.azcollection": {"nsg", "acr", "aks cluster"},
    # AWS services with no module in the scraped amazon.aws set; they still
    # point at the only collection that could ever serve them.
    # "object storage" is the generic name for S3 (served by s3_bucket/s3_object).
    "amazon.aws": {
        "alb", "ecs", "eks", "sns", "sqs", "cloudfront", "elasticache",
        "secretsmanager", "ssm", "postgresql", "object storage",
    },
    # nmcli is the NetworkManager CLI module; users say "NetworkManager".
    "community.general": {"networkmanager"},
}


def _corpus() -> dict[str, list[tuple[str, str]]]:
    """collection -> [(short_module_name, description)]."""
    if not PARSED_DIR.exists():
        pytest.skip("parsed corpus not available")

    out: dict[str, list[tuple[str, str]]] = {}
    for path in PARSED_DIR.glob("*/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        collection = data.get("collection", "")
        module = (data.get("module") or "").split(".")[-1]
        out.setdefault(collection, []).append(
            (module, (data.get("description") or "").lower())
        )
    if not out:
        pytest.skip("parsed corpus is empty")
    return out


def _supported(keyword: str, modules: list[tuple[str, str]]) -> bool:
    squashed = keyword.replace(" ", "")
    return any(
        squashed in name.replace("_", "") or keyword in description
        for name, description in modules
    )


@pytest.mark.parametrize("collection", sorted(COLLECTION_KEYWORDS))
def test_every_keyword_has_a_backing_module(collection: str):
    corpus = _corpus()
    modules = corpus.get(collection)
    if not modules:
        pytest.skip(f"{collection} not present in the parsed corpus")

    allowed = GENERIC_MODULE_CONCEPTS.get(collection, set())
    orphans = [
        kw
        for kw in COLLECTION_KEYWORDS[collection]
        if kw not in allowed and not _supported(kw, modules)
    ]
    assert not orphans, (
        f"{collection} routes these keywords but has no module for them: {orphans}. "
        "Either drop the keyword, move it to the collection that owns the module, "
        "or add it to GENERIC_MODULE_CONCEPTS if a general-purpose module serves it."
    )


def test_docker_does_not_pin_to_a_collection_without_docker_modules():
    # community.docker is not scraped, so no collection can serve this; the
    # router must leave the search unfiltered rather than guess.
    decision = route_collections("run a docker container from an image")
    assert "community.general" not in decision.collections


def test_hostname_can_reach_ansible_builtin():
    decision = route_collections("set the system hostname to web-01")
    assert decision.mode == "all" or "ansible.builtin" in decision.collections
