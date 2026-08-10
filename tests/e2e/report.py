"""Write JSON + Markdown E2E evaluation reports under reports/."""

from __future__ import annotations

import json
import os
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPORTS_DIR = os.path.join(ROOT, "reports")


def write_report(summary: dict, *, prefix: str = "e2e_platform_eval") -> tuple[str, str]:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(REPORTS_DIR, f"{prefix}_{ts}.json")
    md_path = os.path.join(REPORTS_DIR, f"{prefix}_{ts}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    md = render_markdown(summary)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    return json_path, md_path


def render_markdown(summary: dict) -> str:
    lines = [
        "# AnsibleAI — E2E Platform Evaluation Report",
        "",
        f"- **Generated (UTC):** {summary.get('generated_at')}",
        f"- **Mode:** `{summary.get('mode')}`",
    ]
    if summary.get("base_url"):
        lines.append(f"- **API:** `{summary['base_url']}`")
    lines.append(f"- **Sequential:** {summary.get('sequential')}")
    lines.append(f"- **Total cases:** {summary.get('total_cases')}")
    g = summary.get("global") or {}
    lines.extend(
        [
            "",
            "## Global summary",
            "",
            f"- **Average overall score:** {g.get('avg_overall_score', 0)} / 100",
            f"- **Pass rate (score ≥ 70):** {g.get('pass_rate_70', 0)}%",
            "",
            "## Layer methodology",
            "",
            "Scores are weighted across five layers (AI agent testing best practice):",
            "",
            "| Layer | Weight | What it measures |",
            "|-------|--------|------------------|",
        ]
    )
    w = summary.get("layer_weights") or {}
    desc = {
        "intent_understanding": "Query signals match target cloud/module intent",
        "retrieval_quality": "RAG primary collection/module vs golden expectation",
        "module_correctness": "Detected module + YAML uses expected collection",
        "playbook_quality": "Validator pass + golden `yaml_contains` rules",
        "runtime_behavior": "YAML parses; optional `ansible-playbook --syntax-check`",
    }
    for name, weight in w.items():
        pct = int(weight * 100)
        lines.append(f"| {name} | {pct}% | {desc.get(name, '')} |")

    lines.extend(["", "## Per-collection summary", ""])
    for coll, stats in sorted((summary.get("by_collection") or {}).items()):
        lines.append(f"### `{coll}`")
        lines.append("")
        lines.append(
            f"- Cases: {stats.get('total')} | Completed: {stats.get('completed')} | "
            f"Errors: {stats.get('errors')} | Avg score: **{stats.get('avg_overall_score')}** | "
            f"Pass ≥70: {stats.get('pass_rate_70')}%"
        )
        layer_avgs = stats.get("avg_layer_scores") or {}
        if layer_avgs:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(layer_avgs.items()))
            lines.append(f"- Layer averages: {parts}")
        lines.append("")

    lines.extend(["", "## Per-case results", ""])
    for r in summary.get("results") or []:
        ev = r.get("evaluation") or {}
        sc = ev.get("overall_score", "—")
        cid = r.get("case_id", "?")
        err = r.get("error")
        lines.append(f"### `{cid}` — score **{sc}**")
        lines.append("")
        if err:
            lines.append(f"- **Error:** {err}")
        lines.append(f"- **Duration:** {r.get('duration_sec')}s")
        lines.append(f"- **Expected collection:** `{r.get('expected', {}).get('collection')}`")
        lines.append(f"- **Detected module:** `{r.get('detected_module')}`")
        rag = r.get("retrieval_meta") or {}
        lines.append(f"- **RAG primary:** `{rag.get('primary_module')}`")
        v = r.get("validation") or {}
        lines.append(f"- **Validator valid:** {v.get('is_valid')}")
        if v.get("errors"):
            lines.append(f"- **Validation errors:** {'; '.join(v['errors'][:3])}")
        layers = ev.get("layers") or {}
        if layers:
            lines.append("")
            lines.append("| Layer | Score |")
            lines.append("|-------|-------|")
            for ln, ld in layers.items():
                lines.append(f"| {ln} | {ld.get('score')} |")
        pq = layers.get("playbook_quality") or {}
        missing = pq.get("missing_contains") or []
        if missing:
            lines.append(f"- **Missing yaml_contains:** {', '.join(missing)}")
        lines.append("")
        pb = (r.get("playbook") or "")[:400]
        if pb:
            lines.append("```yaml")
            lines.append(pb)
            if len(r.get("playbook") or "") > 400:
                lines.append("# ... truncated")
            lines.append("```")
        lines.append("")

    return "\n".join(lines)
