# AnsibleAI — Production Deployment Progress Report

> **Last updated:** Phase 5b (August 2026); remaining roadmap retargeted to
> Kubernetes **without GPU nodes** (Phase 4 = cluster hosting).
> **Next phase:** Phase 4 (Helm chart on kubeadm lab cluster, host Ollama) —
> see [Phases remaining](#phases-remaining)
>
> This report is the living record of every production-readiness phase.
> Each phase adds a section describing what changed, why it changed, and
> what the app looked like before and after. Future phases append to this
> file rather than replacing it, so the full history is always in one
> place.

---

## How the app started

AnsibleAI began as a single-file Flask prototype. Everything ran in one
process on one machine:

- **No users.** Every route was open. Anyone who could reach the port
  could read, delete, or generate in every conversation.
- **No config management.** Secrets and settings were scattered across
  `os.getenv()` calls with hardcoded fallbacks. A missing key was
  discovered at request time, not at startup.
- **No structured logging.** `print()` statements. No request IDs, no
  JSON, no way to correlate a log line to a request.
- **No health probes.** Nothing for Kubernetes (or even a load balancer)
  to ask "are you alive" or "can you serve traffic".
- **No migrations.** `db.create_all()` on every boot. Schema changes
  required dropping and recreating tables.
- **No containers.** The app ran under the Werkzeug dev server on
  `127.0.0.1:5000` with `debug=True` and `allow_unsafe_werkzeug=True`.
- **Synchronous generation.** A chat request held an HTTP connection open
  for 2–10 minutes while the LangGraph loop ran. The browser, the web
  worker, and the agent were one uninterruptible chain.
- **All state in memory.** Socket.IO sessions, cancellation flags, and
  SSE log queues were Python dicts. A second process — or even a second
  gunicorn worker — could not see any of them.
- **Playbooks on local disk.** Generated YAML was written to `output/`,
  which is ephemeral on a container and invisible to any other replica.

The frontend was a React SPA served by Vite in development and built
into `static/dist` for production. The agent used LangGraph with a
CoT-driven repair loop and ansible-lint gating — the intelligence was
already there, but everything around it was prototype-grade.

---

## Phase 0 — Hygiene, config, and user management

**Goal:** Make the app safe to expose to more than one person and
establish the foundations every later phase builds on.

Split into three sub-phases:

### 0a. Code hygiene and config

| Before | After |
|--------|-------|
| `print()` logging | `structlog` with JSON output, request IDs, log levels |
| `os.getenv()` scattered everywhere | `config.py` with pydantic-settings, fail-fast validation |
| `db.create_all()` on boot | Alembic migrations, boot refuses un-migrated schemas |
| No health endpoints | `/healthz` (liveness) and `/readyz` (DB, schema, RAG, vector store) |
| No linting or type checking | `ruff`, `mypy`, `pre-commit`, ESLint + `tsc` for the frontend |
| Debug logs committed to repo | Removed; `data/chromadb/` and `output/` added to `.gitignore` |

**Key files created:** `config.py`, `logging_setup.py`, `alembic.ini`,
`migrations/`

### 0b. User management

| Before | After |
|--------|-------|
| No concept of a user | `User` model with argon2id password hashing |
| Every conversation visible to everyone | `chat_threads.user_id` FK, per-user thread scoping |
| No authentication | Flask-Login + server-side sessions (Flask-Session) |
| No auth endpoints | `register`, `login`, `logout`, `me`, `password/change`, `csrf` |
| No frontend auth | Login/register pages, auth context, protected routes, 401 interceptor |
| Socket.IO broadcast to all | Per-user rooms, `connect` handler rejects unauthenticated sockets |

**Key files created:** `auth/` package (`__init__.py`, `routes.py`,
`security.py`, `passwords.py`, `audit.py`), `scripts/seed_admin.py`,
`scripts/smoke_auth.py`

The `User` model includes nullable `provider` and `external_id` columns,
unused now, that Phase 5 (Keycloak SSO) will use for account linking
without a new migration.

### 0c. Application security baseline

| Before | After |
|--------|-------|
| All routes open | `@login_required` default-deny on every route |
| Thread IDs enumerable | 404 (not 403) on another user's thread |
| No CSRF protection | Double-submit cookie via Flask-WTF |
| No rate limiting | Flask-Limiter (per-IP and per-account), progressive lockout |
| No security headers | Flask-Talisman: HSTS, CSP, `X-Frame-Options`, Referrer-Policy |
| No session hardening | Session rotation on login, idle + absolute timeouts |
| No audit trail | `AuditEvent` table logging login/logout/password/role/admin actions |
| Login errors leak info | Uniform "invalid email or password", dummy hash on unknown emails |

**Verification:** 114 tests passing, `ruff`/`mypy` clean,
`smoke_auth.py` passing all 24 checks against a running server. 183
existing threads and 416 messages preserved and reassigned to the seeded
admin.

---

## Phase 1 — Containerization

**Goal:** Package the app into a production-grade container image and
provide a local Docker Compose stack that mirrors production.

| Before | After |
|--------|-------|
| Werkzeug dev server, `127.0.0.1`, `debug=True` | gunicorn + gevent on `0.0.0.0:5000` |
| No Dockerfile | Multi-stage build: Node SPA build, Python venv, slim runtime |
| Root process, full capabilities | `uid 10001`, `cap_drop: ALL`, `no-new-privileges`, `read_only: true` |
| ansible-lint invoked but not in requirements | `ansible-core` + `ansible-lint` declared, `ANSIBLE_LINT_MODE=native` |
| No compose stack | MySQL 8.4, Redis 7, one-shot `migrate` service, `api` service |
| No entrypoint dispatch | `docker/entrypoint.sh` with roles: `api`, `migrate`, `smoke`, `exec` |
| Socket.IO async mode hardcoded | Configurable `SOCKETIO_ASYNC_MODE` matching gunicorn's worker class |

**Key files created:** `Dockerfile`, `docker-compose.yml`,
`docker/entrypoint.sh`, `gunicorn.conf.py`, `.dockerignore`,
`.env.docker.example`, `tests/test_container_config.py` (17 tests)

**Design decisions:**

- **One image, not two.** The plan specified separate API and worker
  Dockerfiles, but since generation still ran inline, both images would
  have been byte-identical. One image = one build, one Trivy scan, one
  SBOM.
- **`-k gevent`, not `GeventWebSocketWorker`.** The latter comes from
  `gevent-websocket` (unmaintained since 2017). Current `python-engineio`
  uses `simple-websocket` and having both installed makes the server
  close every socket after the 101 handshake. A test asserts
  `gevent-websocket` stays out of `requirements.txt`.
- **Compose mirrors what the code talks to.** MySQL (not yet Postgres),
  Redis, no MinIO or Keycloak — those services arrive with the phases
  that wire them up.

**Verification:** 161 tests passing, `ruff`/`mypy` clean. Docker not
installed on the development machine at the time, so `docker build` was
not run — the 17 container-config tests assert the invariants without a
daemon.

---

## Phase 2 — Statelessness refactor

**Goal:** Remove every piece of per-request state from the web process
so the app can run multiple replicas.

This is the single largest change. Before Phase 2, the app was
containerized but still architecturally a single-process prototype.
After Phase 2, nothing about a generation turn lives in the process that
accepted it.

### The core problem Phase 2 solved

When a user asked for a playbook, `POST /api/chat` held an HTTP
connection open for 2–10 minutes while the LangGraph agent ran. This
created four problems:

1. **A generation died if anything touched the web process.** A rolling
   deploy, pod eviction, gunicorn recycle, or proxy idle timeout killed
   the request and discarded all the tokens already spent.
2. **Only one replica was possible.** Three things lived in Python dicts:
   Socket.IO sessions, cancellation events (`threading.Event`), and SSE
   log queues. A second worker or replica couldn't see any of them.
3. **Web capacity = LLM capacity.** One request occupied one worker for
   the full generation. Serving two concurrent users required two web
   workers, even though the actual bottleneck was Ollama.
4. **Generated playbooks lived on local disk.** `output/` is ephemeral
   in Kubernetes and different on every replica.

### What changed

| Component | Before (Phase 1) | After (Phase 2) |
|-----------|-------------------|------------------|
| `POST /api/chat` | Blocks for minutes, returns the answer | Returns `202` in milliseconds |
| Where the agent runs | Inside the gunicorn worker | In a Celery worker (`tasks.py`) |
| Cancellation | `dict[int, threading.Event]` | Redis keys with TTL (`agent/cancel.py`) |
| Socket.IO emits | Direct, same-process only | Redis message queue (`realtime.py`) |
| Scrape log tailing | `queue.Queue` per session | Redis streams (`logstream.py`) |
| Playbook archive | `output/` on local disk | MinIO, date-partitioned (`storage.py`) |
| Frontend completion signal | POST response body | `generation_complete` socket event + 5s poll |
| Concurrent sends to same thread | Silent race condition | `409 already_running` |
| Safe replica count | Exactly 1 | API: limited by sticky sessions. Workers: unlimited |
| Readiness probe | DB + schema + RAG | + broker connectivity |

### New files

| File | Purpose |
|------|---------|
| `celery_app.py` | Celery configuration: early ack, no retries, JSON-only |
| `tasks.py` | `ansibleai.generation.run` — the agent turn as a background job |
| `realtime.py` | Shared Socket.IO emission for API and worker |
| `logstream.py` | Pluggable log stream backend (memory / Redis streams) |
| `storage.py` | Pluggable artifact store (local / S3-MinIO) + scratch file management |
| `tests/test_async_generation.py` | 16 tests for the 202 contract, task lifecycle, and edge cases |
| `tests/test_state_backends.py` | 15 tests for cancel, log stream, and artifact store backends |

### Modified files

| File | What changed |
|------|-------------|
| `config.py` | 15+ new settings: Celery, message queue, cancel/logstream/artifact backends, S3 credentials. Production guards refuse eager mode, memory backends, and missing message queue outside development. |
| `app.py` | Chat route returns 202 + enqueue instead of inline run. Emit helpers aliased from `realtime.py`. `_DOC_LOG_QUEUES` replaced by `logstream.py`. Broker probe in `/readyz`. |
| `agent/cancel.py` | Complete rewrite: pluggable backend protocol, `MemoryCancelBackend` and `RedisCancelBackend`, TTL-guarded keys, `is_running()` added. |
| `agent/tools.py` | `draft_playbook` uses `storage.write_working_file()` instead of `rag/generator.save_playbook()`. |
| `docker-compose.yml` | Added `minio` and `worker` services. All Phase 2 env vars wired. Worker has no `output/` mount. |
| `docker/entrypoint.sh` | `worker` role implemented: `celery -A tasks worker` with concurrency, gossip, and mingle flags. |
| `gunicorn.conf.py` | Worker-count warning updated for the new architecture. |
| `.env.docker.example` | MinIO credentials, worker concurrency, Celery tuning, artifact backend docs. |
| `.env.example` | Full Phase 2 settings documented with explanations. |
| `requirements.txt` | Added `celery==5.6.3`, `boto3==1.43.63`. |
| `frontend/src/lib/types.ts` | `ChatResponse` replaced by `ChatAcceptedResponse` (no `assistant_message`) and `ChatJobStatus`. |
| `frontend/src/lib/api.ts` | `chat.send` returns `ChatAcceptedResponse`. Added `chat.status()` polling endpoint. |
| `frontend/src/app/providers/ChatProvider.tsx` | `sendMessage` no longer awaits an answer. Watchdog poll (5s interval, 30min max) covers a dropped socket. `settleThread` centralizes cleanup on every terminal path. `abortRef` removed (nothing to abort). |
| `tests/test_container_config.py` | 10 new tests for worker service, message queue, broker separation, artifact backend, time limits. |

### Design decisions

- **`task_acks_late=False`.** The usual default (`True`) means a killed
  worker re-delivers the job. For an LLM generation that already spent
  nine minutes of GPU time, that's not recovery — it's paying twice. The
  user retries by sending a new message, which is explicit and visible.
- **`max_retries=0`.** Same reasoning. A failed generation has already
  spent its tokens; replaying it would spend them again for the same
  likely failure.
- **`CELERY_TASK_ALWAYS_EAGER=true` in development.** Runs the task
  inline so `python app.py` needs no broker and no worker. `config.py`
  refuses it outside development.
- **MinIO is the archive, not the read path.** The YAML is stored on the
  chat message and rendered from the database. A storage outage costs the
  archive copy, not the answer the user waited for.
- **409 for concurrent sends.** Making the endpoint non-blocking made it
  trivial to fire two turns into one thread whose histories immediately
  diverge. The guard prevents that.
- **Broker probe in `/readyz`.** Chat is the application. A pod that
  cannot enqueue shouldn't be in the Service endpoints.

### Verification

- **197 tests passing** (36 new), 1 skipped
- `ruff` and `mypy` clean on all Phase 2 modules
- Frontend: `tsc --noEmit` clean, `vite build` succeeds
- Celery app loads and registers `ansibleai.generation.run`
- Eager mode confirmed: task runs inline with no broker

---

## Cumulative test count

| Phase | Tests added | Running total |
|-------|-------------|---------------|
| Pre-Phase 0 | ~45 | ~45 |
| Phase 0 | ~69 | ~114 |
| Phase 1 | 17 | ~131 (161 at Phase 1 end) |
| Phase 2 | 36 | 197 |

---

## Current architecture

```
Browser
  │
  ├─ HTTP ──► Flask API (gunicorn/gevent)
  │              ├─ POST /api/chat → 202 + enqueue
  │              ├─ GET /api/chat/status/:id → poll
  │              ├─ POST /api/chat/cancel → Redis flag
  │              └─ Socket.IO (Redis message queue)
  │                     ▲
  │                     │ emits
  │                     │
  ├─ WebSocket ◄────────┘
  │
  └─ SSE ◄──── /docs/stream/:id (Redis streams)

Celery worker (same image, "worker" role)
  ├─ ansibleai.generation.run
  │     ├─ LangGraph agent loop
  │     ├─ ansible-lint validation
  │     ├─ emits progress via realtime.py → Redis → API → browser
  │     ├─ persists answer to MySQL
  │     └─ archives playbook to MinIO
  ├─ cancel check: Redis keys with TTL
  └─ soft/hard time limits

Infrastructure (docker-compose)
  ├─ MySQL 8.4
  ├─ Redis 7 (sessions db/0, socketio db/1, broker db/2)
  └─ MinIO (playbook artifacts)
```

---

## What's next: Phase 3

Migrate ChromaDB to Postgres+pgvector and Ollama embeddings to TEI. The
current vector index is 8,065 chunks in a SQLite-backed ChromaDB file
that is bind-mounted into both the API and the worker — a shared file
that SQLite was not designed for. pgvector moves the index into a proper
database, and TEI replaces the Ollama embedding endpoint that is
currently coupled to the inference server.

---

## Phase 3 — Vector store and embeddings migration

### Goal

Replace ChromaDB (SQLite-backed on-disk file shared between containers) with
Postgres 16 + pgvector, and replace the tightly-coupled `OllamaEmbeddings`
with an OpenAI-compatible `/v1/embeddings` client that can target TEI, Ollama,
or any compatible server.

### Before

- Vectors stored in `data/chromadb/` (SQLite file), bind-mounted RW into both
  API and worker containers — a known concurrency issue.
- Embeddings generated by `langchain_ollama.OllamaEmbeddings` — coupled to Ollama.
- MySQL 8.4 for application tables.
- No cache invalidation after reindex.
- `invalidate_caches()` existed but was never called.

### After

- Vectors stored in Postgres `document_chunks` table with HNSW index
  (`vector_cosine_ops`, m=16, ef_construction=128) and GIN on JSONB metadata.
- Embeddings via `rag/embeddings.py` — a generic OpenAI-compatible client
  configurable to TEI or Ollama `/v1/embeddings`.
- PostgreSQL 16 + pgvector for both application tables and vectors.
- Redis pub/sub invalidation after every reindex, clearing BM25, vectorstore
  proxy, and collection allow-list caches across all pods.

### New files

| File | Purpose |
|------|---------|
| `rag/embeddings.py` | OpenAI-compatible embedding client (batched, configurable) |
| `rag/vectorstore.py` | pgvector-backed store with filter translation |
| `rag/invalidation.py` | Redis pub/sub cache invalidation |
| `migrations/versions/0003_pgvector_document_chunks.py` | pgvector extension + tables |
| `tests/test_phase3_pgvector.py` | 28 tests for the new layer |

### Modified files

| File | Change |
|------|--------|
| `rag/indexer.py` | Complete rewrite: embeds via `rag/embeddings` and upserts into pgvector |
| `rag/retriever.py` | Removed `Chroma` import; type annotations changed to `Any` |
| `rag/sparse_index.py` | Comment update; interface unchanged (proxy provides `.get()`) |
| `rag/pipeline.py` | Status command uses pgvector `count()` instead of ChromaDB |
| `agent/tools.py` | Unchanged — proxy preserves the lazy-singleton interface |
| `app.py` | `/readyz` and `/rag/status` use `rag.vectorstore.count()`; invalidation listener started |
| `config.py` | Added embedding + vector settings; DATABASE_URL example updated |
| `docker-compose.yml` | `pgvector/pgvector:pg16` replaces `mysql:8.4`; no chromadb mounts |
| `requirements.txt` | Removed chromadb/langchain-chroma/langchain-ollama/pymysql; added pgvector/psycopg2-binary/httpx/numpy |
| `gunicorn.conf.py` | Comment update (pymysql → psycopg2) |
| `models.py` | Comment update (MySQL → PostgreSQL) |
| `logging_setup.py` | chromadb → pgvector log suppression |
| `.env.example` | Postgres URL + embedding settings |
| `.env.docker.example` | Postgres credentials (replaces MySQL) |

### Key design decisions

1. **HNSW over IVFFlat** — at 8k chunks, IVFFlat's `nlist` tuning is pointless;
   HNSW gives sub-ms recall with no parameter search.
2. **JSONB metadata with GIN index** — Chroma-style filters (`$eq`, `$in`,
   `$contains`, `$and`) translated to SQLAlchemy JSONB operators; GIN makes
   containment queries use the index.
3. **Proxy shim in indexer** — `load_vectorstore()` returns a `_PgVectorProxy`
   that provides the same `.similarity_search_with_relevance_scores()` and
   `.get()` methods the retriever expects, keeping the 6-stage pipeline
   unchanged.
4. **Embeddings decoupled from Ollama** — the client targets any OpenAI-compatible
   `/v1/embeddings` endpoint, making the switch to TEI in Phase 4 a config change.
5. **Schema version gate** — `check_schema_compatibility()` compares stored
   `embed_model`, `embedding_dimensions`, `index_schema_version`, and
   `chunk_schema_version` against running config; mismatch blocks indexing
   without `--reset`.

### Verification

```bash
docker compose --env-file .env.docker up --build -d
docker compose --env-file .env.docker exec api python rag/indexer.py --reset
# Watch: embeddings via Ollama /v1/embeddings, chunks stored in pgvector
```

---

## Retrieval quality tuning (post-Phase 3)

A retrieval-only benchmark (`scripts/eval_retrieval.py` on
`rag/retrieval_benchmark.json`, 55 task-phrased queries, no LLM involved) was
established first; every subsequent change was measured against it, and one
experiment was rejected on evidence.

| Run | top1 | hit@8 | MRR | Artifact |
|-----|------|-------|-----|----------|
| Baseline (Phase 3 stack, untouched pipeline) | 34.5% | 60.0% | 0.448 | `reports/retrieval_baseline.json` |
| + module score aggregation + read-only demotion | 40.0% | 63.6% | 0.495 | `reports/retrieval_after_rank_fix.json` |
| + separate "purpose" chunk (rejected) | 34.5% | 63.6% | 0.467 | `reports/retrieval_after_purpose_chunk.json` |
| + task names folded into overview (kept) | 36.4% | 65.5% | 0.475 | `reports/retrieval_final.json` |

What landed:

1. **Module-level score aggregation** (`rag/retrieval_utils.py`) — primary
   module chosen by a decayed sum of its best chunks (1.0/0.30/0.10) instead of
   the single best chunk. A module supported by three matching chunks now beats
   a sibling with one lucky chunk.
2. **Read-only module demotion** (`rag/retriever.py`) — token-matched action
   verbs ("store", "attach", "schedule", …) extend write-intent detection, and
   `*_info` / `*_facts` modules are demoted on any non-read query.
3. **Task vocabulary in overview chunks** (`rag/ingestion.py`, chunk schema
   `v5_overview_tasks`) — example task names ("Install a list of packages")
   folded into each module's overview chunk. This is the only natural-language
   task phrasing in the docs and it is what recall was missing. A separate
   embedded "purpose" chunk was tried first and rejected: 1,222 extra
   generic-verb chunks polluted ranking (−5.5 top1).
4. **Routing keywords** — "object storage" → amazon.aws, "networkmanager" →
   community.general (guard test updated).

The chosen end state trades 2 top1 queries against the rank-fix-only state for
one query rescued from complete pack absence — the agent can recover a rank-2
module from the ranked list it receives, but a module missing from the pack is
unrecoverable. Remaining misses are dominated by semantic vocabulary gaps that
need a stronger embedding model (Phase 4's TEI deployment is the natural place
to try `bge-m3` or similar). `kubernetes.core.k8s_cp` is missing from the
parsed corpus entirely (scrape gap).

Comparison tooling: `scripts/compare_eval_runs.py` diffs two benchmark runs
query-by-query. New unit coverage: `tests/test_retrieval_ranking.py` (8 tests).
Reindex required after the chunk schema change:
`docker compose --env-file .env.docker exec api python rag/indexer.py --reset`.

---

## Phase 6a — LLMOps observability (Compose)

**Goal:** Local parity for tracing and metrics **without Kubernetes**, so
every chat turn is visible in Langfuse and operational signals land in
Prometheus/Grafana.

**Status: complete.** Langfuse UI and Grafana dashboard verified on the
developer Compose stack.

### Before

- No distributed traces of LangGraph / LLM / gate steps
- No Prometheus exposition; Grafana had a datasource only (empty UI)
- Token usage and generation latency lived only in ad-hoc logs

### After

| Area | Delivered |
|------|-----------|
| Prometheus | `GET /metrics` on the API; scrape job `ansibleai-api` |
| Domain metrics | HTTP RED, generation start/complete/duration, gate results, repair iterations, LLM calls/tokens |
| Langfuse | Self-hosted v3 stack; Python SDK 3.15; opt-in via `LANGFUSE_*` |
| Trace shape | Root `generate-playbook` (`agent`); nested retriever / generation / evaluator; LangGraph `CallbackHandler`; `user_id` + `session_id` |
| Grafana | Provisioned folder **AnsibleAI** → **AnsibleAI overview** |
| Skill | Official Langfuse agent skill at `.agents/skills/langfuse` |

### New / key files

| Path | Purpose |
|------|---------|
| `observability/metrics.py` | Prometheus counters/histograms + `/metrics` body |
| `observability/tracing.py` | Langfuse client, `generation_trace`, typed `observe()` |
| `deploy/observability/docker-compose.langfuse.yml` | Langfuse + Postgres/Redis/ClickHouse/MinIO |
| `docker-compose.observability.yml` | Prometheus + Grafana |
| `deploy/observability/grafana/provisioning/...` | Datasource + overview dashboard JSON |
| `deploy/observability/README.md` | Operator runbook |
| `tests/test_observability.py` | Metrics public + Langfuse no-op / wiring tests |

### Wiring

- `tasks.py` — wraps each Celery generation in `generation_trace`
- `agent/llm.py` — records generations + token usage when the provider reports it
- `agent/graph.py` — `retrieve-docs` / `run-production-gate` observations
- `agent/orchestrator.py` — LangGraph callbacks under the active trace
- `app.py` — HTTP timing middleware; public `/metrics`
- `docker-compose.yml` `x-app-env` — `LANGFUSE_*` + `LANGFUSE_TRACING_ENVIRONMENT`

### Design decisions

1. **SDK 3.15, not 4.x** — self-hosted compose image is Langfuse **v3**; SDK 4’s default Public API expects server v4.
2. **Separate Langfuse Compose project** — avoids clashing with app Postgres/Redis/MinIO; containers reach the UI via `host.docker.internal:3000`.
3. **No full playbook YAML in traces** — truncated prompts/outputs only.
4. **Grafana provisioned as code** — dashboards live under `deploy/observability/grafana/` and load on Grafana recreate.
5. **GPU / Loki / Tempo deferred** — need a real cluster (and GPU nodes for vLLM/DCGM panels). Prompt management and alert rules are Phase **6b**.
6. **Langfuse is operator-only** (locked in Phase 5b). Members see today’s token spend in Account. Traces stay in Langfuse (`:3000`); the SPA never links there or copies a user id.

### Verify

```bash
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.observability.yml up -d
docker compose --env-file .env.docker \
  -f deploy/observability/docker-compose.langfuse.yml up -d
# After LANGFUSE_ENABLED=true + keys: recreate api worker, send a chat
# Langfuse → Traces; Grafana → AnsibleAI overview; Prometheus → targets UP
```

Details: [deploy/observability/README.md](../deploy/observability/README.md).

---

## Cumulative test count

| Phase | Tests added | Running total |
|-------|-------------|---------------|
| Pre-Phase 0 | ~45 | ~45 |
| Phase 0 | ~69 | ~114 |
| Phase 1 | 17 | ~131 (161 at Phase 1 end) |
| Phase 2 | 36 | 197 |
| Phase 3 | 28 | 232 |
| RAG tuning | 8 | 240 |
| Phase 6a | ~7 | ~247 |
| Phase 5 / 5b | ~40 | **287** |

---

## Current architecture (post–Phase 5b)

```
Browser (members stay on AnsibleAI)
  │
  ├─ HTTP ──► Flask API (gunicorn/gevent)
  │              ├─ POST /api/auth/login → Keycloak ROPC (hybrid/oidc)
  │              ├─ GET /api/auth/profile → tokens spent + activity
  │              ├─ POST /api/chat → 202 + enqueue
  │              ├─ GET /metrics → Prometheus scrape
  │              └─ Socket.IO (Redis message queue)
  │
Admin browser ──► Keycloak (:8080)  — users, temp passwords, SMTP
Ops browser   ──► Langfuse (:3000)  — traces (not linked from the SPA)

Celery worker
  ├─ LangGraph + LLM + gate
  ├─ Redis daily token counter (shown in Account)
  ├─ Langfuse traces (generate-playbook tree, operator-only UI)
  └─ Prometheus domain metrics (generation / LLM / gate)

Observability (Compose)
  ├─ Prometheus (:9090) ← scrapes api:5000/metrics
  ├─ Grafana (:3001) ← AnsibleAI overview dashboard
  └─ Langfuse (:3000) ← worker SDK (host.docker.internal)

Identity (Compose --profile sso)
  └─ Keycloak 26 (:8080) realm ansibleai

Data plane
  ├─ Postgres 16 + pgvector
  ├─ Redis 7
  └─ MinIO (playbook archive)
```

---

## Phase 5 — Keycloak identity

**Status: complete** (August 2026), in two slices. Identity still uses
the Phase 0 `User` row, Flask-Login session, CSRF, and default-deny
hook. Credentials can come from Keycloak or a local argon2id password.

### 5a — OIDC BFF (authorization code)

The first slice wired Keycloak as an IdP without changing the login
page’s product shape yet.

| Area | Delivered | Notes |
| --- | --- | --- |
| Modes | `AUTH_MODE=local` (default) / `hybrid` / `oidc` | Tests and `python app.py` stay password-only (`AUTH_MODE=local` in pytest) |
| BFF OIDC | `GET /api/auth/oidc/login` + `/callback` | Authorization code + PKCE; confidential client; SPA never sees the secret |
| Account link | verified email → existing `users.email`; `provider=keycloak`, `external_id=sub` | Local hash retired except `AUTH_BREAK_GLASS_EMAILS` |
| JWT | `Authorization: Bearer` + Socket.IO `auth.token` | JWKS from `OIDC_INTERNAL_BASE_URL`; `iss` is the public issuer |
| Roles | Keycloak group `ansibleai-admins` / realm role `ansibleai-admin` | Mapping **off** by default after 5b (`OIDC_MAP_APP_ADMIN=false`) |
| Token budgets | `USER_DAILY_TOKEN_BUDGET` in the worker | Redis counter; Langfuse metadata `token_budget_*` for operators |
| Compose | `--profile sso` Keycloak 26 + realm import | Default stack does not start Keycloak |
| K8s | `deploy/keycloak/k8s/oauth2-proxy.yaml` | Placeholder for ingress; Compose never puts oauth2-proxy in front of gunicorn |

Design (5a): [specs/phase5_keycloak_sso_design.md](../specs/phase5_keycloak_sso_design.md).

Deviations from the original Phase 5 bullets:

- **oauth2-proxy is not in Compose.** It would fight Flask-Login cookies,
  CSRF, and Socket.IO. The cluster ingress is the right place.
- **Local passwords are not deleted globally.** They are retired per
  account on first successful IdP link, with break-glass addresses kept.

### 5b — In-app login, Keycloak-only admins, member account

**Locked product:** members never leave AnsibleAI. Keycloak is the
identity store and the **only** admin console (create users, temporary
passwords, SMTP). AnsibleAI has no invite screen and no operator chrome
when `AUTH_MODE` is `hybrid` or `oidc`.

| Area | Delivered | Notes |
| --- | --- | --- |
| Login | Single email + password card | SPA posts `POST /api/auth/login`; no “Sign in with SSO” |
| IdP grant | Keycloak resource-owner password credentials | BFF holds the confidential client; `OIDC_BROWSER_REDIRECT` defaults false |
| Invites | Keycloak console only | No `POST /api/admin/users`, no Team UI |
| First password | Temporary credential in Keycloak, change **in AnsibleAI** | Admin API briefly clears `UPDATE_PASSWORD`, retries the grant, restores until in-app change |
| App admins | Hidden when Keycloak is the IdP | `app_admin_ui` true only in `AUTH_MODE=local`; KB scrape/restore is an ops procedure |
| Account | Identity, tokens spent today, threads, password | No Langfuse URL, no user-id copy |
| Token spend | Always counted in Redis | Cap still optional (`USER_DAILY_TOKEN_BUDGET=0` = unlimited, used still increments) |
| SMTP | Keycloak realm Email | Test connection mails the logged-in Keycloak admin; Credential Reset is the member path |

Design: [specs/phase5b_embedded_login_design.md](../specs/phase5b_embedded_login_design.md).
Runbook: [deploy/keycloak/README.md](../deploy/keycloak/README.md).

ADRs:

1. **Password grant (ROPC)** instead of browser redirect — one AnsibleAI
   login page. Authorization-code + PKCE remains in the API as an escape
   hatch (`OIDC_BROWSER_REDIRECT=true`).
2. **Admins stay in Keycloak** — do not auto-promote Keycloak groups to
   `users.role=admin`.
3. **Temporary password, then in-app change** — members never open the
   Keycloak account console for first login.

Operational notes from the live Compose stack:

- Keycloak console `admin` ≠ `sso-admin@ansibleai.local` ≠ break-glass
  `admin@ansibleai.local`.
- Gmail SMTP works once From is saved on realm **ansibleai** (StartTLS
  587). School inboxes (e.g. Esprit Outlook/M365) may quarantine Gmail
  senders even when Keycloak reports “Email sent”.
- Execute-actions email links use `http://localhost:8080` and only work
  on this PC; the intended member path is temporary password + AnsibleAI
  change, not that link.

```
Member browser ──► AnsibleAI login (email + password)
                     POST /api/auth/login
                       ├─ break-glass → local argon2id
                       └─ hybrid/oidc → Keycloak password grant
                            └─ temp password → Admin API + must_change_password

Admin browser  ──► Keycloak :8080 (users, SMTP, disable)

Celery worker
  └─ daily Redis token counter (always) + Langfuse metadata (operators)
```

**Verification:** 287 tests collected (auth, OIDC/ROPC, budgets, authz).
Pytest forces `AUTH_MODE=local` so a developer hybrid `.env` cannot send
the suite to a live Keycloak.

---

## Phases remaining

| Phase | Summary | Status |
|-------|---------|--------|
| 4 | Kubernetes hosting **without GPU nodes** — Ansible kubeadm on **192.168.1.11** (master) + **192.168.1.12** (worker) from control **192.168.1.19**; then Helm | Bootstrap project ready; Helm app still pending |
| 4-gpu | Optional: vLLM + TEI + NVIDIA GPU Operator + DCGM — only if NVIDIA GPU nodes appear | Deferred |
| 5 / 5b | Keycloak identity — in-app login, Keycloak-only admins, tokens spent in Account | **Complete** (cluster Keycloak install is Phase 4; no oauth2-proxy on members) |
| 6a | Prometheus + Grafana + Langfuse (operator UI) on Compose | **Complete** |
| 6b | Langfuse prompt management, Celery exporter, CPU-cluster alert rules | Pending (after 4) |
| 6c | Loki/Tempo on the cluster (GPU panels only with 4-gpu) | Pending (after 7) |
| 7 | GitHub Actions, SHA tags, Trivy, ArgoCD GitOps, eval gate, rolling + documented rollback | Pending |
| 8 | Default-deny NetworkPolicies, restricted PSS, Kyverno, ESO/Sealed Secrets, CNPG PITR, Velero, k6 | Pending |

**Recommended next:** Phase **4** — put the running Compose app on the lab Kubernetes cluster. Inference stays on host Ollama.  
**Do not wait for GPUs.** vLLM/TEI is an optional add-on.  
**Then:** 6b (alerts) → 7 (GitOps + eval gate) → 6c (Loki/Tempo) → 8 (hardening).
