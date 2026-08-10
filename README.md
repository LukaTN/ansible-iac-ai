# AnsibleAI

AI-powered Infrastructure-as-Code assistant that generates Ansible playbooks grounded on indexed official module documentation.

The stack combines a **React 19** web UI, a **Flask + PostgreSQL** API, a **Celery worker** running a **LangGraph agent** (reason → tools → draft → production gate → repair loop), and a **hybrid RAG pipeline** (pgvector dense search + BM25, OpenAI-compatible embeddings).

Phases **0–3** of the production LLMOps plan are complete (auth, containers, async workers, Postgres+pgvector). See [docs/production_progress_report.md](docs/production_progress_report.md).

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Browser — React SPA (frontend/) ──build──► static/dist/                 │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ REST + WebSocket + SSE
┌────────────────────────────────▼─────────────────────────────────────────┐
│  Flask API (gunicorn / gevent)                                           │
│  • Auth (Flask-Login, CSRF, rate limits, audit log)                      │
│  • POST /api/chat → 202 + Celery enqueue (Socket.IO progress)            │
│  • KB scrape / rollback / docs admin (SSE via Redis streams)             │
└────────────┬───────────────────────────────┬─────────────────────────────┘
             │ Redis                         │
             ▼                               ▼
┌────────────────────────────┐   ┌──────────────────────────────────────────┐
│  Celery worker             │   │  pipeline/ + rag/                        │
│  LangGraph agent loop      │◄──│  KB scrape/parse, pgvector indexer,      │
│  ansible-lint gate         │   │  hybrid retriever (dense + BM25)         │
│  MinIO playbook archive    │   └──────────────────┬───────────────────────┘
└────────────┬───────────────┘                      │
             ▼                                      ▼
     PostgreSQL 16 + pgvector              Embeddings (/v1/embeddings)
     (users, chat, vectors)                TEI or Ollama (nomic-embed-text)
             │
             ▼
    Ollama and/or OpenRouter
    (planner + codegen LLMs)
```

| Layer | Tech | Role |
|-------|------|------|
| Frontend | React 19, TypeScript, Vite, Tailwind v4, Radix UI | Chat UI, stats, docs admin, auth pages |
| Backend | Flask 3, SQLAlchemy, psycopg2, Flask-SocketIO, Celery | REST API, WebSocket, SSE, async generation |
| Worker | Celery + Redis | LangGraph runs, cancel, realtime emits |
| **Agent** | LangGraph (`agent/graph.py`), tools, LLM client | CoT reasoning, draft → validate → repair |
| RAG | LangChain, pgvector, BM25, `rag/embeddings.py` | Hybrid retrieval over Ansible module docs |
| Artifacts | MinIO (S3) | Durable playbook archive |
| Auth | Flask-Login, argon2id, Redis sessions | Users, CSRF, rate limits, audit log |

## Recommended: Docker Compose

Local parity stack (API, worker, Postgres+pgvector, Redis, MinIO, one-shot migrate):

```bash
cp .env.docker.example .env.docker
# Edit SECRET_KEY, POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, BOOTSTRAP_ADMIN_*

# Host Ollama must be running (embeddings + LLMs)
ollama pull nomic-embed-text
ollama pull gemma3:12b
ollama pull qwen2.5-coder:14b

docker compose --env-file .env.docker up --build -d

# First-time vector index (into Postgres via pgvector)
docker compose --env-file .env.docker exec api python rag/indexer.py --reset
```

Open **http://localhost:5000** and sign in with the bootstrap admin from `.env.docker`.

The image installs Galaxy collections (`amazon.aws`, `azure.azcollection`, `community.general`, `kubernetes.core`) so ansible-lint can resolve modules the agent generates. If you see `syntax-check[unknown-module]` for those collections, rebuild the image: `docker compose --env-file .env.docker build --no-cache api`.

## The agent (`agent/`)

One LangGraph state machine: several nodes, one shared state. It decides *when* to retrieve docs and **loops** on YAML until the production gate passes (or the iteration budget is exhausted).

### Graph

```
START → reason ──→ tools ──→ reason
           │
           ├──→ ask_user → END
           │
           ├──→ draft → gate ──→ reason   (repair loop)
           │              │
           │              └──→ respond → END
           └──→ respond → END
