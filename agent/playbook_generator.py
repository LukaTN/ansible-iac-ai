"""
=============================================================
  AnsibleAI — Playbook drafting via agent LLM

  Retrieval metadata (docs + scores) comes from RAG; this module
  performs ONE draft (or repair) LLM call per invocation. The
  LangGraph agent owns the quality loop: it validates each draft
  (full validator + ansible-lint) and calls back into
  `draft_playbook_from_retrieval` with structured gate feedback
  until the production gate passes.
=============================================================
"""

from __future__ import annotations

import os
import re

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from agent.llm import AGENT_MODEL
from agent.llm import chat as agent_chat
from agent.prompts import PLAYBOOK_USER_MESSAGE_TEMPLATE, build_playbook_system_prompt
from logging_setup import get_logger
from rag.generator import (
    _collect_generation_issues,
    _extract_constraints,
    _format_constraints,
    _fqcn_match_task_module,
    ansible_jinja_var,
    build_context_string,
    extract_yaml,
    quote_bare_jinja,
    save_playbook,
)
from rag.retrieval_utils import format_ranked_modules_lines, list_ranked_modules

log = get_logger(__name__)


def _playbook_model() -> str:
    m = (os.getenv("PLAYBOOK_MODEL") or "").strip()
    return m if m else AGENT_MODEL


def _playbook_max_tokens() -> int:
    raw = (os.getenv("PLAYBOOK_MAX_TOKENS") or "").strip()
    if raw.isdigit():
        return max(512, int(raw))
    return 3500


def _playbook_temperature() -> float:
    # Low temperature keeps playbook structure/hygiene consistent across runs.
    raw = (os.getenv("PLAYBOOK_TEMPERATURE") or "").strip()
    try:
        return float(raw) if raw else 0.1
    except ValueError:
        return 0.1


def _render_docs(docs: list, scores: list[float]) -> str:
    if not docs:
        return "(none)"
    return build_context_string(docs, scores)


_CHUNK_ORDER = {"overview": 0, "required_params": 1, "optional_params": 2}


def _split_context_sections(docs: list, scores: list[float]) -> tuple[str, str, list]:
    required_docs = []
    example_docs = []
    required_scores: list[float] = []
    example_scores: list[float] = []
    for doc, score in zip(docs, scores):
        ctype = (doc.metadata or {}).get("chunk_type")
        if ctype == "required_params":
            required_docs.append(doc)
            required_scores.append(score)
        elif ctype == "example":
            example_docs.append(doc)
            example_scores.append(score)
    top_examples = example_docs[:5]
    top_example_scores = example_scores[:5]
    return (
        _render_docs(required_docs, required_scores),
        _render_docs(top_examples, top_example_scores),
        top_examples,
    )


def _build_module_grouped_context(
    docs: list,
    scores: list[float],
    ranked_modules: list[dict],
) -> str:
    """Group non-example chunks under their module, in retrieval-ranked order."""
    by_mod: dict[str, list[tuple]] = {}
    for doc, score in zip(docs, scores):
        md = doc.metadata or {}
        mod = md.get("module")
        if not mod or md.get("chunk_type") == "example":
            continue
        by_mod.setdefault(mod, []).append((doc, float(score)))

    order: list[str] = []
    seen: set[str] = set()
    for entry in ranked_modules or []:
        m = entry.get("module")
        if m and m in by_mod and m not in seen:
            order.append(m)
            seen.add(m)
    for m in sorted(by_mod.keys()):
        if m not in seen:
            order.append(m)

    blocks: list[str] = []
    for m in order:
        items = sorted(
            by_mod[m],
            key=lambda x: (
                _CHUNK_ORDER.get((x[0].metadata or {}).get("chunk_type"), 5),
                -x[1],
            ),
        )
        sub_docs = [d for d, _ in items]
        sub_scores = [s for _, s in items]
        rank_hint = next((e for e in (ranked_modules or []) if e.get("module") == m), None)
        if rank_hint:
            header = (
                f"### `{m}` (retrieval rank #{rank_hint.get('rank')}, "
                f"top_score≈{rank_hint.get('top_score')})"
            )
        else:
            header = f"### `{m}`"
        blocks.append(header + "\n" + build_context_string(sub_docs, sub_scores))
    return "\n\n".join(blocks) if blocks else "(none)"


def _derive_example_pattern_contract(example_docs: list) -> dict:
    """Lightweight hint only — do not surface example key lists (avoids copy-paste pressure)."""
    modules: list[str] = []
    if not example_docs:
        return {"summary": "none", "modules": [], "recurring_keys": []}
    for d in example_docs:
        txt = d.page_content or ""
        for m in re.findall(r"^\s{2,}([a-zA-Z0-9_.]+)\s*:\s*$", txt, flags=re.MULTILINE):
            if m not in modules:
                modules.append(m)
    summary = "modules=" + (", ".join(modules[:4]) if modules else "unknown")
    return {"summary": summary, "modules": modules, "recurring_keys": []}


