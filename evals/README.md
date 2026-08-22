# Phase 6b — LLMOps eval loop (Compose)

Closed loop on the laptop Compose stack (not kubeadm):

```
data curation → prompts → model → agent → gate → evals
        ↑                                         |
        └──────── scores / baselines ─────────────┘
```

## Baselines (committed floors for Phase 7)

| File | What it gates |
|------|----------------|
| [baselines/retrieval.json](baselines/retrieval.json) | Retriever-only (`eval_retrieval.py`) |
| [baselines/golden.json](baselines/golden.json) | 5-layer E2E (`run_e2e_eval.py`) |
| [baselines/models.json](baselines/models.json) | Bake-off pairs for `scripts/model_bakeoff.py` |

Refresh scores, then promote only if they beat the floors:

```bash
# Retrieval (needs Postgres + a built pgvector index)
python scripts/eval_retrieval.py --json reports/retrieval.latest.json
python scripts/eval_gate.py --retrieval reports/retrieval.latest.json

# E2E core suite (needs Ollama + index; Compose API or --mode pipeline)
python scripts/run_e2e_eval.py --mode pipeline --suite core
python scripts/eval_gate.py --e2e reports/e2e_platform_eval_*.json
```

`eval_gate.py` exits `1` when a metric is below the committed threshold, and
**2** when the report file is missing (not a pass). Phase 7 CI checks that
contract. Live promotion:

```bash
python scripts/lab_eval_gate.py --live --mode pipeline --suite core
python scripts/lab_eval_gate.py --live --mode api --base-url http://192.168.1.18:30080 --suite core
```

Do not lower floors to hide a regression.

## Knowledge-base coverage

```bash
python scripts/kb_coverage.py
```

Writes `reports/kb_coverage.json` (gitignored) and prints collection counts vs `retrieval_benchmark.json`.

## Prompts

Git remains the source of truth in `backend/agent/prompts.py`. When Langfuse is enabled, `backend/agent/prompt_registry.py` tries `ansibleai-agent-system` and `ansibleai-playbook-system` (label `production`) and falls back to git. Playbook text is **not** Langfuse-compiled so Ansible `{{ jinja }}` stays intact.

Upload git text into Langfuse (Compose API env; no-op without keys):

```bash
docker compose --env-file .env.docker run --rm --no-deps \
  -v ${PWD}/scripts:/app/scripts:ro \
  api exec python /app/scripts/sync_langfuse_prompts.py
```

## Model bake-off

Compare planner/codegen pairs. Each pair is a **new process** (`AGENT_MODEL` is import-time). The script never writes `.env`.

```bash
# Reuse today's incumbent report + run the 7b challenger on Compose
python scripts/model_bakeoff.py --compose --suite core \
  --reuse incumbent=reports/e2e_platform_eval_20260822_135845.json

# Or only print the docker commands
python scripts/model_bakeoff.py --compose --dry-run
```

Winner = highest `avg_overall_score` among pairs that **pass** `eval_gate.py`. Summary: `evals/runs/bakeoff_latest.json`.

## Safety cases

`tests/e2e/golden_dataset.yaml` `safety_cases` (`yaml_must_not_contain`) run with `--suite core` and `--suite safety`.
