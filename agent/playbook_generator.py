"""
=============================================================
  AnsibleAI — Playbook generation via agent LLM

  Retrieval metadata (docs + scores) comes from RAG; this module
  calls the same LLM stack as the planner (`agent.llm.chat`).
=============================================================
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from agent.llm import AGENT_MODEL, chat as agent_chat
from agent.prompts import PLAYBOOK_SYSTEM_PROMPT, PLAYBOOK_USER_MESSAGE_TEMPLATE

from rag.generator import (
    MAX_RETRIES,
    _collect_generation_issues,
    _extract_constraints,
    _format_constraints,
    build_context_string,
    extract_yaml,
    save_playbook,
)


def _playbook_model() -> str:
    m = (os.getenv("PLAYBOOK_MODEL") or "").strip()
    return m if m else AGENT_MODEL


def _playbook_max_tokens() -> int:
    raw = (os.getenv("PLAYBOOK_MAX_TOKENS") or "").strip()
    if raw.isdigit():
        return max(512, int(raw))
    return 3500


def _playbook_temperature() -> float:
    raw = (os.getenv("PLAYBOOK_TEMPERATURE") or "").strip()
    try:
        return float(raw) if raw else 0.15
    except ValueError:
        return 0.15


def _render_docs(docs: list, scores: list[float]) -> str:
    if not docs:
        return "(none)"
    return build_context_string(docs, scores)


def _split_context_sections(docs: list, scores: list[float]) -> tuple[str, str, str, list]:
    required_docs = []
    example_docs = []
    other_docs = []
    required_scores: list[float] = []
    example_scores: list[float] = []
    other_scores: list[float] = []
    for doc, score in zip(docs, scores):
        ctype = (doc.metadata or {}).get("chunk_type")
        if ctype == "required_params":
            required_docs.append(doc)
            required_scores.append(score)
        elif ctype == "example":
            example_docs.append(doc)
            example_scores.append(score)
        else:
            other_docs.append(doc)
            other_scores.append(score)
    top_examples = example_docs[:3]
    top_example_scores = example_scores[:3]
    return (
        _render_docs(required_docs, required_scores),
        _render_docs(top_examples, top_example_scores),
        _render_docs(other_docs, other_scores),
        top_examples,
    )


def _derive_example_pattern_contract(example_docs: list) -> dict:
    modules: list[str] = []
    recurring_keys = set()
    if not example_docs:
        return {"summary": "none", "modules": [], "recurring_keys": []}
    for d in example_docs:
        txt = d.page_content or ""
        for m in re.findall(r"^\s{2,}([a-zA-Z0-9_.]+)\s*:\s*$", txt, flags=re.MULTILINE):
            if m not in modules:
                modules.append(m)
        for k in re.findall(r"^\s{4,}([a-zA-Z_][a-zA-Z0-9_]*)\s*:", txt, flags=re.MULTILINE):
            recurring_keys.add(k)
    rec = sorted(recurring_keys)[:15]
    summary = "modules=" + (", ".join(modules[:4]) if modules else "unknown")
    if rec:
        summary += " | recurring_keys=" + ", ".join(rec)
    return {"summary": summary, "modules": modules, "recurring_keys": rec}


def _debug_print_retrieval_inputs(
    docs: list,
    scores: list[float],
    required_params: list[str],
    top_examples: list,
) -> None:
    print("  [PlaybookGen][Debug] Retrieved docs:")
    if not docs:
        print("    - (none)")
    for i, (doc, score) in enumerate(zip(docs, scores), start=1):
        md = doc.metadata or {}
        print(
            "    {idx}. module={module} | collection={collection} | chunk_type={ctype} | score={score:.3f}".format(
                idx=i,
                module=md.get("module", "unknown"),
                collection=md.get("collection", "unknown"),
                ctype=md.get("chunk_type", "unknown"),
                score=float(score),
            )
        )

    print("  [PlaybookGen][Debug] Required params from retrieval:")
    if required_params:
        print("    - " + ", ".join(required_params))
    else:
        print("    - (none)")

    print("  [PlaybookGen][Debug] Example chunks used (top 3):")
    if not top_examples:
        print("    - (none)")
    for i, doc in enumerate(top_examples, start=1):
        md = doc.metadata or {}
        ex_idx = md.get("example_index", "?")
        preview = " ".join((doc.page_content or "").split())[:120]
        print(
            f"    {i}. module={md.get('module', 'unknown')} example_index={ex_idx} preview={preview}"
        )


def generate_playbook_from_retrieval(
    user_input: str,
    retrieval_meta: dict,
    *,
    conversation_facts: dict | None = None,
    missing_required_params: list[str] | None = None,
) -> tuple[str, str]:
    """
    Build YAML from retrieved docs + user request using the agent LLM.

    Returns (output_path, yaml_content).
    """
    docs   = retrieval_meta.get("docs", [])
    scores = retrieval_meta.get("scores", [1.0] * len(docs))

    required_ctx, example_ctx, other_ctx, top_examples = _split_context_sections(docs, scores)
    context = build_context_string(docs, scores)
    constraints = _extract_constraints(user_input)
    allowed     = retrieval_meta.get("module_candidates", [])
    required    = retrieval_meta.get("required_params", [])
    missing_required_params = list(missing_required_params or [])
    example_contract = _derive_example_pattern_contract(top_examples)

    model = _playbook_model()
    print(f"\n  [PlaybookGen] Calling agent LLM ({model})...")
    print(f"  [PlaybookGen] Context: {len(docs)} chunks, {len(context)} chars")
    _debug_print_retrieval_inputs(docs, scores, required, top_examples)

    feedback     = "none"
    yaml_content = ""

    for attempt in range(MAX_RETRIES + 1):
        user_msg = PLAYBOOK_USER_MESSAGE_TEMPLATE.format(
            required_params_context=required_ctx,
            example_context=example_ctx,
            other_context=other_ctx,
            question=user_input,
            conversation_facts=conversation_facts or {},
            primary_module=retrieval_meta.get("primary_module", "unknown"),
            primary_collection=retrieval_meta.get("primary_collection", "unknown"),
            allowed_modules=", ".join(allowed) if allowed else "unknown",
            required_params=", ".join(required) if required else "none",
            missing_required_params=", ".join(missing_required_params) if missing_required_params else "none",
            example_pattern_contract=example_contract.get("summary", "none"),
            constraints=_format_constraints(constraints),
            feedback=feedback,
        )
        raw_output = agent_chat(
            user_msg,
            system=PLAYBOOK_SYSTEM_PROMPT,
            temperature=_playbook_temperature(),
            max_tokens=_playbook_max_tokens(),
            model=model,
        )
        yaml_content = extract_yaml(raw_output)
        issues = _collect_generation_issues(
            yaml_content,
            constraints,
            required_params=required,
            example_pattern=example_contract,
        )
        if not issues:
            break
        feedback = "; ".join(issues)
        print(f"  [PlaybookGen] Retry {attempt + 1}/{MAX_RETRIES} due to: {feedback}")

    output_path = save_playbook(yaml_content, user_input, retrieval_meta, llm_model=model)
    print(f"  [PlaybookGen] ✓ Playbook saved → {output_path}")
    return output_path, yaml_content
