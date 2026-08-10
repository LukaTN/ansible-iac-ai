"""Compare two eval_retrieval.py JSON outputs query-by-query.

Usage: python scripts/compare_eval_runs.py reports/a.json reports/b.json
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    path_a, path_b = sys.argv[1], sys.argv[2]
    rows_a = {r["id"]: r for r in json.load(open(path_a, encoding="utf-8"))["rows"]}
    rows_b = {r["id"]: r for r in json.load(open(path_b, encoding="utf-8"))["rows"]}
    common = [i for i in rows_a if i in rows_b]

    print(f"A: {path_a}\nB: {path_b}\n")

    print("--- pack recall (hit) changes ---")
    for i in common:
        if rows_a[i]["hit"] != rows_b[i]["hit"]:
            direction = "GAINED" if rows_b[i]["hit"] else "LOST  "
            print(f"  {direction} {i}: {rows_a[i]['expected']}")

    print("\n--- top1 changes ---")
    for i in common:
        if rows_a[i]["top1"] != rows_b[i]["top1"]:
            direction = "GAINED" if rows_b[i]["top1"] else "LOST  "
            print(
                f"  {direction} {i}: expected={rows_a[i]['expected']} "
                f"A_got={rows_a[i]['got']} B_got={rows_b[i]['got']}"
            )

    print("\n--- rank changes (same hit, moved) ---")
    for i in common:
        ra, rb = rows_a[i]["rank"], rows_b[i]["rank"]
        if ra != rb and rows_a[i]["hit"] and rows_b[i]["hit"]:
            print(f"  {i}: rank {ra} -> {rb}  ({rows_a[i]['expected']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
