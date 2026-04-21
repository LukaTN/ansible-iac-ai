"""
=============================================================
  AnsibleAI RAG — Generator helpers (retrieval context → YAML prep)

  LLM playbook generation lives in agent/playbook_generator.py.
  This module keeps: context formatting, constraint checks, YAML extract,
  and file save with retrieval metadata headers.
=============================================================
"""

import os
import re
from datetime import datetime
from typing import List

from dotenv import load_dotenv
from langchain_core.documents import Document

# Get the project root directory (parent of the rag directory)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

# Load .env file from project root
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

OUTPUT_DIR  = "output"
MAX_RETRIES = 2


# ─────────────────────────────────────────────
#  CONTEXT BUILDER
# ─────────────────────────────────────────────

def build_context_string(docs: List[Document], scores: List[float]) -> str:
    """
    Build a formatted context string from retrieved docs.
    Ordered by score, with metadata headers.
    """
    blocks = []
    for doc, score in zip(docs, scores):
        meta  = doc.metadata
        header = (
            f"[{meta.get('collection')} | {meta.get('module')} | "
            f"{meta.get('chunk_type')} | relevance: {score:.3f}]"
        )
        blocks.append(f"{header}\n{doc.page_content}")

    return "\n\n---\n\n".join(blocks)


# ─────────────────────────────────────────────
#  YAML EXTRACTOR
# ─────────────────────────────────────────────

