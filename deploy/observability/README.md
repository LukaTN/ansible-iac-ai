# Phase 6a — Observability (Docker Compose)

**Status: complete** (August 2026). Local LLMOps parity **without Kubernetes**:
Prometheus metrics, a provisioned Grafana dashboard, and self-hosted Langfuse
traces for every chat generation.

| Stack | Compose file | UI |
|-------|--------------|-----|
| Prometheus + Grafana | `docker-compose.observability.yml` (repo root) | `:9090` / `:3001` |
| Langfuse | `deploy/observability/docker-compose.langfuse.yml` | `:3000` |

## What shipped

### Metrics (`observability/metrics.py` + `GET /metrics`)

| Metric | Meaning |
|--------|---------|
| `ansibleai_http_requests_total` / `_duration_seconds` | Flask RED |
| `ansibleai_generation_started_total` / `_completed_total` / `_duration_seconds` | Celery agent turns |
| `ansibleai_gate_result_total` | Production-gate `passed` / `failed` / `environment` |
| `ansibleai_repair_iterations` | Repair-loop depth |
| `ansibleai_llm_calls_total` / `_duration_seconds` / `_tokens_total` | LLM round-trips |

`/metrics` is public (Prometheus scrape). Network-restrict it in real clusters.

### Langfuse (`observability/tracing.py`, SDK **3.15** ↔ server **v3**)

- One trace per chat turn: root `generate-playbook` (`as_type=agent`)
- `user_id` + `session_id` (= thread id) for Sessions view
- Nested: `retrieve-docs` (retriever), `generate-response` (generation + tokens), `run-production-gate` (evaluator)
- LangGraph `CallbackHandler` for node structure
- Truncated I/O — never full playbook YAML or secrets
- Opt-in via `LANGFUSE_ENABLED` + keys (no-op when disabled)
- Operator-only UI (`:3000`). Members see token spend in AnsibleAI Account; the SPA does not link to Langfuse

### Grafana

Provisioned folder **AnsibleAI** → dashboard **AnsibleAI overview**
(`deploy/observability/grafana/provisioning/…`):

- HTTP rate / latency / 5xx ratio
- Generation throughput & duration
- Gate outcomes & repair iterations
- LLM call rate, latency, tokens by model

Direct link: http://localhost:3001/d/ansibleai-overview/ansibleai-overview

## Start

```bash
# From repo root — app + Prometheus + Grafana
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.observability.yml up -d

# Langfuse (separate Compose project; needs secrets in .env.docker)
docker compose --env-file .env.docker \
  -f deploy/observability/docker-compose.langfuse.yml up -d
```

## Connect Langfuse to the app

1. http://localhost:3000 → create organization + project (if needed).
2. Project → **API Keys** → create `pk-lf-…` / `sk-lf-…`.
3. In `.env.docker`:

```bash
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://host.docker.internal:3000
LANGFUSE_BASE_URL=http://host.docker.internal:3000
LANGFUSE_TRACING_ENVIRONMENT=development
```

(`host.docker.internal` is required: Langfuse is a **separate** Compose
project, so `langfuse-web` is not on the app network.)

4. Recreate api + worker:

```bash
docker compose --env-file .env.docker up -d --force-recreate api worker
```

5. Send a chat message → Langfuse **Traces** shows `generate-playbook`.

## Secrets

Copy the Phase 6a block from `.env.docker.example` into `.env.docker`, then
replace `CHANGE_ME_*`:

```bash
openssl rand -base64 32   # NEXTAUTH_SECRET, SALT, LANGFUSE_REDIS_AUTH, …
openssl rand -hex 32      # ENCRYPTION_KEY (64 hex chars)
```

## Verify

| Check | Expect |
|-------|--------|
| http://localhost:3000 | Langfuse UI |
| http://localhost:3001 → **AnsibleAI** / **AnsibleAI overview** | Dashboard loads |
| http://localhost:9090/targets | `ansibleai-api` **UP** |
| `curl -s http://localhost:5000/metrics` | `ansibleai_*` series |
| Chat turn → Langfuse Traces | Nested agent / retriever / generation / evaluator |

After editing dashboards under `deploy/observability/grafana/`:

```bash
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.observability.yml \
  up -d --force-recreate grafana
```

## Layout

```
deploy/observability/
  README.md                          # this file
  prometheus.yml                     # scrape api:5000/metrics
  docker-compose.langfuse.yml        # Langfuse v3 + deps
  grafana/provisioning/
    datasources/datasource.yml       # Prometheus uid=prometheus
    dashboards/dashboards.yml
    dashboards/json/ansibleai-overview.json
```

App code: `observability/` (`metrics.py`, `tracing.py`), wired from
`app.py`, `tasks.py`, `agent/llm.py`, `agent/graph.py`, `agent/orchestrator.py`.

## Not in 6a (later)

| Item | When |
|------|------|
| Langfuse prompt management (`agent/prompts.py`) | Phase 6b |
| Celery exporter, richer alert rules | Phase 6b |
| Loki / Tempo | Cluster-scale ops |
| vLLM / DCGM GPU dashboards | After Phase 4 (real GPU nodes) |

## Stop

```bash
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.observability.yml \
  stop prometheus grafana

docker compose --env-file .env.docker \
  -f deploy/observability/docker-compose.langfuse.yml down
```
