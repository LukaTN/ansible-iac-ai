# AnsibleAI E2E evaluation

## Golden dataset

`golden_dataset.yaml` defines:

- **5 core cases** — cross-cloud scenarios (EC2, S3, Azure VM, K8s, builtin debug)
- **25 collection cases** — 5 prompts × 5 collections (`amazon.aws`, `azure.azcollection`, `kubernetes.core`, `community.general`, `ansible.builtin`)

Each case includes:

- User **query**
- **Expected** collection + module(s)
- **intent_signals** — for layer 1
- **yaml_contains** / **yaml_contains_any** — structural golden checks (not full YAML diff)

## Five evaluation layers

| Layer | Default weight | Measures |
|-------|----------------|----------|
| Intent understanding | 20% | Query keywords vs target cloud/module |
| Retrieval quality | 20% | RAG `primary_module` / ranked list vs golden |
| Module correctness | 25% | Validator module + YAML uses expected collection |
| Playbook quality | 25% | `validate_playbook` + golden contains rules |
| Runtime behavior | 10% | YAML parse + optional `ansible-playbook --syntax-check` |

**Overall score** = weighted sum (0–100). Pass threshold in reports: **≥ 70**.

## Run full suite

```bash
# Terminal 1
py app.py

# Terminal 2 — all 30 cases via HTTP (sequential, one at a time)
python scripts/run_e2e_eval.py --mode api

# Reports written to:
#   reports/e2e_platform_eval_YYYYMMDD_HHMMSS.json
#   reports/e2e_platform_eval_YYYYMMDD_HHMMSS.md
```

### Options

```bash
python scripts/run_e2e_eval.py --mode api --suite core
python scripts/run_e2e_eval.py --mode api --collection amazon.aws
python scripts/run_e2e_eval.py --mode pipeline --case-id aws-ec2
python scripts/run_e2e_eval.py --mode api --timeout 1200
```

`pipeline` mode runs the LangGraph agent (`agent.handle_message`) in-process (no Flask), still needs Ollama + Chroma.

## Pytest

```bash
pip install -r requirements-dev.txt

# Layer scoring only (no LLM)
pytest tests/test_e2e_layers_unit.py -v

# One live E2E case
set E2E_RUN=1
pytest tests/test_e2e_platform.py -v -s
```