def _enforce_required_placeholders(
    yaml_content: str,
    primary_module: str | None,
    required_params: list[str],
    required_params_by_module: dict[str, list[str]] | None = None,
) -> str:
    """
    Fill missing required params with quoted Jinja placeholders
    (`"{{ var_<param> }}"` via ansible_jinja_var) for every task whose module
    appears in required_params_by_module (multi-module playbooks).
    """
    by_mod = dict(required_params_by_module or {})
    if not by_mod and primary_module and required_params:
        by_mod[primary_module] = list(required_params)
    if not by_mod or not yaml_content.strip():
        return yaml_content

    try:
        parsed = yaml.safe_load(yaml_content)
    except Exception:
        return yaml_content

    if not isinstance(parsed, list) or not parsed:
        return yaml_content

    fqcn_list = list(by_mod.keys())
    added: list[str] = []
    for play in parsed:
        if not isinstance(play, dict):
            continue
        tasks = play.get("tasks")
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict) or "block" in task:
                continue
            for key, val in task.items():
                if key == "name" or not isinstance(val, dict):
                    continue
                fqcn = _fqcn_match_task_module(key, fqcn_list)
                if not fqcn:
                    continue
                for param in by_mod.get(fqcn, []):
                    p = (param or "").strip()
                    if not p or p in val:
                        continue
                    val[p] = ansible_jinja_var(p)
                    added.append(f"{fqcn}:{p}")

    if not added:
        return yaml_content

    log.info("playbook.placeholders_added", params=added)
    rendered = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)
    if not rendered.lstrip().startswith("---"):
        rendered = "---\n" + rendered
    return rendered.strip()


def draft_playbook_from_retrieval(
    user_input: str,
    retrieval_meta: dict,
    *,
    conversation_facts: dict | None = None,
    feedback: str = "none",
    fix_plan: str = "none",
) -> tuple[str, list[str]]:
    """
    ONE LLM draft/repair pass. Returns (yaml_content, generation_issues).

    `feedback` and `fix_plan` carry the gate failures and the CoT repair
    plan from the previous iteration; the agent graph decides whether to
    call again. Saving to disk is the caller's responsibility.
    """
    docs   = retrieval_meta.get("docs", [])
    scores = retrieval_meta.get("scores", [1.0] * len(docs))

    required_ctx, example_ctx, top_examples = _split_context_sections(docs, scores)
    ranked_modules = retrieval_meta.get("ranked_modules") or list_ranked_modules(
        docs, scores, limit=8
    )
    module_grouped_ctx = _build_module_grouped_context(docs, scores, ranked_modules)
    ranked_summary = format_ranked_modules_lines(ranked_modules)
    constraints = _extract_constraints(user_input)
    allowed     = retrieval_meta.get("module_candidates", [])
    required    = retrieval_meta.get("required_params", [])
    example_contract = _derive_example_pattern_contract(top_examples)

    conversation_facts_str = (
        "\n".join(
            f"  {k}: {v}"
            for k, v in (conversation_facts or {}).items()
            if v is not None
        )
        or "(none)"
    )

    model = _playbook_model()
    is_repair = feedback not in ("", "none")
    log.info(
        "playbook.generation.start",
        pass_type="repair" if is_repair else "draft",
        model=model,
        chunks=len(docs),
    )

    user_msg = PLAYBOOK_USER_MESSAGE_TEMPLATE.format(
        required_params_context=required_ctx,
        example_context=example_ctx,
        module_grouped_context=module_grouped_ctx,
        ranked_modules_summary=ranked_summary,
        question=user_input,
        conversation_facts=conversation_facts_str,
        primary_module=retrieval_meta.get("primary_module", "unknown"),
        primary_collection=retrieval_meta.get("primary_collection", "unknown"),
        allowed_modules=", ".join(allowed) if allowed else "unknown",
        required_params=", ".join(required) if required else "none",
        example_pattern_contract=example_contract.get("summary", "none"),
        constraints=_format_constraints(constraints),
        feedback=feedback or "none",
        fix_plan=fix_plan or "none",
    )
    system_prompt = build_playbook_system_prompt(
        retrieval_meta.get("primary_collection")
    )
    # Slight temperature bump on repair passes so a different fix is possible.
    temperature = _playbook_temperature() + (0.1 if is_repair else 0.0)
    raw_output = agent_chat(
        user_msg,
        system=system_prompt,
        temperature=min(temperature, 0.35),
        max_tokens=_playbook_max_tokens(),
        model=model,
    )
    yaml_content = extract_yaml(raw_output)

    # Must precede placeholder enforcement, which needs the document to parse.
    yaml_content, jinja_fixes = quote_bare_jinja(yaml_content)
    if jinja_fixes:
        log.info("playbook.generation.jinja_quoted", fixes=jinja_fixes)

    yaml_content = _enforce_required_placeholders(
        yaml_content=yaml_content,
        primary_module=retrieval_meta.get("primary_module"),
        required_params=required,
        required_params_by_module=retrieval_meta.get("required_params_by_module"),
    )

    issues = _collect_generation_issues(
        yaml_content,
        constraints,
        required_params=required,
        required_params_by_module=retrieval_meta.get("required_params_by_module") or None,
    )
    if issues:
        log.info("playbook.generation.issues_flagged", issues=issues)

    return yaml_content, issues


def generate_playbook_from_retrieval(
    user_input: str,
    retrieval_meta: dict,
    *,
    conversation_facts: dict | None = None,
    missing_required_params: list[str] | None = None,
) -> tuple[str, str]:
    """
    Compatibility wrapper for non-agent callers (rag/pipeline.py CLI,
    rag/evaluator.py, scripts/trace_query.py): single draft pass + save.
    The chat agent uses `draft_playbook_from_retrieval` + the graph's
    validate/repair loop instead.

    Returns (output_path, yaml_content).
    """
    _ = missing_required_params  # legacy argument, no longer used
    yaml_content, _issues = draft_playbook_from_retrieval(
        user_input,
        retrieval_meta,
        conversation_facts=conversation_facts,
    )
    output_path = save_playbook(
        yaml_content, user_input, retrieval_meta, llm_model=_playbook_model()
    )
    log.info("playbook.saved", path=output_path)
    return output_path, yaml_content
