#!/usr/bin/env python3
"""Score AGENT_MODEL / PLAYBOOK_MODEL pairs against the golden E2E floors.

Each pair is a fresh process (or Compose `exec`) so AGENT_MODEL is not
stuck at import time. This script never writes `.env` — it only
recommends a winner among pairs that pass eval_gate.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS = ROOT / "evals" / "baselines" / "models.json"
RUNS_DIR = ROOT / "evals" / "runs"
JSON_LINE = re.compile(r"JSON:\s+(\S+)")


def slug(tag: str) -> str:
    return tag.replace(":", "-").replace("/", "-")


def pair_slug(agent: str, playbook: str) -> str:
    return f"{slug(agent)}__{slug(playbook)}"


def load_pairs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    out = []
    for p in pairs:
        agent = str(p["agent"]).strip()
        playbook = str(p["playbook"]).strip()
        out.append({
            "id": str(p.get("id") or pair_slug(agent, playbook)),
            "agent": agent,
            "playbook": playbook,
        })
    return out


def parse_pair_flag(raw: str) -> dict:
    if "=" not in raw:
        raise ValueError(f"pair must be agent=playbook, got {raw!r}")
    agent, playbook = raw.split("=", 1)
    agent, playbook = agent.strip(), playbook.strip()
    if not agent or not playbook:
        raise ValueError(f"pair must be agent=playbook, got {raw!r}")
    return {"id": pair_slug(agent, playbook), "agent": agent, "playbook": playbook}


def host_report_path(printed: str) -> Path:
    raw = printed.strip()
    if raw.startswith("/app/reports/"):
        return ROOT / "reports" / Path(raw).name
    return Path(raw)


def parse_report_path(stdout: str) -> Path | None:
    matches = JSON_LINE.findall(stdout)
    if not matches:
        return None
    return host_report_path(matches[-1])


def compose_argv(
    pair: dict,
    *,
    suite: str,
    extra: list[str],
    report_prefix: str,
) -> list[str]:
    cwd = str(ROOT)
    return [
        "docker", "compose", "--env-file", ".env.docker",
        "run", "--rm", "--no-deps",
        "-e", f"AGENT_MODEL={pair['agent']}",
        "-e", f"PLAYBOOK_MODEL={pair['playbook']}",
        "-v", f"{cwd}/scripts:/app/scripts:ro",
        "-v", f"{cwd}/evals:/app/evals:ro",
        "-v", f"{cwd}/tests:/app/tests:ro",
        "-v", f"{cwd}/reports:/app/reports",
        "api", "exec", "python", "scripts/run_e2e_eval.py",
        "--mode", "pipeline",
        "--suite", suite,
        "--report-prefix", report_prefix,
        *extra,
    ]


def host_argv(pair: dict, *, suite: str, extra: list[str], report_prefix: str) -> list[str]:
    return [
        sys.executable, str(ROOT / "scripts" / "run_e2e_eval.py"),
        "--mode", "pipeline",
        "--suite", suite,
        "--report-prefix", report_prefix,
        *extra,
    ]


def gate_e2e(report: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "eval_gate.py"), "--e2e", str(report)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, text.strip()


def scores_from_report(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    g = data.get("global") or {}
    return {
        "avg_overall_score": float(g.get("avg_overall_score") or 0),
        "pass_rate_70": float(g.get("pass_rate_70") or 0),
        "total_cases": int(data.get("total_cases") or 0),
    }


def pick_winner(rows: list[dict]) -> dict | None:
    eligible = [r for r in rows if r.get("gate") == "passed"]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda r: (r.get("avg_overall_score") or 0, r.get("pass_rate_70") or 0),
    )


def run_pair(
    pair: dict,
    *,
    compose: bool,
    suite: str,
    extra: list[str],
    dry_run: bool,
) -> dict:
    prefix = f"e2e_bakeoff_{pair['id']}"
    argv = (
        compose_argv(pair, suite=suite, extra=extra, report_prefix=prefix)
        if compose
        else host_argv(pair, suite=suite, extra=extra, report_prefix=prefix)
    )
    env = os.environ.copy()
    env["AGENT_MODEL"] = pair["agent"]
    env["PLAYBOOK_MODEL"] = pair["playbook"]
    row = {
        "id": pair["id"],
        "agent": pair["agent"],
        "playbook": pair["playbook"],
        "command": argv,
        "reused": False,
    }
    if dry_run:
        row["gate"] = "skipped"
        return row

    print("=" * 60)
    print(f"  BAKE-OFF {pair['id']}: {pair['agent']} / {pair['playbook']}")
    print("=" * 60)
    proc = subprocess.run(argv, cwd=ROOT, env=env, text=True, capture_output=False)
    # Re-run capture is not possible after the fact; parse newest prefix file.
    reports = sorted((ROOT / "reports").glob(f"{prefix}_*.json"))
    if not reports:
        row.update({"gate": "error", "error": f"no report for prefix {prefix}", "exit": proc.returncode})
        return row
    report = reports[-1]
    scores = scores_from_report(report)
    code, gate_text = gate_e2e(report)
    row.update({
        "report": str(report.relative_to(ROOT)).replace("\\", "/"),
        "exit": proc.returncode,
        "gate": "passed" if code == 0 else "failed",
        "gate_output": gate_text,
        **scores,
    })
    return row


def main() -> int:
    p = argparse.ArgumentParser(description="Bake off Ollama planner/codegen pairs")
    p.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    p.add_argument("--pair", action="append", dest="pairs", help="agent=playbook (repeatable)")
    p.add_argument(
        "--reuse",
        action="append",
        default=[],
        metavar="ID=REPORT",
        help="reuse an existing e2e JSON for pair id (skip that run)",
    )
    p.add_argument("--suite", default="core", choices=("core", "collections", "safety", "all"))
    p.add_argument("--case-id", action="append", dest="case_ids")
    p.add_argument("--compose", action="store_true", help="run each pair via docker compose")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out", type=Path, default=RUNS_DIR / "bakeoff_latest.json")
    args = p.parse_args()

    extra: list[str] = []
    for cid in args.case_ids or []:
        extra.extend(["--case-id", cid])

    if args.pairs:
        pairs = [parse_pair_flag(s) for s in args.pairs]
    else:
        pairs = load_pairs(args.models)
    if not pairs:
        p.error("no pairs in --pair or models.json")

    reuse: dict[str, Path] = {}
    for item in args.reuse:
        if "=" not in item:
            p.error("--reuse must be ID=path")
        pid, path = item.split("=", 1)
        reuse[pid.strip()] = Path(path.strip())

    rows: list[dict] = []
    for pair in pairs:
        if pair["id"] in reuse:
            report = reuse[pair["id"]]
            if not report.is_file():
                print(f"missing reuse report: {report}", file=sys.stderr)
                return 2
            scores = scores_from_report(report)
            code, gate_text = gate_e2e(report)
            rows.append({
                "id": pair["id"],
                "agent": pair["agent"],
                "playbook": pair["playbook"],
                "reused": True,
                "report": str(report).replace("\\", "/"),
                "gate": "passed" if code == 0 else "failed",
                "gate_output": gate_text,
                **scores,
            })
            print(f"REUSED {pair['id']} -> {report} gate={rows[-1]['gate']}")
            continue
        rows.append(run_pair(
            pair,
            compose=args.compose,
            suite=args.suite,
            extra=extra,
            dry_run=args.dry_run,
        ))

    winner = None if args.dry_run else pick_winner(rows)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "suite": args.suite,
        "compose": args.compose,
        "dry_run": args.dry_run,
        "pairs": rows,
        "winner": (
            {
                "id": winner["id"],
                "agent": winner["agent"],
                "playbook": winner["playbook"],
                "avg_overall_score": winner.get("avg_overall_score"),
                "pass_rate_70": winner.get("pass_rate_70"),
            }
            if winner
            else None
        ),
        "promote": (
            "Set AGENT_MODEL / PLAYBOOK_MODEL to the winner only after reviewing the reports. "
            "This script does not write .env."
            if winner
            else "No pair passed eval_gate.py — keep the incumbent."
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print()
    print("=" * 60)
    if winner:
        print(f"  WINNER (gate-pass): {winner['id']}  "
              f"{winner['agent']} / {winner['playbook']}  "
              f"avg={winner.get('avg_overall_score')}  "
              f"pass70={winner.get('pass_rate_70')}%")
    else:
        print("  NO WINNER — no pair passed eval_gate.py" if not args.dry_run else "  DRY RUN")
    print(f"  Summary: {args.out}")
    print("=" * 60)
    if args.dry_run:
        return 0
    return 0 if winner else 1


if __name__ == "__main__":
    raise SystemExit(main())
