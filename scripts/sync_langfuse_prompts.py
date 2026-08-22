#!/usr/bin/env python3
"""Upload git prompt text to Langfuse (label production). Never compile().

Skips when LANGFUSE is disabled or keys are missing. Does not print secrets.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> int:
    from observability.tracing import get_client

    from agent.prompt_registry import AGENT_SYSTEM_NAME, PLAYBOOK_SYSTEM_NAME
    from agent.prompts import _PLAYBOOK_SYSTEM_PROMPT_BASE, AGENT_SYSTEM, PROMPT_VERSION

    client = get_client()
    if client is None:
        print("skipped: Langfuse disabled or keys missing (git remains source of truth)")
        return 0

    payloads = (
        (AGENT_SYSTEM_NAME, AGENT_SYSTEM),
        (PLAYBOOK_SYSTEM_NAME, _PLAYBOOK_SYSTEM_PROMPT_BASE),
    )
    for name, text in payloads:
        client.create_prompt(
            name=name,
            type="text",
            prompt=text,
            labels=["production"],
            config={"prompt_version": PROMPT_VERSION, "compile": False},
        )
        print(f"upserted {name} label=production compile=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
