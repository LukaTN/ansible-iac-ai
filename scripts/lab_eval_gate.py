#!/usr/bin/env python3
"""Run eval_gate.py against a report, optionally after a live E2E suite.

Missing reports exit 2 (not a pass). GitHub-hosted CI cannot reach the lab
Ingress; use this on Compose or a self-hosted runner labeled lab.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))


def _newest_e2e_report() -> Path | None:
    reports = sorted(
        (ROOT / "reports").glob("e2e_platform_eval_*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    return reports[-1] if reports else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Lab / CI eval gate wrapper")
    parser.add_argument("--live", action="store_true", help="run run_e2e_eval.py first")
    parser.add_argument("--mode", choices=("api", "pipeline"), default="api")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument(
        "--suite",
        choices=("core", "safety", "collections", "all"),
        default="core",
    )
    parser.add_argument("--e2e", type=Path, help="existing E2E JSON")
    parser.add_argument("--retrieval", type=Path, help="existing retrieval JSON")
    args = parser.parse_args()

    e2e_path = args.e2e
    if args.live:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "run_e2e_eval.py"),
            "--mode",
            args.mode,
            "--base-url",
            args.base_url,
            "--suite",
            args.suite,
        ]
        print("+", " ".join(cmd), flush=True)
        live = subprocess.run(cmd, cwd=ROOT, check=False)
        if live.returncode != 0:
            return live.returncode
        e2e_path = e2e_path or _newest_e2e_report()
        if e2e_path is None:
            print("missing e2e report after live run", file=sys.stderr)
            return 2

    if e2e_path is None and args.retrieval is None:
        print("pass --e2e and/or --retrieval, or --live", file=sys.stderr)
        return 2

    gate = [
        sys.executable,
        str(ROOT / "scripts" / "eval_gate.py"),
    ]
    if e2e_path is not None:
        gate.extend(["--e2e", str(e2e_path)])
    if args.retrieval is not None:
        gate.extend(["--retrieval", str(args.retrieval)])
    print("+", " ".join(gate), flush=True)
    return subprocess.run(gate, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