```

| Node | What happens |
|------|----------------|
| **reason** | CoT LLM decision (JSON): intent, search query, or clarifying questions. After a failed gate it produces a **fix plan**. |
| **tools** | `search_docs` (hybrid RAG + collection routing); `validate_yaml` for pasted YAML |
| **draft** | One YAML generation/repair pass via the playbook LLM |
| **gate** | Full validator + **ansible-lint**. Production-ready = 0 errors, lint passed, no placeholders |
| **respond** | Final reply with gate verdict / synthesis |
| **ask_user** | Stops with clarifying questions when the request cannot be grounded |

Loop budget: `AGENT_MAX_ITERATIONS` (default 4). Environment failures (lint backend missing) do not burn repair iterations.

### Tools (`agent/tools.py`)

| Tool | Purpose |
|------|---------|
| `search_docs` | Hybrid search over pgvector + BM25 |
| `draft_playbook` | One draft/repair YAML pass |
| `validate_playbook_file` / `validate_yaml` | KB-aware validator + ansible-lint |
| `get_module_info` | Structured module reference from the knowledge base |

### LLM providers (`agent/llm.py`)

| Provider | Config |
|----------|--------|
| **ollama** | `AGENT_LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL`, `AGENT_MODEL` |
| **openrouter** | `OPENROUTER_API_KEY`, `AGENT_MODEL` (+ optional `AGENT_FALLBACK_MODELS`) |

Playbook YAML uses `PLAYBOOK_MODEL` when set (e.g. `qwen2.5-coder:14b`); otherwise `AGENT_MODEL`.

### Chat pipeline

`POST /api/chat` persists the user message, enqueues `tasks.run_generation`, and returns **202**. Progress and the final assistant message arrive over Socket.IO (`generation_progress` / `generation_complete`). Poll `GET /api/chat/status/:thread_id` as a fallback if the socket drops.

### Agent package layout

```
agent/
├── orchestrator.py        # handle_message(), AgentResponse
├── graph.py               # LangGraph nodes
├── state.py               # AgentState, production gate
├── tools.py               # search_docs, draft_*, validate_*, get_module_info
├── playbook_generator.py  # RAG context → YAML draft/repair
├── llm.py                 # OpenRouter / Ollama client
├── prompts.py             # Reason / repair / respond / playbook prompts
├── cancel.py              # Cooperative cancel (memory / Redis)
└── collections.py         # KB-derived collection allow-list
```

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| **Docker Desktop** | recent | Recommended full stack |
| **Ollama** | ≥ 0.1.26 | Embeddings (`/v1/embeddings`) + optional LLMs |
| **Python** | 3.11+ | Host-only / test development |
| **Node.js** | 20+ | Frontend when developing outside Docker |
| **OpenRouter API key** | optional | Cloud planner instead of Ollama |

## Host development (optional)

Use this when you are not running the full Compose stack.

### 1. Configure

```bash
cp .env.example .env
# DATABASE_URL=postgresql+psycopg2://ansibleai:ansibleai@localhost:5432/ansibleai
# EMBEDDING_BASE_URL=http://localhost:11434/v1
```

### 2. Python env + DB

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# Postgres 16 + pgvector must be reachable (Compose `db` service is fine)
python -m alembic upgrade head
python scripts/seed_admin.py
```

### 3. Models + index

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:14b   # optional dedicated codegen
python rag/indexer.py --reset  # embeds into Postgres
```

### 4. Frontend + run

```bash
npm install && npm run build
python app.py                  # http://localhost:5000
```

Without Redis/Celery, `CELERY_TASK_ALWAYS_EAGER=true` (dev default) runs generation in-process. Prefer Compose for realistic async + lint behavior.

### Dev UI with HMR

```bash
# Terminal 1
python app.py

# Terminal 2
npm run dev                    # http://localhost:5173 (proxies API)
```

### ansible-lint on Windows (host only)

`ansible-lint` does not run under native Windows Python. In Docker it runs natively inside the Linux image. On the host, use WSL or Docker (`ANSIBLE_LINT_MODE=wsl` / `docker`). See older setup notes in git history if you need Hyper-V/WSL1 workarounds.

## Environment variables

Templates: [`.env.example`](.env.example) (host) and [`.env.docker.example`](.env.docker.example) (Compose).

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | yes | Session / CSRF signing |
| `DATABASE_URL` | yes | `postgresql+psycopg2://...` |
| `EMBEDDING_BASE_URL` | yes* | OpenAI-compatible embeddings (`…/v1`) |
| `EMBEDDING_MODEL` | no | Default `nomic-embed-text` |
| `EMBEDDING_DIMENSIONS` | no | Default `768` |
| `AGENT_LLM_PROVIDER` | no | `ollama` or `openrouter` |
| `AGENT_MODEL` / `PLAYBOOK_MODEL` | no | Planner / codegen models |
| `REDIS_URL` | Compose | Sessions, cancel, Socket.IO queue, Celery |
| `CELERY_BROKER_URL` | Compose | Worker broker |
| `CORS_ORIGINS` | no | Must include the browser origin (`:5000` and/or `:5173`) |

\*Falls back to `OLLAMA_BASE_URL/v1` when empty.

