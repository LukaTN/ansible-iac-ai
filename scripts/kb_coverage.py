#!/usr/bin/env python3
"""Knowledge-base coverage vs the retrieval benchmark (Phase 6b data curation)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARSED = ROOT / "data" / "parsed"
BENCHMARK = ROOT / "backend" / "rag" / "retrieval_benchmark.json"
OUT_DIR = ROOT / "reports"


def _module_from_json(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("module") or "").strip()


def parsed_inventory(parsed_dir: Path) -> dict[str, list[str]]:
    by_ns: dict[str, list[str]] = {}
    if not parsed_dir.is_dir():
        return by_ns
    for ns_dir in sorted(p for p in parsed_dir.iterdir() if p.is_dir()):
        mods = []
        for fp in sorted(ns_dir.glob("*.json")):
            name = _module_from_json(fp)
            if name:
                mods.append(name)
        by_ns[ns_dir.name] = mods
    return by_ns


def benchmark_expected(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    samples = raw["samples"] if isinstance(raw, dict) else raw
    out: list[str] = []
    for s in samples:
        exp = s.get("expected_module")
        if exp:
            out.append(exp)
        out.extend(s.get("acceptable") or [])
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="KB coverage vs retrieval benchmark")
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when benchmark modules are missing from data/parsed",
    )
    args = p.parse_args()

    inventory = parsed_inventory(PARSED)
    all_mods = {m for mods in inventory.values() for m in mods}
    expected = benchmark_expected(BENCHMARK) if BENCHMARK.is_file() else []
    missing = sorted({e for e in expected if e not in all_mods})

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "parsed_dir": str(PARSED),
        "parsed_present": PARSED.is_dir(),
        "collections": {
            ns: {"modules": len(mods)}
            for ns, mods in inventory.items()
        },
        "total_modules": len(all_mods),
        "benchmark_expected": len(set(expected)),
        "benchmark_missing_from_parsed": missing,
        "coverage_ratio": (
            round((len(set(expected)) - len(missing)) / len(set(expected)), 4)
            if expected
            else None
        ),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "kb_coverage.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"parsed dir     : {PARSED} ({'ok' if PARSED.is_dir() else 'MISSING'})")
    print(f"total modules  : {report['total_modules']}")
    for ns, stats in report["collections"].items():
        print(f"  {ns:<28} {stats['modules']:>5}")
    print(f"benchmark mods : {report['benchmark_expected']}")
    print(f"missing        : {len(missing)}")
    for m in missing[:20]:
        print(f"  - {m}")
    if len(missing) > 20:
        print(f"  … {len(missing) - 20} more")
    print(f"wrote {out_path}")
    if args.strict and missing:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
