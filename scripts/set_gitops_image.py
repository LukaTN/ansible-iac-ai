#!/usr/bin/env python3
"""Pin the GitOps / Helm image tag. Refuses :latest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "deploy" / "helm" / "ansibleai" / "values-gitops-image.yaml"


def validate_tag(tag: str) -> str:
    cleaned = tag.strip().lstrip(":")
    if not cleaned:
        raise ValueError("image tag is empty")
    if cleaned.lower() == "latest":
        raise ValueError("refusing to pin image tag 'latest'")
    return cleaned


def render(*, repository: str, tag: str, pull_policy: str) -> str:
    return (
        "# Phase 7 — image pin for ArgoCD (values-staging / values-prod overlay).\n"
        "# Updated by scripts/set_gitops_image.py or after the image workflow.\n"
        "# Never set tag to latest.\n"
        "image:\n"
        f"  repository: {repository}\n"
        f'  tag: "{tag}"\n'
        f"  pullPolicy: {pull_policy}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Write values-gitops-image.yaml")
    parser.add_argument("--tag", required=True, help="git SHA or lab tag (not latest)")
    parser.add_argument(
        "--repository",
        default="ansibleai/app",
        help="Image repository (lab: ansibleai/app; CI: ghcr.io/<owner>/<repo>)",
    )
    parser.add_argument(
        "--pull-policy",
        default="IfNotPresent",
        choices=("IfNotPresent", "Never", "Always"),
    )
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    try:
        tag = validate_tag(args.tag)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    repository = args.repository.strip()
    if not repository:
        print("repository is empty", file=sys.stderr)
        return 2
    if repository.endswith(":latest") or repository.endswith("/latest"):
        print("refusing a repository that embeds :latest", file=sys.stderr)
        return 2

    args.path.parent.mkdir(parents=True, exist_ok=True)
    args.path.write_text(
        render(repository=repository, tag=tag, pull_policy=args.pull_policy),
        encoding="utf-8",
    )
    print(f"wrote {args.path.relative_to(ROOT)} image={repository}:{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