def extract_yaml(raw: str) -> str:
    """Extract clean YAML from LLM output."""
    # Strip markdown code fences
    fence = re.search(r"```(?:yaml|yml)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()

    # Find first --- marker
    lines     = raw.splitlines()
    start_idx = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
    if start_idx is not None:
        return "\n".join(lines[start_idx:]).strip()

    # Fallback: find first - name: line
    for i, line in enumerate(lines):
        if re.match(r"^-\s+name:", line):
            return "\n".join(lines[i:]).strip()

    return raw.strip()


def _extract_constraints(user_input: str) -> dict:
    """Parse strong constraints from natural language request."""
    text = user_input or ""
    lower = text.lower()

    def _grab(pattern: str) -> str | None:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            return None
        return m.group(1).strip().rstrip(".,")

    # Free-form resource name: "<type> rg-monitoring", "instance named foo".
    resource_name = _grab(
        r"(?:resource\s+group|instance|bucket|cluster|deployment|vm|virtual\s+machine|function|container)\s+"
        r"(?:called\s+|named\s+)?([a-zA-Z][a-zA-Z0-9\-_]{1,62})"
    )
    # `name: rg-monitoring` or `name=rg-monitoring` replies / captured facts.
    name_kv = _grab(r"(?:^|\s)name\s*[:=]\s*([a-zA-Z][a-zA-Z0-9\-_]{1,62})")

    location_kv = _grab(
        r"(?:location|region)\s*[:=]\s*([a-zA-Z][a-zA-Z0-9\-_]{1,40})"
    )
    subscription_kv = _grab(
        r"subscription(?:_id)?\s*[:=]\s*([a-fA-F0-9\-]{8,64})"
    )
    resource_group_kv = _grab(
        r"resource[_\s]group(?:_name)?\s*[:=]\s*([a-zA-Z][a-zA-Z0-9\-_]{1,62})"
    )

    constraints = {
        "resource_name": name_kv or resource_name,
        "location": location_kv,
        "subscription_id": subscription_kv,
        "resource_group": resource_group_kv,
        "deployment_name": _grab(r"deployment\s+name\s*:\s*([^\n]+)"),
        "image": _grab(r"image\s*:\s*([^\n]+)"),
        "replicas": _grab(r"replicas\s*:\s*(\d+)"),
        "container_port": _grab(r"container\s+port\s*:\s*(\d+)"),
        "configmap_name": _grab(r"configmap\s+name\s*:\s*([^\n]+)"),
        "secret_name": _grab(r"secret\s+name\s*:\s*([^\n]+)"),
        "service_name": _grab(r"connect\s+to\s+([a-zA-Z0-9\-_.]+)"),
        "must_use_configmap": "configmap" in lower and ("load" in lower or "from the configmap" in lower),
        "must_use_secret": "secret" in lower and ("read" in lower or "from the secret" in lower),
        "allow_resource_creation": bool(re.search(r"\b(create|provision|define)\b.*\b(configmap|secret)\b", lower)),
    }

    if not constraints["configmap_name"] and constraints["deployment_name"] and constraints["must_use_configmap"]:
        constraints["configmap_name"] = f"{constraints['deployment_name']}-config"
    if not constraints["secret_name"] and constraints["deployment_name"] and constraints["must_use_secret"]:
        constraints["secret_name"] = f"{constraints['deployment_name']}-secret"

    return constraints


def _format_constraints(constraints: dict) -> str:
    lines = []
    for key in (
        "resource_name",
        "location",
        "subscription_id",
        "resource_group",
        "deployment_name",
        "image",
        "replicas",
        "container_port",
        "configmap_name",
        "secret_name",
        "service_name",
        "must_use_configmap",
        "must_use_secret",
        "allow_resource_creation",
    ):
        val = constraints.get(key)
        if val is not None and val != "":
            lines.append(f"- {key}: {val}")
    return "\n".join(lines) if lines else "- none"


def _collect_generation_issues(
    yaml_content: str,
    constraints: dict,
    *,
    required_params: list[str] | None = None,
    example_pattern: dict | None = None,
) -> list[str]:
    """Lightweight guardrails to catch common false playbooks."""
    issues: list[str] = []
    raw = yaml_content or ""

    if "kubernetes.core.k8s_resource" in raw or re.search(r"\bk8s_resource\s*:", raw):
        issues.append("Invalid module detected: kubernetes.core.k8s_resource")

    placeholder_patterns = [
        r"configuration values here",
        r"placeholder",
        r"\btodo\b",
        r"\bchangeme\b",
        r"<[^>]+>",
    ]
    for pat in placeholder_patterns:
        if re.search(pat, raw, flags=re.IGNORECASE):
            issues.append("Placeholder content detected in YAML output")
            break

    if constraints.get("deployment_name") and constraints["deployment_name"] not in raw:
        issues.append("Exact deployment name is missing")
    if constraints.get("resource_name") and constraints["resource_name"] not in raw:
        issues.append(
            f"Exact resource name `{constraints['resource_name']}` from the "
            "request is missing in the YAML"
        )
    if constraints.get("location") and constraints["location"] not in raw:
        issues.append(
            f"Exact location `{constraints['location']}` from the request is "
            "missing in the YAML"
        )
    if constraints.get("subscription_id") and constraints["subscription_id"] not in raw:
        issues.append("Exact subscription_id from the request is missing in the YAML")
    if constraints.get("resource_group") and constraints["resource_group"] not in raw:
        issues.append(
            f"Exact resource_group `{constraints['resource_group']}` from the "
            "request is missing in the YAML"
        )
    if constraints.get("image") and constraints["image"] not in raw:
        issues.append("Exact image is missing")
    if constraints.get("replicas") and not re.search(rf"replicas\s*:\s*{re.escape(str(constraints['replicas']))}\b", raw):
        issues.append("Replicas value does not match request")
    if constraints.get("container_port") and not re.search(rf"containerPort\s*:\s*{re.escape(str(constraints['container_port']))}\b", raw):
        issues.append("Container port does not match request")

    if constraints.get("must_use_configmap"):
        if "configMapRef" not in raw:
            issues.append("Missing configMapRef under container envFrom")
        if constraints.get("configmap_name") and constraints["configmap_name"] not in raw:
            issues.append("ConfigMap name does not match request")
    if constraints.get("must_use_secret"):
        if "secretRef" not in raw:
            issues.append("Missing secretRef under container envFrom")
        if constraints.get("secret_name") and constraints["secret_name"] not in raw:
            issues.append("Secret name does not match request")

    if constraints.get("service_name") and constraints["service_name"] not in raw:
        issues.append("Service connection target missing (expected DB_HOST value)")

    if not constraints.get("allow_resource_creation"):
        if re.search(r"kind:\s*ConfigMap", raw) or re.search(r"kind:\s*Secret", raw):
            issues.append("Unnecessary resource creation task detected for ConfigMap/Secret")

    if re.search(r"-\s+name:\s*Scale deployment", raw, flags=re.IGNORECASE):
        issues.append("Unnecessary separate scale task detected")

    for p in (required_params or []):
        p = (p or "").strip()
        if not p:
            continue
        if not re.search(rf"(^|\s){re.escape(p)}\s*:", raw, flags=re.IGNORECASE):
            issues.append(f"Required param `{p}` is missing in generated YAML")

    pattern = dict(example_pattern or {})
    for key in (pattern.get("recurring_keys") or []):
        if not re.search(rf"(^|\s){re.escape(str(key))}\s*:", raw, flags=re.IGNORECASE):
            issues.append(f"Example-shared key `{key}` is missing in generated YAML")

    return issues


# ─────────────────────────────────────────────
#  SAVE
# ─────────────────────────────────────────────

def save_playbook(
    yaml_content: str,
    user_input: str,
    retrieval_meta: dict,
    *,
    llm_model: str = "unknown",
) -> str:
    """Save the generated playbook with RAG + LLM metadata header."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_name = re.sub(r"[^a-z0-9]+", "_", user_input.lower())[:30].strip("_")
    filename   = f"playbook_{short_name}_{timestamp}.yml"
    filepath   = os.path.join(OUTPUT_DIR, filename)

    header = (
        f"# ============================================================\n"
        f"# AnsibleAI — ChromaDB retrieval + agent LLM playbook\n"
        f"# Date        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Request     : {user_input}\n"
        f"# Module      : {retrieval_meta.get('primary_module', '—')}\n"
        f"# Collection  : {retrieval_meta.get('primary_collection', '—')}\n"
        f"# RAG score   : {retrieval_meta.get('primary_score', '—')}\n"
        f"# Chunks used : {len(retrieval_meta.get('docs', []))}\n"
        f"# Source URL  : {retrieval_meta.get('source_url', '—')}\n"
        f"# LLM model   : {llm_model}\n"
        f"# ============================================================\n\n"
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + yaml_content)

    return filepath