## API overview

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/auth/*` | various | Register, login, logout, me, password, CSRF |
| `/api/chat` | POST | Enqueue generation → **202** |
| `/api/chat/status/:id` | GET | Job / thread generation status |
| `/api/threads` | GET, DELETE | List / clear own threads |
| `/api/threads/:id` | GET, PATCH, DELETE | Open / rename / delete |
| `/stats` | GET | Generation statistics |
| `/rag/status` | GET | pgvector chunk count + embed model |
| `/healthz` / `/readyz` | GET | Liveness / readiness |
| `/docs/*` | various | KB health, scrape, rollback, SSE logs |

## RAG quality

Retrieval is **hybrid** (semantic vectors + BM25), with collection routing and module-level ranking.

Benchmark (no LLM — embedding + retrieval only):

```bash
# Inside Compose API container (index must exist)
docker compose --env-file .env.docker exec api \
  python scripts/eval_retrieval.py --json reports/retrieval_final.json

# Diff two runs
python scripts/compare_eval_runs.py reports/retrieval_baseline.json reports/retrieval_final.json
```

Dataset: `rag/retrieval_benchmark.json`. Recent baseline → tuned numbers and decisions are in [docs/production_progress_report.md](docs/production_progress_report.md) (post–Phase 3 section).

## CLI utilities

```bash
python rag/indexer.py --reset
python rag/pipeline.py --status
python scripts/trace_query.py "scale deployment to 3 replicas"
python scripts/eval_retrieval.py
python scripts/run_e2e_eval.py --mode api
python scripts/smoke_auth.py
```

E2E details: [tests/e2e/README.md](tests/e2e/README.md).

## Project layout

```
ansible-iac-ai/
├── app.py                 # Flask API
├── config.py              # pydantic-settings
├── celery_app.py / tasks.py / realtime.py / storage.py / logstream.py
├── auth/                  # Login, CSRF, sessions, audit
├── agent/                 # LangGraph agent
├── frontend/              # React SPA
├── pipeline/              # KB scrape, parse, validate, ansible-lint runner
├── rag/                   # Embeddings, pgvector store, indexer, retriever
├── migrations/            # Alembic (incl. pgvector)
├── docker/                # entrypoint + ansible-collections.yml
├── deploy/observability/  # Phase 6a Prometheus / Grafana / Langfuse
├── data/parsed/           # Parsed module JSON (RAG source)
├── docs/                  # Progress report, layout, presentations
├── scripts/               # seed_admin, smoke_auth, eval runners
├── tests/
├── Dockerfile / docker-compose.yml / docker-compose.observability.yml
└── reports/               # Indexing / retrieval benchmarks (gitignored)
```

Full tree: [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md).

## Roadmap (LLMOps plan)

| Phase | Status | Summary |
|-------|--------|---------|
| 0 | Done | Auth, security, config, Alembic |
| 1 | Done | Multi-stage Dockerfile, Compose |
| 2 | Done | Celery, Redis cancel/logs, MinIO, 202 chat |
| 3 | Done | Postgres + pgvector, TEI-ready embeddings client |
| 4 | Pending | vLLM + TEI on GPU nodes (K8s) |
| 5 | Pending | Keycloak SSO |
| 6 | 6a done | Metrics + Langfuse + Grafana dashboard — [deploy/observability/README.md](deploy/observability/README.md); 6b/6c remaining |

| 7 | Pending | CI eval gate, ArgoCD |
| 8 | Pending | Hardening, Vault, DR |

### Observability (Phase 6a)

```bash
# App + Prometheus (:9090) + Grafana (:3001)
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.observability.yml up -d

# Langfuse UI (:3000) — needs Phase 6a secrets in .env.docker
docker compose --env-file .env.docker \
  -f deploy/observability/docker-compose.langfuse.yml up -d
```

`/metrics` is public on the API. Langfuse is opt-in via `LANGFUSE_*`. Grafana loads the provisioned **AnsibleAI overview** dashboard. Full runbook: [deploy/observability/README.md](deploy/observability/README.md). Progress write-up: [docs/production_progress_report.md](docs/production_progress_report.md).

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -q --ignore=tests/e2e

set E2E_RUN=1   # Windows PowerShell: $env:E2E_RUN=1
pytest tests/test_e2e_platform.py -v -s
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Blank page at `:5000` | Rebuild UI (`npm run build`) or use Compose image build |
| `DATABASE_URL` / `SECRET_KEY` errors | Copy `.env` / `.env.docker` from the `*.example` files |
| RAG status: 0 chunks | `python rag/indexer.py --reset` (or via `docker compose exec api …`) |
| `syntax-check[unknown-module]` for `amazon.aws.*` | Rebuild image so Galaxy collections are installed |
| Chat stuck / “Reconnecting…” | Ensure `CORS_ORIGINS` includes your UI origin; check `worker` logs |
| Compose fails on `MINIO_ROOT_PASSWORD` | Set it in `.env.docker` (min 8 chars) |
| Agent / Ollama timeouts | Pull models; raise `AGENT_REQUEST_TIMEOUT`; warm Ollama on worker start |
| Host eval script hits MySQL | Use Compose/`DATABASE_URL` postgres URL — Phase 3 dropped MySQL |

## License

Academic / PFE project — see repository for details.
