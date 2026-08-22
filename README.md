# AnsibleAI

AI-powered Infrastructure-as-Code assistant that generates Ansible playbooks grounded on indexed official module documentation.

The stack combines a **React 19** web UI, a **Flask + PostgreSQL** API, a **Celery worker** running a **LangGraph agent** (reason → tools → draft → production gate → repair loop), and a **hybrid RAG pipeline** (pgvector dense search + BM25, OpenAI-compatible embeddings).

This is an academic / PFE (end-of-studies) project in DevOps, IaC, RAG, and LLM agents. Phases **0–3**, **5/5b**, and **6a** of the production LLMOps plan are implemented (auth, containers, async workers, Postgres+pgvector, Keycloak in-app login, Prometheus/Grafana/Langfuse). Phase **4a** (kubeadm lab) is done; the Phase **4b** Helm chart is in the repository. Phases **6b**, **7**, and **8** are not implemented. See [docs/production_progress_report.md](docs/production_progress_report.md) and [docs/general_introduction.md](docs/general_introduction.md).

---

## Table of contents

1. [Overview](#overview)
2. [Features](#features)
3. [Technology stack](#technology-stack)
4. [Architecture](#architecture)
5. [Project structure](#project-structure)
6. [Prerequisites](#prerequisites)
7. [Installation](#installation)
8. [Environment files](#environment-files)
9. [Environment variables](#environment-variables)
10. [Database](#database)
11. [Authentication and authorization](#authentication-and-authorization)
12. [API documentation](#api-documentation)
13. [WebSocket events](#websocket-events)
14. [The LangGraph agent](#the-langgraph-agent)
15. [Knowledge base and RAG](#knowledge-base-and-rag)
16. [External services](#external-services)
17. [Frontend](#frontend)
18. [Available commands](#available-commands)
19. [Testing](#testing)
20. [Docker](#docker)
21. [Kubernetes / Helm](#kubernetes--helm)
22. [Cluster bootstrap (Ansible)](#cluster-bootstrap-ansible)
23. [Observability](#observability)
24. [CI/CD](#cicd)
25. [Main workflows](#main-workflows)
26. [Security](#security)
27. [Troubleshooting](#troubleshooting)
28. [Known limitations](#known-limitations)
29. [Development guidelines](#development-guidelines)
30. [Further documentation](#further-documentation)
31. [License](#license)

---

## Overview

**Problem.** Writing Ansible playbooks requires picking the right module among hundreds of similarly named candidates, supplying exact parameter names and FQCNs, and keeping up with collection documentation. Generic LLMs hallucinate modules, go stale against live APIs, and do not run ansible-lint.

**Purpose.** AnsibleAI takes a natural-language task, retrieves official module documentation from a local knowledge base, drafts YAML, and loops through a production gate (validator + ansible-lint + placeholder checks) until the playbook is acceptable or the iteration budget is exhausted.

**Users / roles.**

| Role | Who | What they can do |
|------|-----|------------------|
| Member (`users.role=user`) | Authenticated operators | Chat, threads, stats, docs read-only, account / password |
| Administrator (`users.role=admin`) | First local account, bootstrap admin, or Keycloak-mapped admin | Everything a member can, plus KB check-updates, re-scrape, and rollback. App admin chrome is shown only when `AUTH_MODE=local`. |
| Identity admin | Keycloak console (hybrid/oidc) | Create users, temporary passwords, SMTP. Not an AnsibleAI UI. |

**Default local identity** is email + argon2id password. Optional **hybrid / oidc** mode authenticates members against Keycloak from the AnsibleAI login page (resource-owner password grant). There is no payment, email-sending, or file-upload product surface.

---

## Features

### Authentication

- Self-serve registration (policy-gated: `closed` / `domain` / `open`)
- Email + password login (local hash, or Keycloak ROPC when OIDC is configured)
- Logout with server-side session clear
- Password change (local argon2id, or Keycloak Admin API for SSO users)
- Forced password change after a Keycloak temporary password
- CSRF token bootstrap (`GET /api/auth/csrf` + readable `csrf_token` cookie)
- Public auth capability probe for the login screen (`GET /api/auth/config`)
- Session probe (`GET /api/auth/me`) that returns 200 with `authenticated: false` when anonymous
- Account profile: identity, daily token budget, thread activity
- Optional Keycloak hosted-UI login (`OIDC_BROWSER_REDIRECT=true`)
- Break-glass local passwords (`AUTH_BREAK_GLASS_EMAILS`)
- Account lockout after consecutive failed logins
- First registered user becomes administrator
- Optional admin approval for later self-registrations

### User features

- Multi-turn chat threads (create, list, open, rename, delete, clear own threads)
- Natural-language playbook generation with live agent progress
- Stop an in-flight generation
- Playbook display with validation results and RAG source chips
- Agent thinking / tool-trace UI
- First-run onboarding (welcome, agent loop, workspace tour, prompt craft)
- Account panel (profile, token spend, password)
- Analytics side panel (generation counts by module)
- Docs side panel (KB health; mutations for admins only)
- Session-expiry handling that returns the SPA to the login screen

### Admin features

- Compare local scraped HTML hashes against upstream docs.ansible.com
- Re-scrape selected modules into `data/parsed`
- Rollback the knowledge base from `data/kb_versions` snapshots
- Live SSE log tail of scrape sessions
- Bootstrap admin seed (`scripts/seed_admin.py` / Compose `migrate` role)

There is **no** in-app user-management API (activate / deactivate / change role). Audit event names for those actions exist in `backend/auth/audit.py` but are not wired to HTTP routes. In hybrid/oidc, identity administration is Keycloak.

### Business / generation features

- LangGraph loop: reason → tools → draft → gate → repair → respond (or ask_user)
- Hybrid retrieval over Ansible collections: `ansible.builtin`, `amazon.aws`, `azure.azcollection`, `kubernetes.core`, `community.general`
- KB-aware YAML validator plus ansible-lint production gate
- Cooperative cancel via memory or Redis
- Daily per-user LLM token budget (optional; 0 = unlimited)
- Durable playbook archive to local disk or S3/MinIO (YAML is also stored on the chat message)

### Platform features

- Liveness (`/healthz`) and readiness (`/readyz`) probes
- Prometheus metrics (`/metrics`)
- Optional Langfuse traces
- Alembic migrations (Postgres + pgvector)
- Multi-role Docker image (`api`, `worker`, `migrate`, `smoke`, `exec`)
- Helm chart for a kubeadm lab
- Ansible playbooks to bootstrap that lab cluster

---

## Technology stack

| Category | Technology | How it is used |
|----------|------------|----------------|
| Frontend | React 19, TypeScript 5.9, Vite 7 | SPA in `frontend/`; production build written to `static/dist/` |
| UI | Tailwind CSS v4, Radix UI (collapsible, dialog, tabs), clsx, tailwind-merge | Chat shell, dialogs, tabs |
| Realtime client | socket.io-client 4 | Generation progress and thread events |
| Backend | Flask 3.0, Flask-SQLAlchemy, Flask-SocketIO, gunicorn, gevent | HTTP API, SPA hosting, WebSocket |
| Config / logging | pydantic-settings, python-dotenv, structlog | Fail-fast settings; JSON or console logs |
| Auth | Flask-Login, Flask-Session, Flask-WTF CSRF, Flask-Limiter, Flask-Talisman, argon2-cffi, email-validator, PyJWT, cryptography | Sessions, CSRF, rate limits, headers, OIDC |
| Worker | Celery 5.6, Redis 5 | Async LangGraph runs |
| Database | PostgreSQL 16, pgvector, psycopg2, SQLAlchemy 2, Alembic | Users, chat, audit, scrape sessions, vectors |
| Agent | LangGraph 1.1, ollama Python SDK | Reason / draft / gate loop |
| RAG | LangChain core/community, httpx, numpy | Embeddings client, hybrid retriever, BM25 |
| Lint gate | ansible-core 2.18, ansible-lint 24.12 (non-Windows) | Production gate; Galaxy collections in the image |
| Artifacts | boto3 | MinIO / S3 playbook archive |
| HTML scrape | requests, BeautifulSoup4, PyYAML | KB pipeline |
| Observability | prometheus-client, langfuse 3.15 | `/metrics` and optional traces |
| Container | Docker, Docker Compose | Local parity stack |
| Orchestration | Helm 3 chart, kubeadm via Ansible | Lab Kubernetes |
| Identity (optional) | Keycloak 26.2 | Member login via ROPC |
| Inference | Ollama (host process, not a Compose service) | Planner, codegen, embeddings |
| Testing | pytest, ruff, mypy, ESLint, pre-commit, gitleaks | Unit/integration, lint, secret scan |
| Eval extras (dev) | ragas, datasets | Optional RAG evaluation CLI |

Installed but **not** used as a product feature: there is no Stripe/PayPal, no transactional email from AnsibleAI, and no frontend `import.meta.env` variables. `RATE_LIMIT_CHAT` is defined in settings but is not attached to `/api/chat`.

---

## Architecture

```mermaid
flowchart TB
  subgraph browser [Browser]
    SPA[React SPA]
  end
  subgraph apiProc [Flask API]
    Auth[Auth / CSRF / sessions]
    REST[REST + SSE]
    SIO[Socket.IO]
  end
  subgraph workerProc [Celery worker]
    LG[LangGraph agent]
    Lint[ansible-lint gate]
  end
  subgraph dataPlane [Data plane]
    PG[(PostgreSQL + pgvector)]
    Redis[(Redis)]
    MinIO[(MinIO)]
    KB[Parsed KB on disk]
  end
  subgraph infer [Host inference]
    Ollama[Ollama LLMs + embeddings]
  end
  subgraph optional [Optional]
    KC[Keycloak]
    Prom[Prometheus / Grafana]
    LF[Langfuse]
  end
  SPA -->|REST + cookies| REST
  SPA -->|WebSocket| SIO
  REST --> Auth
  Auth --> PG
  Auth --> Redis
  REST -->|enqueue| Redis
  SIO --> Redis
  workerProc --> Redis
  LG --> PG
  LG --> Ollama
  LG --> KB
  Lint --> MinIO
  REST --> KB
  REST -.->|hybrid/oidc| KC
  REST --> Prom
  LG -.-> LF
```

| Layer | Responsibility |
|-------|----------------|
| Browser | Login, chat, threads, stats, docs panel. Talks same-origin (or Vite proxy) with cookies. |
| Flask API | Auth, thread CRUD, enqueue generation, probes, metrics, KB admin, serve SPA. |
| Celery worker | Runs the agent; emits progress over Redis → Socket.IO. |
| PostgreSQL | Relational data + `document_chunks` vectors. |
| Redis | Sessions (Compose/k8s), rate limits, Celery broker, Socket.IO queue, cancel flags, SSE log streams, token budgets. |
| MinIO | Durable playbook archive when `ARTIFACT_BACKEND=s3`. |
| Ollama | Host GPU inference; Compose reaches it via `host.docker.internal`. |

---

## Project structure

```text
ansible-iac-ai/
├── README.md
├── pyproject.toml                 # ruff / mypy / pytest (pythonpath = backend)
├── requirements.txt               # runtime Python pins
├── requirements-dev.txt           # pytest, ruff, mypy, ragas, …
├── alembic.ini                    # script_location = backend/migrations
├── package.json                   # thin wrapper → frontend npm scripts
├── Dockerfile                     # multi-stage: SPA + Python 3.12 runtime
├── docker-compose.yml             # api, worker, db, redis, minio, migrate, optional keycloak
├── docker-compose.observability.yml
├── .env.example                   # host-process template
├── .env.docker.example            # Compose interpolation + container env
├── .pre-commit-config.yaml
│
├── backend/                       # all Python application code
│   ├── app.py                     # Flask routes, Socket.IO, KB admin
│   ├── config.py                  # pydantic-settings (import-time validation)
│   ├── models.py                  # SQLAlchemy models
│   ├── celery_app.py / tasks.py   # Celery + generation task
│   ├── realtime.py                # Socket.IO emits (API + worker)
│   ├── logstream.py               # SSE scrape logs
│   ├── storage.py                 # playbook artifacts (local / S3)
│   ├── logging_setup.py
│   ├── gunicorn.conf.py
│   ├── agent/                     # LangGraph: graph, tools, LLM, prompts, cancel
│   ├── auth/                      # login, CSRF, OIDC, passwords, budgets, audit
│   ├── rag/                       # embeddings, indexer, hybrid retriever, evaluator
│   ├── pipeline/                  # scrape, parse, structure, validator, ansible-lint
│   ├── observability/             # Prometheus metrics + Langfuse tracing
│   └── migrations/                # Alembic (0001 baseline, 0002 users, 0003 pgvector)
│
├── frontend/                      # React 19 + Vite + TypeScript SPA
│   └── src/
│       ├── app/                   # App shell + providers (auth, chat, socket, …)
│       ├── components/            # auth, chat, threads, panel, onboarding, ui
│       ├── lib/                   # api.ts, socket.ts, types
│       └── styles/
│
├── static/dist/                   # Vite production output (served by Flask)
├── scripts/                       # seed_admin, smoke_auth, retrieval eval, e2e runner
├── tests/                         # pytest (+ tests/e2e golden dataset)
├── docker/                        # entrypoint.sh, ansible-collections.yml
├── deploy/
│   ├── ansible/                   # kubeadm lab bootstrap (Phase 4a)
│   ├── helm/ansibleai/            # application chart (Phase 4b)
│   ├── keycloak/                  # realm JSON + oauth2-proxy notes
│   └── observability/             # Prometheus, Grafana, Langfuse compose
├── docs/                          # reports, presentations, internship materials
├── specs/                         # Phase 5 / 5b design notes
├── data/                          # local KB / scrape artifacts (gitignored)
├── output/                        # local playbook scratch (gitignored)
└── reports/                       # eval / scrape reports (gitignored)
```

Runtime paths (`data/`, `output/`, `.env`) are relative to the **repository root**. Application imports (`config`, `agent`, `rag`, …) resolve when `backend/` is on `PYTHONPATH`. Full tree notes: [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md).

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Docker Desktop (or Engine + Compose) | recent | Recommended full stack |
| Ollama | ≥ 0.1.26 | Embeddings (`/v1/embeddings`) and planner / codegen LLMs |
| Python | 3.11+ on the host; **3.12** in the image | Host-only / tests |
| Node.js | ≥ 20 | Frontend when developing outside Docker |
| npm | comes with Node 20 | Frontend install / Vite |
| PostgreSQL 16 + pgvector | Compose image `pgvector/pgvector:pg16` | Required for a real run (tests may use SQLite) |
| Helm | 3.14+ | Optional Kubernetes install |
| kubectl / Ansible | as documented under `deploy/ansible` | Optional lab cluster |

Ollama models typically pulled:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:7b      # common laptop planner/codegen
# Compose example also references:
#   gemma3:12b
#   qwen2.5-coder:14b
```

Size models to VRAM. A separate `PLAYBOOK_MODEL` only helps if both planner and drafter stay loaded; otherwise Ollama evicts and reloads on every hop.

---

## Installation

### Recommended: Docker Compose

```bash
cp .env.docker.example .env.docker
# Edit SECRET_KEY, POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, BOOTSTRAP_ADMIN_*

ollama pull nomic-embed-text
ollama pull gemma3:12b            # matches .env.docker.example AGENT_MODEL
ollama pull qwen2.5-coder:14b     # matches PLAYBOOK_MODEL

docker compose --env-file .env.docker up --build -d

# First-time vector index (into Postgres via pgvector)
docker compose --env-file .env.docker exec api python backend/rag/indexer.py --reset
```

Open **http://localhost:5000** and sign in with `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` from `.env.docker`.

The **`--env-file .env.docker` flag is required**. Compose interpolates `${…}` from the shell and from a default `.env` file, not from a service `env_file:`. Using host `.env` would point `DATABASE_URL` at localhost inside containers.

The image installs Galaxy collections (`amazon.aws`, `azure.azcollection`, `community.general`, `kubernetes.core`) so ansible-lint can resolve generated modules. If you see `syntax-check[unknown-module]` for those collections, rebuild:

```bash
docker compose --env-file .env.docker build --no-cache api
```

Optional SSO:

```bash
# Set AUTH_MODE=hybrid, OIDC_CLIENT_SECRET, KEYCLOAK_ADMIN_PASSWORD in .env.docker
docker compose --env-file .env.docker --profile sso up --build
```

See [deploy/keycloak/README.md](deploy/keycloak/README.md).

### Host development (optional)

Use this when you are not running the full Compose stack. Postgres 16 + pgvector must still be reachable.

```bash
cp .env.example .env
# DATABASE_URL=postgresql+psycopg2://ansibleai:ansibleai@localhost:5432/ansibleai
# SECRET_KEY=…  EMBEDDING_BASE_URL=http://localhost:11434/v1

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m alembic upgrade head
python scripts/seed_admin.py

ollama pull nomic-embed-text
ollama pull qwen2.5-coder:7b

# PYTHONPATH so short imports (config, agent, rag, …) resolve
# Windows PowerShell:
$env:PYTHONPATH="backend"
python backend/rag/indexer.py --reset

npm install
npm run build
python backend/app.py           # http://localhost:5000
```

Without Redis/Celery, `CELERY_TASK_ALWAYS_EAGER=true` (development default) runs generation in-process. Prefer Compose for realistic async + lint behavior.

Dev UI with HMR:

```bash
# Terminal 1 — API
$env:PYTHONPATH="backend"   # Windows PowerShell
python backend/app.py

# Terminal 2 — Vite (proxies /api, /stats, /rag, /docs, /socket.io → :5000)
npm run dev                    # http://localhost:5173
```

`ansible-lint` does not run under native Windows Python. In Docker it runs inside the Linux image (`ANSIBLE_LINT_MODE=native`). On a Windows host use WSL or Docker (`ANSIBLE_LINT_MODE=wsl` / `docker`).

---

## Environment files

| File | Intended for | Committed? | How to use |
|------|--------------|------------|------------|
| `.env.example` | Host `python backend/app.py`, Alembic, local scripts | Yes (template) | `cp .env.example .env` then edit |
| `.env` | Host process. pydantic-settings loads `PROJECT_ROOT/.env`. `load_dotenv` also exports it for modules that still call `os.getenv`. | **No** (`.gitignore`) | Create locally |
| `.env.docker.example` | Compose interpolation + container `env_file` | Yes (template) | `cp .env.docker.example .env.docker` |
| `.env.docker` | `docker compose --env-file .env.docker` | **No** | Create locally; keep host localhost URLs out of this file |
| `.env.local` / `.env.*.local` | — | Ignored if present | Not used by the app code |

There are **no** frontend env files and **no** `VITE_*` / `import.meta.env` reads. The SPA calls same-origin paths.

`.env` vs `.env.docker` are split on purpose: on the host, Postgres and Redis are `localhost`; inside Compose they are `db` and `redis`, and `docker-compose.yml` injects those URLs.

Helm does not use these files. Non-secrets go into a ConfigMap; credentials go into a Kubernetes Secret (see [Kubernetes / Helm](#kubernetes--helm)).

---

## Environment variables

Central definition: `backend/config.py` (`Settings`). Extra variables are read via `os.getenv` in gunicorn, the entrypoint, the agent, ansible-lint, RAG, tests, and Compose/Helm.

**Never commit real secrets.** Examples below are placeholders.

### Application (`backend/config.py`)

| Variable | Required | Used by | Purpose | Example / format | Secret? |
| -------- | -------- | ------- | ------- | ---------------- | ------- |
| `APP_ENV` | no (default `development`) | config, Flask | `development` / `staging` / `production`. Outside development: secure cookies, HSTS, no eager Celery, no in-memory cancel/log backends, `SECRET_KEY` ≥ 32 chars, `DEBUG` off, `REGISTRATION_MODE=open` forbidden | `development` | no |
| `DEBUG` | no | Flask | Must be false outside development | `false` | no |
| `PORT` | no (5000) | gunicorn bind, healthcheck | HTTP listen port | `5000` | no |
| `SECRET_KEY` | **yes** | sessions, CSRF | Signing key. Generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"` | `your_secret_key_here` | **yes** |
| `DATABASE_URL` | **yes** | SQLAlchemy, Alembic | `postgresql+psycopg2://user:pass@host:5432/db` | `postgresql+psycopg2://ansibleai:your_database_password_here@localhost:5432/ansibleai` | **yes** |
| `DB_POOL_RECYCLE` | no (300) | SQLAlchemy pool | Connection recycle seconds | `300` | no |
| `SESSION_BACKEND` | no (`sqlalchemy` host; `redis` Compose) | Flask-Session | `sqlalchemy` / `redis` / `filesystem` | `redis` | no |
| `REDIS_URL` | if any Redis backend is selected | sessions, limiter, cancel, logs, budgets, Celery fallback | Redis URL | `redis://localhost:6379/0` | depends |
| `SESSION_LIFETIME_MINUTES` | no (720) | cookie / session TTL | Absolute session lifetime | `720` | no |
| `SESSION_IDLE_TIMEOUT_MINUTES` | no (120) | settings only | Declared idle timeout; not enforced in `auth/security.py` | `120` | no |
| `SESSION_COOKIE_NAME` | no (`ansibleai_session`) | cookie | Session cookie name | `ansibleai_session` | no |
| `SESSION_COOKIE_SECURE` | no | cookie | Forced true outside development | `false` | no |
| `FORCE_HTTPS` | no | Talisman | Forced true outside development | `false` | no |
| `REGISTRATION_MODE` | no (`domain` in code; Compose example `open`) | register | `closed` / `domain` / `open` | `open` | no |
| `ALLOWED_EMAIL_DOMAINS` | if mode is `domain` | register | Comma-separated domains | `example.com` | no |
| `REQUIRE_ADMIN_APPROVAL` | no (`true` in code; Compose example `false`) | register | Inactive until an admin exists (first user always active) | `false` | no |
| `PASSWORD_MIN_LENGTH` | no (12, min 8) | password policy | NIST-style length floor | `12` | no |
| `BOOTSTRAP_ADMIN_EMAIL` | with password | migrate / seed_admin | First admin seed | `admin@ansibleai.local` | no |
| `BOOTSTRAP_ADMIN_PASSWORD` | with email | migrate / seed_admin | Must satisfy password policy (Compose: min 12 chars) | `your_admin_password_here` | **yes** |
| `RATE_LIMIT_ENABLED` | no (`true`) | Flask-Limiter | Disable only for tests | `true` | no |
| `RATE_LIMIT_BACKEND` | no (`memory`; Compose `redis`) | limiter storage | `memory` / `redis` | `redis` | no |
| `RATE_LIMIT_LOGIN` | no | `POST /api/auth/login` | Flask-Limiter spec | `10 per minute;60 per hour` | no |
| `RATE_LIMIT_REGISTER` | no | `POST /api/auth/register` | Flask-Limiter spec | `5 per hour` | no |
| `RATE_LIMIT_CHAT` | no | **defined only** | Default `30 per hour`; not applied to `/api/chat` | `30 per hour` | no |
| `ACCOUNT_LOCKOUT_THRESHOLD` | no (8) | local login | Failed attempts before lock | `8` | no |
| `ACCOUNT_LOCKOUT_MINUTES` | no (15) | local login | Lock duration | `15` | no |
| `AUTH_MODE` | no (`local`) | auth | `local` / `hybrid` / `oidc` | `local` | no |
| `OIDC_ISSUER` | if hybrid/oidc | OIDC | Browser-facing issuer (`iss`) | `http://localhost:8080/realms/ansibleai` | no |
| `OIDC_INTERNAL_BASE_URL` | recommended in Compose | token/JWKS fetch | In-cluster Keycloak origin | `http://keycloak:8080` | no |
| `OIDC_CLIENT_ID` | if hybrid/oidc | OIDC | Confidential client | `ansibleai-web` | no |
| `OIDC_CLIENT_SECRET` | if hybrid/oidc | OIDC | Client secret | `your_oidc_client_secret_here` | **yes** |
| `OIDC_REDIRECT_URI` | if hybrid/oidc | hosted-UI callback | Must match Keycloak client | `http://localhost:5000/api/auth/oidc/callback` | no |
| `OIDC_SCOPES` | no | OIDC | Space-separated | `openid email profile` | no |
| `OIDC_ADMIN_GROUP` | no | claim mapping | `ansibleai-admins` | `ansibleai-admins` | no |
| `OIDC_ADMIN_ROLE` | no | claim mapping | `ansibleai-admin` | `ansibleai-admin` | no |
| `AUTH_BREAK_GLASS_EMAILS` | no | login | Comma-separated emails that may use a local hash in oidc mode | `admin@ansibleai.local` | no |
| `OIDC_RETIRE_LOCAL_PASSWORD` | no (`true`) | SSO link | Drop local hash except break-glass | `true` | no |
| `OIDC_REQUIRE_EMAIL_VERIFIED` | no (`true`; Compose often `false`) | SSO | Require Keycloak `email_verified` | `false` | no |
| `OIDC_BROWSER_REDIRECT` | no (`false`) | `/api/auth/oidc/login` | Advertise hosted Keycloak UI | `false` | no |
| `OIDC_MAP_APP_ADMIN` | no (`false`) | SSO | Map Keycloak admin group → `users.role=admin` | `false` | no |
| `KEYCLOAK_ADMIN` | optional | Keycloak Admin API | Master-realm admin for password change | `admin` | no |
| `KEYCLOAK_ADMIN_PASSWORD` | optional | Keycloak Admin API | Companion password | `your_keycloak_admin_password_here` | **yes** |
| `USER_DAILY_TOKEN_BUDGET` | no (`0`) | Celery worker | Daily LLM token cap per user; `0` = unlimited | `0` | no |
| `LOG_LEVEL` | no (`DEBUG` in code) | structlog | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` | `INFO` | no |
| `LOG_FORMAT` | no (`console`) | structlog | `console` / `json` | `console` | no |
| `CORS_ORIGINS` | no | Socket.IO CORS | Comma-separated browser origins. Missing the origin you open causes “Reconnecting…” | `http://localhost:5000,http://localhost:5173` | no |
| `SOCKETIO_ASYNC_MODE` | no (`threading` host; `gevent` API container) | Flask-SocketIO | Must match gunicorn worker class | `gevent` | no |
| `SOCKETIO_MESSAGE_QUEUE` | required outside development | Socket.IO Redis | Worker → API emit channel | `redis://redis:6379/1` | no |
| `CELERY_BROKER_URL` | Compose | Celery | Defaults to `REDIS_URL` if empty | `redis://redis:6379/2` | no |
| `CELERY_RESULT_BACKEND` | Compose | Celery | Status dict only | `redis://redis:6379/2` | no |
| `CELERY_TASK_ALWAYS_EAGER` | no (`true` in development) | Celery | Inline tasks; **refused** outside development | `false` | no |
| `CELERY_SOFT_TIME_LIMIT` | no (1500) | Celery | Soft timeout so the task can persist a failure | `1500` | no |
| `CELERY_TIME_LIMIT` | no (1620) | Celery | Hard kill; must exceed soft limit | `1620` | no |
| `CELERY_PREFETCH_MULTIPLIER` | no (1) | Celery | Keep at 1 for long LLM tasks | `1` | no |
| `CELERY_WORKER_CONCURRENCY` | no (2) | Celery / entrypoint | Concurrent generations per worker | `2` | no |
| `CANCEL_BACKEND` | no (`memory`; Compose `redis`) | cancel flags | `memory` forbidden outside development | `redis` | no |
| `CANCEL_TTL_SECONDS` | no (3600) | cancel keys | TTL so a crash cannot leave a thread “running” forever | `3600` | no |
| `LOG_STREAM_BACKEND` | no (`memory`; Compose `redis`) | SSE scrape logs | Same constraint as cancel | `redis` | no |
| `LOG_STREAM_MAX_ENTRIES` | no (5000) | logstream | Cap | `5000` | no |
| `LOG_STREAM_TTL_SECONDS` | no (86400) | logstream | TTL | `86400` | no |
| `ARTIFACT_BACKEND` | no (`local`; Compose `s3`) | storage | `local` / `s3` | `s3` | no |
| `ARTIFACT_LOCAL_DIR` | no (`output`) | local archive | Directory under repo root | `output` | no |
| `S3_ENDPOINT_URL` | if `s3` | boto3 | MinIO / S3 endpoint | `http://minio:9000` | no |
| `S3_BUCKET` | no (`ansibleai-playbooks`) | boto3 | Bucket name | `ansibleai-playbooks` | no |
| `S3_ACCESS_KEY` | if `s3` | boto3 | Access key | `your_s3_access_key_here` | **yes** |
| `S3_SECRET_KEY` | if `s3` | boto3 | Secret key | `your_s3_secret_key_here` | **yes** |
| `S3_REGION` | no (`us-east-1`) | boto3 | Region | `us-east-1` | no |
| `AGENT_MODEL` | no (`qwen2.5-coder:7b`) | agent LLM | Ollama tag for reason/repair/respond | `qwen2.5-coder:7b` | no |
| `AGENT_MAX_ITERATIONS` | no (`4`) | agent state | Draft attempts (1 initial + repairs). `.env.example` comments `3`; code default is `4`. | `4` | no |
| `AGENT_REQUEST_TIMEOUT` | no (300) | Ollama client | Seconds per LLM call | `300` | no |
| `OLLAMA_BASE_URL` | no (`http://localhost:11434`) | LLM + embeddings fallback | Compose uses `http://host.docker.internal:11434` | `http://localhost:11434` | no |
| `OLLAMA_MODEL` | no | pipeline/RAG CLI | Legacy codegen tag; chat uses `PLAYBOOK_MODEL` / `AGENT_MODEL` | `qwen2.5-coder:14b` | no |
| `PLAYBOOK_MODEL` | no (`qwen2.5-coder:14b` in settings) | YAML draft | Dedicated codegen model if set | `qwen2.5-coder:7b` | no |
| `PLAYBOOK_MAX_TOKENS` | no (2500) | draft | Too low truncates YAML; repair cannot recover | `2500` | no |
| `PLAYBOOK_TEMPERATURE` | no (0.2) | draft | Sampling temperature | `0.2` | no |
| `RAG_PARSED_DIR` | no (`data/parsed`) | ingestion | Parsed module JSON root | `data/parsed` | no |
| `RAG_MAX_CHUNKS_PER_COLLECTION` | no | retriever | Diversity cap when unfiltered | `4` | no |
| `RAG_MAX_CHUNKS_PER_MODULE` | no | retriever | Diversity cap | `2` | no |
| `EMBEDDING_BASE_URL` | recommended | embeddings client | OpenAI-compatible `/v1`. Falls back to `OLLAMA_BASE_URL/v1` | `http://localhost:11434/v1` | no |
| `EMBEDDING_MODEL` | no (`nomic-embed-text`) | indexer | Embedding model id | `nomic-embed-text` | no |
| `EMBEDDING_DIMENSIONS` | no (768) | pgvector column | Must match the model | `768` | no |
| `EMBEDDING_BATCH_SIZE` | no (64) | indexer | Batch size | `64` | no |
| `EMBEDDING_API_KEY` | optional | embeddings | Bearer for TEI / OpenAI-compatible servers that require it | `your_api_key_here` | **yes** |
| `VECTOR_INDEX_VERSION` | no (`v3_pgvector`) | indexer | Schema compatibility marker | `v3_pgvector` | no |
| `VECTOR_COLLECTION` | no (`ansible_docs`) | vector store | Collection name | `ansible_docs` | no |
| `ANSIBLE_LINT_MODE` | no (`auto`) | lint runner | `auto` / `native` / `wsl` / `docker` / `skip` | `native` | no |
| `LANGFUSE_ENABLED` | no (`false`) | tracing | Opt-in traces | `false` | no |
| `LANGFUSE_PUBLIC_KEY` | if enabled | Langfuse SDK | `pk-lf-…` | `pk-lf-your_public_key_here` | **yes** |
| `LANGFUSE_SECRET_KEY` | if enabled | Langfuse SDK | `sk-lf-…` | `sk-lf-your_secret_key_here` | **yes** |
| `LANGFUSE_HOST` | no (`http://localhost:3000`) | SDK alias | From Compose use `http://host.docker.internal:3000` | `http://localhost:3000` | no |
| `LANGFUSE_BASE_URL` | no | SDK v3 canonical URL | Mirrors host when unset | `http://host.docker.internal:3000` | no |
| `LANGFUSE_TRACING_ENVIRONMENT` | no | Langfuse UI filter | `development` / `staging` / `production` | `development` | no |

### Agent / lint / RAG extras (`os.getenv`)

| Variable | Required | Used by | Purpose | Example | Secret? |
| -------- | -------- | ------- | ------- | ------- | ------- |
| `AGENT_FAST_PLANNER` | no (default on) | `agent/graph.py` | `0` always calls the planner LLM; default skips it when heuristics already decide intent | `1` | no |
| `OLLAMA_KEEP_ALIVE` | no (`30m`) | `agent/llm.py` | How long Ollama keeps weights; `-1` pins for process life | `30m` | no |
| `RAG_DISABLE_AUTO_COLLECTION_FILTER` | no | retriever | Force unfiltered search | `1` | no |
| `ANSIBLE_LINT_CMD` | no | lint runner | Override lint executable | | no |
| `ANSIBLE_LINT_WSL_DISTRO` | no | lint runner | WSL distro name | `Ubuntu` | no |
| `ANSIBLE_LINT_DOCKER_IMAGE` | no | lint runner | Default `pipelinecomponents/ansible-lint:latest` | | no |
| `RAGAS_JUDGE_MODEL` | no | `rag/evaluator.py` | Judge model for RAGAS eval | `qwen2.5-coder:14b` | no |
| `FLASK_USE_RELOADER` | no | `app.py` `__main__` | Enable Werkzeug reloader | `1` | no |
| `FLASK_ENV` | unused | `.env.example` comment only | Application uses `APP_ENV` | — | no |

### Gunicorn / container entrypoint

| Variable | Required | Used by | Purpose | Example | Secret? |
| -------- | -------- | ------- | ------- | ------- | ------- |
| `APP_ROLE` | worker healthcheck | entrypoint | `api` / `worker` / … | `worker` | no |
| `GUNICORN_WORKERS` | no (1) | gunicorn | Raise only behind sticky sessions | `1` | no |
| `GUNICORN_WORKER_CLASS` | no (`gevent`) | gunicorn | `gevent` or `gthread` | `gevent` | no |
| `GUNICORN_THREADS` | no (1) | gunicorn | For `gthread` | `100` | no |
| `GUNICORN_WORKER_CONNECTIONS` | no (1000) | gunicorn | gevent connections | `1000` | no |
| `GUNICORN_TIMEOUT` | no (600) | gunicorn | Must exceed worst-case agent loop | `600` | no |
| `GUNICORN_GRACEFUL_TIMEOUT` | no (90) | gunicorn | Drain in-flight requests | `90` | no |
| `GUNICORN_KEEPALIVE` | no (5) | gunicorn | Keep-alive | `5` | no |
| `GUNICORN_BACKLOG` | no (2048) | gunicorn | Listen backlog | `2048` | no |
| `GUNICORN_MAX_REQUESTS` | no (0) | gunicorn | Worker recycle; 0 = off | `0` | no |
| `GUNICORN_MAX_REQUESTS_JITTER` | no (0) | gunicorn | Jitter | `0` | no |
| `GUNICORN_LOG_LEVEL` | no (`info`) | gunicorn | Error log level | `info` | no |
| `CELERY_LOG_LEVEL` | no (`info`) | entrypoint worker | Celery log level | `info` | no |
| `CELERY_MAX_TASKS_PER_CHILD` | no (50) | entrypoint worker | Recycle child after N tasks | `50` | no |
| `CELERY_PING_TIMEOUT` | no (5) | worker healthcheck | `celery inspect ping` timeout | `5` | no |
| `PYTHONPATH` | image sets `/app/backend` | imports | Required for short imports | `backend` | no |

### Compose interpolation (not Settings fields)

Set in `.env.docker`. Compose injects derived `DATABASE_URL`, `REDIS_URL`, S3 keys, etc.

| Variable | Required | Used by | Purpose | Secret? |
| -------- | -------- | ------- | ------- | ------- |
| `POSTGRES_DB` | no (`ansibleai`) | Postgres + URL | Database name | no |
| `POSTGRES_USER` | no (`ansibleai`) | Postgres + URL | Role | no |
| `POSTGRES_PASSWORD` | **yes** for Compose | Postgres | App DB password | **yes** |
| `MINIO_ROOT_USER` | no (`ansibleai`) | MinIO + `S3_ACCESS_KEY` | Root user (lab only) | no |
| `MINIO_ROOT_PASSWORD` | **yes** for Compose | MinIO + `S3_SECRET_KEY` | Min 8 chars | **yes** |
| `MINIO_API_PORT` | no (9000) | host publish | Debug S3 API | no |
| `MINIO_CONSOLE_PORT` | no (9001) | host publish | MinIO console | no |
| `API_PORT` | no (5000) | host publish | App HTTP | no |
| `KEYCLOAK_HOSTNAME` | SSO profile | Keycloak | `http://localhost:8080` | no |
| `KEYCLOAK_PORT` | SSO profile | publish | `8080` | no |

### Observability Compose (Langfuse / Grafana)

Used by `docker-compose.observability.yml` and `deploy/observability/docker-compose.langfuse.yml`. Not read by Flask except the `LANGFUSE_*` keys above.

| Variable | Required | Purpose | Secret? |
| -------- | -------- | ------- | ------- |
| `GRAFANA_ADMIN_USER` | no (`admin`) | Grafana login | no |
| `GRAFANA_ADMIN_PASSWORD` | recommended | Grafana login | **yes** |
| `NEXTAUTH_URL` | Langfuse (`http://localhost:3000`) | Langfuse web | no |
| `NEXTAUTH_SECRET` | Langfuse | NextAuth signing | **yes** |
| `SALT` | Langfuse | Hash salt | **yes** |
| `ENCRYPTION_KEY` | Langfuse | 64 hex chars (`openssl rand -hex 32`) | **yes** |
| `LANGFUSE_DB_PASSWORD` | Langfuse Postgres | Langfuse DB | **yes** |
| `LANGFUSE_REDIS_AUTH` | Langfuse Redis | Redis password | **yes** |
| `CLICKHOUSE_PASSWORD` | Langfuse ClickHouse | ClickHouse | **yes** |
| `LANGFUSE_MINIO_USER` | no (`minio`) | Langfuse MinIO | no |
| `LANGFUSE_MINIO_PASSWORD` | recommended | Langfuse MinIO (min 8 chars) | **yes** |

### Tests / E2E

| Variable | Required | Purpose |
| -------- | -------- | ------- |
| `E2E_RUN` | to run live E2E | `1` enables `tests/test_e2e_platform.py` |
| `E2E_MODE` | no (`api`) | `api` or `pipeline` |
| `E2E_BASE_URL` | no (`http://127.0.0.1:5000`) | Live API |
| `E2E_TIMEOUT` | no (`900`) | Seconds per case |
| `ANSIBLEAI_TEST_AUTH_MODE` | tests/conftest | If unset, conftest forces `AUTH_MODE=local` |

---

## Database

**Technology:** PostgreSQL 16 with the `vector` extension (image `pgvector/pgvector:pg16`). Phase 3 replaced MySQL and ChromaDB.

**Connection:** `DATABASE_URL` (SQLAlchemy + Alembic). Compose builds it from `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` against host `db`.

**Migrations:** Alembic, `alembic.ini` → `backend/migrations`. URL is **not** stored in `alembic.ini`; `migrations/env.py` reads `config.settings`.

```bash
python -m alembic upgrade head
```

Compose `migrate` service runs `alembic upgrade head` then `scripts/seed_admin.py` when bootstrap credentials are set.

| Revision | What it adds |
|----------|----------------|
| `0001_baseline` | `generations`, `scrape_sessions`, `module_versions`, `chat_threads`, `chat_messages` (guarded so existing installs stamp cleanly) |
| `0002_user_management` | `users`, `audit_events`, `chat_threads.user_id` |
| `0003_pgvector` | `CREATE EXTENSION vector`, `document_chunks` (HNSW + GIN), `index_meta` |

Flask-Session may create a `sessions` table when `SESSION_BACKEND=sqlalchemy`.

### Entities

```text
users 1──* chat_threads 1──* chat_messages
users 1──* audit_events          (user_id SET NULL on delete)
scrape_sessions 1──* module_versions   (logical; module_versions.scrape_session_id is not an FK)
generations                          (one row per successful playbook persist from the agent)
document_chunks                      (embeddings; no FK to chat)
index_meta                           (key/value index schema markers)
```

| Table | Important fields |
|-------|------------------|
| `users` | email, password_hash (nullable for SSO), role (`user`/`admin`), is_active, provider (`local`/`keycloak`), external_id, failed_login_count, locked_until, session_epoch |
| `chat_threads` | user_id, title, timestamps |
| `chat_messages` | role `user`/`assistant`, content, playbook, validation JSON, rag_meta, tool_trace |
| `audit_events` | event, outcome, ip, user_agent, detail JSON, denormalized actor_email |
| `generations` | request, module, playbook, is_valid, warnings, errors |
| `scrape_sessions` | status `running\|success\|failed\|partial`, modules_updated/failed, summary |
| `document_chunks` | doc_id, collection_name, content, metadata JSONB, embedding vector(768) |

**Seed:** `python scripts/seed_admin.py` — idempotent; re-run resets the bootstrap admin password and re-activates the account (lockout recovery). Pre-existing threads from before user management are assigned to that email (or `admin@ansibleai.local`).

Unit tests use a throwaway SQLite file (`tests/conftest.py`), not Postgres.

---

## Authentication and authorization

### Mechanisms

| Mode | Login UI | Identity store | Registration | App admin chrome |
|------|----------|----------------|--------------|------------------|
| `local` (default) | Email + password | argon2id on `users.password_hash` | `REGISTRATION_MODE` | Yes (`role=admin`) |
| `hybrid` / `oidc` | Email + password on AnsibleAI | Keycloak ROPC | Disabled (invite-only in Keycloak) | Hidden (`app_admin_ui=false`) |
| Break-glass emails | Local argon2id | Local hash | n/a | n/a |

Default stays `local` so tests and `python backend/app.py` need no identity provider.

### Session / token

- **Browser:** Flask-Login + Flask-Session. Cookie `ansibleai_session` (HttpOnly, SameSite=Lax; Secure outside development). Identifier is `user_id:session_epoch`. Password change or `invalidate_sessions()` bumps the epoch and revokes every session.
- **CSRF:** Flask-WTF. SPA reads non-HttpOnly `csrf_token` cookie and sends `X-CSRFToken`. Bearer-only clients skip CSRF.
- **Machine clients:** `Authorization: Bearer <Keycloak access token>` via Flask-Login `request_loader`. Socket.IO may pass `{ token }` on connect.
- **No refresh-token product flow** in the SPA; the session cookie is refreshed on each request (`SESSION_REFRESH_EACH_REQUEST`).

### Password hashing

argon2id (OWASP baseline: 19 MiB, 2 iterations). Policy: length (`PASSWORD_MIN_LENGTH`, default 12), common-password denylist, no composition rules (NIST SP 800-63B). Dummy hash verification equalizes latency on unknown emails.

### Default-deny

Every Flask endpoint requires a session unless it is in `PUBLIC_ENDPOINTS` (`backend/auth/security.py`): SPA shell, CORS preflight, `/healthz`, `/readyz`, `/metrics`, auth login/register/csrf/config/me/oidc.

`ADMIN_ENDPOINTS`: `docs/rescrape`, `docs/check-updates`, `docs/rollback/restore`. Clearing **own** threads is a normal user action.

Forced password-change sessions may only hit a small allow-list until the password is rotated.

### Login flow (local)

1. SPA `GET /api/auth/config` and `GET /api/auth/me`.
2. `POST /api/auth/login` with email/password (rate-limited).
3. Session regenerated (anti-fixation); CSRF cookie refreshed.
4. Further API calls send cookies + CSRF header.

### Login flow (hybrid)

Same form. API calls Keycloak token endpoint (ROPC), upserts `users` (`provider=keycloak`, `external_id=sub`), optionally clears the local hash. Temporary Keycloak passwords set `must_change_password`; the user sets a new password **on AnsibleAI**.

### Registration flow

Disabled when `AUTH_MODE` is hybrid/oidc or `REGISTRATION_MODE=closed`. Duplicate emails return the same 202 “pending approval” payload as a real pending signup (anti-enumeration). First user is admin and is logged in immediately (201).

---

## API documentation

Unless noted, endpoints require an authenticated session (or Bearer token). Writes need CSRF for cookie sessions. JSON bodies unless stated.

### Auth (`/api/auth`)

| Method | Endpoint | Auth | Description |
| ------ | -------- | ---- | ----------- |
| GET | `/api/auth/csrf` | Public | `{ "csrf_token": "…" }` |
| GET | `/api/auth/config` | Public | `auth_mode`, `oidc_enabled`, `local_login_enabled`, `registration_enabled`, `app_admin_ui`, `oidc_login_url` |
| GET | `/api/auth/me` | Public (200 if anonymous) | `{ authenticated, user }` |
| POST | `/api/auth/register` | Public, rate-limited | Body: `email`, `password`, optional `display_name`. 201 + session, 202 pending, 400/403 |
| POST | `/api/auth/login` | Public, rate-limited | Body: `email`, `password`. 200 or 401 `invalid_credentials` (generic message) |
| POST | `/api/auth/logout` | Required | Clears session |
| GET | `/api/auth/profile` | Required | User, `usage` token budget, `activity` |
| POST | `/api/auth/password/change` | Required | `current_password`, `new_password`. Revokes other sessions |
| GET | `/api/auth/oidc/login` | Public | 404 unless OIDC + `OIDC_BROWSER_REDIRECT`. Redirects to Keycloak (PKCE) |
| GET | `/api/auth/oidc/callback` | Public | Finishes hosted SSO; redirects `/?sso=…` on error |

Login error codes include `invalid_credentials`, `missing_fields`, `idp_unavailable`. Registration: `registration_disabled`, `weak_password`, `invalid_email`.

### Chat and threads

| Method | Endpoint | Auth | Description |
| ------ | -------- | ---- | ----------- |
| POST | `/api/chat` | Required | Enqueue generation. **202** |
| POST | `/api/chat/cancel` | Required | Body `{ "thread_id": int }` |
| GET | `/api/chat/status/<thread_id>` | Required | `{ running, cancelling }` |
| GET | `/api/threads` | Required | Own threads (max 200) |
| GET | `/api/threads/<id>` | Required | Thread + messages |
| PATCH | `/api/threads/<id>` | Required | `{ "title": "…" }` |
| DELETE | `/api/threads/<id>` | Required | Delete one |
| DELETE | `/api/threads` | Required | Delete **caller’s** threads |

`POST /api/chat` body:

```json
{ "thread_id": 12, "message": "Create an S3 bucket with versioning" }
```

`thread_id` optional (creates a thread). Message max 8000 characters.

**202:**

```json
{
  "job_id": "celery-task-id",
  "thread": { "id": 12, "title": "Create an S3 bucket…", "message_count": 1 },
  "user_message": { "id": 40, "role": "user", "content": "…" }
}
```

Errors: `400` empty, `413` too long, `404` thread not owned, `409` `already_running`, `503` `enqueue_failed`.

The assistant message is **not** in the HTTP response. Clients wait for Socket.IO `generation_complete` or poll `/api/chat/status/<id>`.

### Stats, RAG, modules

| Method | Endpoint | Auth | Description |
| ------ | -------- | ---- | ----------- |
| GET | `/stats` | Required | Counts from `generations` (global, not per-user) |
| GET | `/rag/status` | Required | `{ available, chunks, embed_model, backend: "pgvector", index_version }` |
| GET | `/module/<slug>` | Required | Structured module reference from the KB |

### Knowledge-base admin

| Method | Endpoint | Auth | Description |
| ------ | -------- | ---- | ----------- |
| GET | `/docs/status` | Required | KB metadata + module health scores |
| GET | `/docs/rollback/list` | Required | Snapshot filenames under `data/kb_versions` |
| POST | `/docs/rollback/restore` | **Admin** | `{ "filename": "…" }` (path traversal rejected) |
| GET | `/docs/sessions?limit=` | Required | Recent scrape sessions (1–200) |
| GET | `/docs/sessions/<id>` | Required | Session + `module_versions` |
| GET | `/docs/stream/<id>` | Required | SSE log tail (`text/event-stream`) |
| POST | `/docs/check-updates` | **Admin** | Starts a background thread; `{ session_id }` |
| POST | `/docs/rescrape` | **Admin** | `{ "modules": ["amazon.aws::s3_bucket", …] }` max 2000 |

Check-updates and rescrape run **in-process threads** on the API, not Celery. They fetch HTML from docs.ansible.com.

### Platform

| Method | Endpoint | Auth | Description |
| ------ | -------- | ---- | ----------- |
| GET | `/healthz` | Public | `{ "status": "ok" }` — no dependency checks |
| GET | `/readyz` | Public | DB, schema, non-empty KB, broker (hard); vector store (soft). **503** if a hard check fails |
| GET | `/metrics` | Public | Prometheus text. Network-restrict in real clusters |
| GET | `/` | Public | SPA `index.html` |
| GET | `/assets/<path>` | Public | Vite hashed assets |
| OPTIONS | `/`, `/api/*`, `/stats`, `/rag/*`, `/docs/*` | Public | CORS preflight, 204 |

Typical HTTP errors: `401` `unauthenticated`, `403` `forbidden` / `csrf` / `password_change_required`, `429` `rate_limited`.

---

## WebSocket events

Flask-SocketIO at path `/socket.io`. Unauthenticated connections are rejected. Each user joins room `user:<id>`.

**Client → server:** connect with session cookie (or `{ token }` for Bearer).

**Server → client** (per-user room only; never broadcast):

| Event | Payload | When |
|-------|---------|------|
| `generation_progress` | `{ thread_id, step, message, detail? }` | Agent steps: `planning`, `retrieving`, `generating`, `validating`, `synthesizing` |
| `generation_complete` | `{ thread_id, thread }` | Turn settled; client should refetch messages |
| `generation_failed` | `{ thread_id, error }` | Failure |
| `generation_cancelled` | `{ thread_id, error }` | User stop (also emits `generation_failed` for older listeners) |
| `thread_upserted` / `thread_updated` / `thread_deleted` / `threads_cleared` | thread dict or `{ id }` | Thread mutations |

The SPA also polls `/api/chat/status/:id` every 5s for up to 30 minutes if the socket drops (`ChatProvider` watchdog).

---

## The LangGraph agent

Package: `backend/agent/`. Entry: `orchestrator.handle_message()` → graph in `graph.py`. Celery task: `tasks.run_generation`.

```text
START → reason ──→ tools ──→ reason
           │
           ├──→ ask_user → END
           ├──→ draft → gate ──→ reason   (repair loop)
           │              └──→ respond → END
           └──→ respond → END
```

| Node | Behavior |
|------|----------|
| **reason** | CoT JSON: intent, search query, or clarifying questions. After a failed gate, a fix plan. `AGENT_FAST_PLANNER` can skip the LLM when heuristics already decide. |
| **tools** | `search_docs` (hybrid RAG + collection routing); `validate_yaml` for pasted YAML |
| **draft** | One YAML generation/repair pass (`PLAYBOOK_MODEL` or `AGENT_MODEL`) |
| **gate** | KB validator + ansible-lint. Production-ready = 0 errors, lint passed, no placeholders. Environment failures (missing lint backend) do **not** burn repair iterations |
| **respond** | Final reply with gate verdict |
| **ask_user** | Stops with clarifying questions when the request cannot be grounded |

Loop budget: `AGENT_MAX_ITERATIONS` (code default **4**).

### Tools (`agent/tools.py`)

| Tool | Purpose |
|------|---------|
| `search_docs` | Hybrid search over pgvector + BM25 |
| `draft_playbook` | One draft/repair YAML pass |
| `validate_playbook_file` / `validate_yaml` | KB-aware validator + ansible-lint |
| `get_module_info` | Structured module reference from the knowledge base |

Worker warm-up (`tasks.py` `worker_ready`) prefetches Ollama weights so the first chat of the day does not stall on a cold load.

---

## Knowledge base and RAG

### Pipeline (`backend/pipeline/`)

| Stage | Module | Output |
|-------|--------|--------|
| Scrape | `phase1_scraper.py` | `data/raw_html/<collection>/<module>.html` from docs.ansible.com |
| Parse | `phase2_parser.py` | Structured fields from HTML |
| Structure | `phase3_structurer.py` | KB module entries (params, examples, required) |
| Validate | `validator.py` | YAML / structure / FQCN / required params / placeholders / secrets / ansible-lint |
| Lint runner | `ansible_lint_runner.py` | native / WSL / Docker / skip |
| Store | `kb_store.py` | `data/parsed` + `kb_manifest.json` |

Collections in the scraper registry and in `docker/ansible-collections.yml`: `kubernetes.core`, `amazon.aws`, `azure.azcollection`, `community.general`, `ansible.builtin` (builtin ships with ansible-core).

### Index and retrieve (`backend/rag/`)

1. **Ingestion** — chunk parsed docs (`CHUNK_SCHEMA_VERSION`).
2. **Embeddings** — OpenAI-compatible `POST {EMBEDDING_BASE_URL}/embeddings` (`rag/embeddings.py`). Typically Ollama `nomic-embed-text` (768-d) or Hugging Face TEI.
3. **Indexer** — upsert into `document_chunks` (`python backend/rag/indexer.py [--reset] [--collection …]`).
4. **Hybrid search** — dense cosine (HNSW) + BM25, RRF fusion, collection routing, query-aware rerank, diversity caps (`hybrid_search.py`, `retriever.py`, `sparse_index.py`).
5. **Invalidation** — `rag/invalidation.py` after KB changes.

CLI orchestrator: `python backend/rag/pipeline.py --build|--status|--query|--evaluate`.

Benchmark dataset: `backend/rag/retrieval_benchmark.json`. Eval helpers live in `scripts/` (`eval_retrieval.py`, `trace_query.py`, ablations).

---

## External services

| Service | Purpose | Where | Env | Required? |
|---------|---------|-------|-----|-----------|
| **Ollama** | Planner, codegen, embeddings `/v1/embeddings` | Host (Compose: `host.docker.internal:11434`; Helm: laptop Endpoints) | `OLLAMA_BASE_URL`, `AGENT_MODEL`, `PLAYBOOK_MODEL`, `EMBEDDING_*` | **Yes** for generation and indexing |
| **PostgreSQL + pgvector** | App data + vectors | Compose `db` / Helm StatefulSet | `DATABASE_URL` | **Yes** (except SQLite unit tests) |
| **Redis** | Sessions, limiter, Celery, Socket.IO, cancel, SSE, budgets | Compose `redis` | `REDIS_URL` and related | **Yes** outside single-process eager mode |
| **MinIO** | Playbook archive | Compose `minio` | `S3_*` | When `ARTIFACT_BACKEND=s3` |
| **docs.ansible.com** | Upstream HTML for scrape / check-updates | HTTP GET from API threads | none | Only for KB refresh |
| **Keycloak** | Optional IdP | Compose profile `sso` | `AUTH_MODE`, `OIDC_*` | Only if not `local` |
| **Prometheus** | Scrape `/metrics` | `docker-compose.observability.yml` | — | Optional |
| **Grafana** | Provisioned “AnsibleAI overview” dashboard | same file, `:3001` | `GRAFANA_ADMIN_*` | Optional |
| **Langfuse v3** | LLM traces | separate Compose project `:3000` | `LANGFUSE_*` | Optional (no-op if disabled) |
| **Ansible Galaxy** | Collections baked into the image | Docker build | — | For lint of non-builtin modules |

There is no Maps, weather, payment, SMTP, or Firebase integration in application code. Keycloak SMTP is configured in the Keycloak console when used, not by AnsibleAI.

oauth2-proxy (`deploy/keycloak/k8s/oauth2-proxy.yaml`) is a Kubernetes ingress pattern and is **not** required on the laptop stack or for in-app ROPC.

---

## Frontend

SPA in `frontend/` (React 19). Production build: `vite.config.ts` → `static/dist/` (Flask `/` and `/assets`). Dev server `:5173` proxies API and Socket.IO to `:5000`.

| Area | Components / modules |
|------|----------------------|
| Shell | `App.tsx`, `AppHeader`, `AppFooter`, `ThreadSidebar`, `ChatMain`, `SidePanel` |
| Auth | `LoginPage`, `AccountPanel` / force-password, `AuthProvider` |
| Chat | `ChatComposer`, `MessageList`, `MessageBubble`, `PlaybookCard`, `ValidationCard`, `AgentThinking`, `SourceChip`, `WelcomeScreen` |
| Providers | Auth, Chat, Threads, Socket, Panel, Onboarding, App |
| API | `lib/api.ts` (credentials + CSRF) |
| Onboarding | four-step overlay stored in the browser |

No client-side router library: auth gate then a single workspace shell. `isAdmin` is `role === 'admin'` **and** `app_admin_ui` (local mode).

### Frontend Design Mode

To inspect every screen with mock data and **no backend**, see [frontend/DESIGN_MODE.md](frontend/DESIGN_MODE.md).

```bash
cd frontend
npm install
npm run dev:design
```

Uses `VITE_DESIGN_MODE=true`. Normal `npm run dev` still proxies to Flask. Production `vite build` does not enable Design Mode.

---

## Available commands

### Root `package.json` (Node ≥ 20)

| Command | What it does |
|---------|----------------|
| `npm install` | Installs root then `frontend` (`postinstall`) |
| `npm run dev` | Vite HMR on `:5173` |
| `npm run build` | `tsc --noEmit && vite build` → `static/dist/` |
| `npm run preview` | Vite preview of the production build |

### `frontend/package.json`

| Command | What it does |
|---------|----------------|
| `npm run lint` | ESLint on `src` |
| `npm run build` / `dev` / `preview` | Same as above, from `frontend/` |
| `npm run dev:design` | Vite with `VITE_DESIGN_MODE=true` (no Flask required) |

### Python

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt          # pytest, ruff, mypy, ragas, …

python -m alembic upgrade head
python scripts/seed_admin.py
python backend/app.py                        # Werkzeug; refused outside APP_ENV=development

# PYTHONPATH=backend (PowerShell: $env:PYTHONPATH="backend")
python backend/rag/indexer.py --reset
python backend/rag/pipeline.py --status
python backend/rag/pipeline.py --build
python scripts/trace_query.py "scale deployment to 3 replicas"
python scripts/eval_retrieval.py
python scripts/compare_eval_runs.py reports/a.json reports/b.json
python scripts/run_e2e_eval.py --mode api
python scripts/smoke_auth.py
python scripts/diagnose_retrieval.py
# Ablation / sweep helpers: ablate_*.py, sweep_*.py

celery -A tasks worker --loglevel=info --pool=solo   # Windows host worker
```

### Quality

```bash
pre-commit install
pre-commit run --all-files
ruff check .
ruff format .
mypy
```

### Docker Compose

```bash
docker compose --env-file .env.docker up --build -d
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker up -d --scale worker=3
docker compose --env-file .env.docker run --rm api smoke
docker compose --env-file .env.docker run --rm api exec python backend/rag/pipeline.py --build
docker compose --env-file .env.docker exec api python backend/rag/indexer.py --reset
```

### Image roles (`docker/entrypoint.sh`)

```bash
docker run ansibleai/app              # api (gunicorn)
docker run ansibleai/app worker
docker run ansibleai/app migrate
docker run ansibleai/app smoke
docker run ansibleai/app exec python backend/rag/indexer.py --reset
```

---

## Testing

**Framework:** pytest (`pyproject.toml`: `testpaths = ["tests"]`, `pythonpath = ["backend"]`). Markers: `e2e`, `slow`.

```bash
pip install -r requirements-dev.txt
pytest tests/ -q --ignore=tests/e2e

# Live E2E (running app + LLM)
# PowerShell: $env:E2E_RUN=1
set E2E_RUN=1
pytest tests/test_e2e_platform.py -v -s
```

There is **no** pytest coverage command in project config.

| Area | Files |
|------|--------|
| Auth / CSRF / default-deny | `test_auth.py`, `test_authz.py`, `test_oidc.py` |
| Async chat, cancel, backends | `test_async_generation.py`, `test_cancel.py`, `test_state_backends.py` |
| Agent gate / prompts | `test_agent_gate.py`, `test_agent_prompts.py` |
| RAG / hybrid search | `test_hybrid_search.py`, `test_retriever_*.py`, `test_retrieval_ranking.py`, `test_sparse_index.py`, `test_phase3_pgvector.py` |
| Validator / lint / Jinja | `test_validator_module_detection.py`, `test_ansible_lint_runner.py`, `test_jinja_quoting.py`, `test_placeholder_jinja.py` |
| Budgets / observability | `test_budgets.py`, `test_observability.py` |
| Container / Helm / Ansible | `test_container_config.py`, `test_helm_chart.py`, `test_cluster_ansible.py` |
| E2E golden set | `tests/e2e/` (30 cases, five scoring layers). Details: [tests/e2e/README.md](tests/e2e/README.md) |

`tests/conftest.py` forces a hermetic SQLite DB and `AUTH_MODE=local` before importing settings.

Frontend: ESLint + `tsc --noEmit` (pre-commit). No frontend unit-test runner is configured.

---

## Docker

**Dockerfile** — three stages: `frontend` (Node 20, `npm ci` + build), `deps` (Python 3.12 venv + Galaxy collections), `runtime` (non-root uid **10001**, tini, read-only-friendly dirs). One image, several roles.

**Compose services** (`docker-compose.yml`):

| Service | Image / role | Ports | Notes |
|---------|--------------|-------|--------|
| `db` | pgvector/pgvector:pg16 | not published | Volume `db-data`; healthcheck `pg_isready` |
| `redis` | redis:7-alpine | not published | AOF, `noeviction` |
| `minio` | minio RELEASE.2025-09-07… | 9000, 9001 | Volume `minio-data` |
| `migrate` | app image `migrate` | — | Completes then exits; API/worker wait on it |
| `api` | app image `api` | `${API_PORT:-5000}:5000` | Bind-mounts `data/kb_versions`, `data/raw_html`, `output`, `reports` |
| `worker` | app image `worker` | none | Needs MinIO healthy; scales freely |
| `keycloak` + `keycloak-db-init` | profile `sso` | 8080 | Imports `deploy/keycloak/realm-ansibleai.json` |

API/worker: `read_only: true`, `cap_drop: ALL`, `no-new-privileges`, tmpfs `/tmp` and `/home/app`. Extra host `host.docker.internal:host-gateway` for Ollama.

**Observability overlay:** Prometheus `:9090`, Grafana `:3001`. Langfuse is a **separate** project (`deploy/observability/docker-compose.langfuse.yml`) with its own Postgres, Redis, ClickHouse, MinIO; UI `:3000`, Langfuse MinIO published at `9092`.

`.dockerignore` excludes `.env*`, tests, docs, scrape caches, and `static/dist` (rebuilt in-image). `data/parsed` is **not** excluded so a local parsed KB can be copied into the image.

---

## Kubernetes / Helm

Chart: [`deploy/helm/ansibleai`](deploy/helm/ansibleai/README.md) (`Chart.yaml` version `0.1.0`). Packages API, worker, migrate Job, pgvector, Redis, MinIO, host-Ollama Service+Endpoints, optional reindex CronJob, NetworkPolicy, PDB, optional HPA/KEDA (off by default).

Live cluster install was still **pending** in project docs at the time of this inventory; the chart and tests (`tests/test_helm_chart.py`) are in git.

```bash
export KUBECONFIG=deploy/ansible/artifacts/kubeconfig

helm upgrade --install ansibleai deploy/helm/ansibleai \
  -n ansibleai --create-namespace \
  -f deploy/helm/ansibleai/values-staging.yaml \
  --timeout 15m --wait

helm test ansibleai -n ansibleai
kubectl create job --from=cronjob/ansibleai-reindex ansibleai-reindex-now -n ansibleai
```

Do not apply `values-prod.yaml` without TLS and `secrets.existingSecret`. Lab HTTP has no TLS, so `app.env` stays `development` — `APP_ENV=staging|production` forces secure cookies and breaks NodePort HTTP.

**Not installed by this chart:** ArgoCD, Vault/ESO, kube-prometheus-stack, vLLM/GPU Operator, oauth2-proxy on the member Ingress.

Templates include Deployments (api/worker), StatefulSets (postgres/redis/minio), Ingress (sticky cookie `ansibleai-upstream`), ConfigMap, Secret, ServiceAccount (not `default`), PDB, NetworkPolicy, PrometheusRule (optional), reindex CronJob, MinIO bucket Job, Ollama Endpoints.

---

## Cluster bootstrap (Ansible)

[`deploy/ansible`](deploy/ansible/README.md) provisions a **two-node kubeadm** cluster. It does **not** deploy AnsibleAI.

Documented lab IPs: laptop `.14` (Ollama), ansible-control `.19`, master `.18`, worker `.12`. CRI containerd, CNI Calico, ingress-nginx NodePort **30080/30443**, cert-manager.

Playbooks: `ping.yml`, `site.yml`, `verify.yml`, `reset.yml`.

---

## Observability

Phase **6a** is implemented. Full runbook: [deploy/observability/README.md](deploy/observability/README.md).

**Prometheus metrics** (`backend/observability/metrics.py`): HTTP RED, generation start/complete/duration, gate results, repair iterations, LLM calls/tokens.

**Langfuse:** one trace per chat turn (`generate-playbook`), nested retrieve / generate / gate. Truncated I/O — not full playbook YAML. Operator UI only; the member Account panel does not link to it.

**Grafana:** dashboard `ansibleai-overview` at http://localhost:3001/d/ansibleai-overview/ansibleai-overview

```bash
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.observability.yml up -d

docker compose --env-file .env.docker \
  -f deploy/observability/docker-compose.langfuse.yml up -d
```

---

## CI/CD

There is **no** GitHub Actions (or other) pipeline in this repository. `.dockerignore` mentions `.github` but that directory is not present. Phase **7** (CI eval gate vs golden baselines, ArgoCD GitOps) is pending.

What exists locally instead:

- `pre-commit`: trailing whitespace, YAML/JSON, private-key detect, Ruff, mypy (scoped), gitleaks, frontend ESLint + tsc
- Container role `smoke` → `scripts/smoke_auth.py`
- `helm test` connection test in the chart
- Pytest suite for unit/integration and chart/playbook lint

---

## Main workflows

### Playbook generation

```text
User → SPA POST /api/chat
     → persist user message, cancel.begin(thread)
     → Celery run_generation
     → 202 { job_id, thread, user_message }
Worker → budget check → LangGraph (reason/tools/draft/gate/repair)
       → Socket.IO progress → persist assistant message + Generation row
       → optional MinIO archive → generation_complete
SPA → refetch thread (or poll /api/chat/status)
```

### Local password login

```text
SPA → GET /api/auth/config, GET /api/auth/me
    → POST /api/auth/login { email, password }
    → argon2id verify, lockout, session rotate, CSRF cookie
    → workspace (or ForcePasswordChange)
```

### Hybrid SSO login

```text
SPA login form (no Keycloak page)
API → Keycloak token endpoint (ROPC) → upsert User
    → session cookie → optional must_change_password
```

### KB re-scrape (admin, local mode)

```text
Docs pane → POST /docs/check-updates
         → thread hashes local vs remote HTML
         → POST /docs/rescrape { modules }
         → backup data/kb_versions → parse → manifest
         → SSE /docs/stream/<id>
```

---

## Security

Implemented in code/config (not merely “framework could”):

| Control | Implementation |
|---------|----------------|
| Password hashing | argon2id; dummy hash on unknown emails |
| Sessions | Server-side; epoch revoke; `session_protection=strong`; rotation on login |
| CSRF | Double-submit cookie + header; Bearer-only skip |
| Default-deny | `before_request` allow-list |
| Admin mutations | `ADMIN_ENDPOINTS` + `role=admin` |
| Thread isolation | All chat queries filter `user_id` |
| Rate limits | Login/register (Redis in Compose); lockout |
| Headers | Talisman CSP, frame deny, referrer, HSTS outside development |
| CORS | Socket.IO origin list |
| Body size | `MAX_CONTENT_LENGTH` 2 MiB |
| Celery | JSON serializers only (no pickle) |
| Secrets | `.env` gitignored; gitleaks hook; Helm Secret ≠ ConfigMap |
| SQL | SQLAlchemy bound parameters |
| XSS | React escaping; CSP `script-src 'self'` |
| Container | non-root 10001, drop ALL caps, read-only root, no-new-privileges |
| NetworkPolicy | default-deny in Helm when enabled |
| `/metrics` | Public by design — restrict at the network layer |

Not claimed: `RATE_LIMIT_CHAT` enforcement; idle session timeout despite `SESSION_IDLE_TIMEOUT_MINUTES`; in-app user admin API; Vault/ESO (Phase 8).

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| Blank page at `:5000` | Rebuild UI (`npm run build`) or use the Compose image build |
| Config errors at import | Copy `.env` / `.env.docker` from the `*.example` files; set `DATABASE_URL` and `SECRET_KEY` |
| Compose interpolation empty | Always pass `--env-file .env.docker` |
| `MINIO_ROOT_PASSWORD` error | Set it in `.env.docker` (min 8 characters) |
| RAG status: 0 chunks | `python backend/rag/indexer.py --reset` (or `docker compose exec api …`) |
| `/readyz` 503 knowledge_base | Parsed KB missing/empty under `data/parsed` |
| `syntax-check[unknown-module]` for `amazon.aws.*` | Rebuild image so Galaxy collections are installed |
| Chat stuck / “Reconnecting…” | `CORS_ORIGINS` must include the origin you opened; check `worker` logs and Redis |
| Agent / Ollama timeouts | Pull models; raise `AGENT_REQUEST_TIMEOUT`; worker warm-up; `OLLAMA_KEEP_ALIVE` |
| Secure cookie / HSTS on HTTP | Keep `APP_ENV=development` for Compose and lab NodePort |
| Host eval hitting MySQL | Use a Postgres `DATABASE_URL` — MySQL was removed in Phase 3 |
| ansible-lint on Windows host | Use Compose/WSL; native Windows Python cannot import ansible-lint |
| Langfuse no traces | `LANGFUSE_ENABLED=true` plus keys; from containers use `host.docker.internal:3000` |
| SSO login `idp_unavailable` | Keycloak profile up; `OIDC_INTERNAL_BASE_URL` vs `OIDC_ISSUER` hostname split |
| Registration 403 | `REGISTRATION_MODE`, domain allow-list, or hybrid/oidc invite-only |

---

## Known limitations

| Status | Item |
|--------|------|
| **Implemented** | Auth, async chat, LangGraph gate, hybrid RAG, Compose stack, metrics/Langfuse opt-in, Helm chart in git, Keycloak ROPC, kubeadm playbooks |
| **Partially implemented** | Helm live install (chart present; production overlay not for casual use). KB scrape on API threads (not Celery). Stats are global `generations`, not per-user. Token budget fail-open if Redis is down |
| **Configuration-dependent** | OIDC, Langfuse, S3 artifacts, ansible-lint backend, registration policy, daily token cap |
| **Declared but unused** | `RATE_LIMIT_CHAT` (not on `/api/chat`). `SESSION_IDLE_TIMEOUT_MINUTES` (not enforced). `FLASK_ENV` (use `APP_ENV`). Audit constants `USER_ROLE_CHANGED` / `USER_ACTIVATED` / `USER_DEACTIVATED` with no admin user API |
| **Planned / TODO (roadmap)** | Phase 6b LLMOps loop; Phase 7 CI eval gate + ArgoCD; Phase 8 Vault/ESO, hardening, DR; vLLM/GPU Operator; `__Host-` cookie prefix (blocked on HTTP) |
| **Not implemented** | GitHub Actions, LICENSE file, payment/email/upload products, frontend env vars, in-app invite/user-admin UI, oauth2-proxy on laptop |

`tests/e2e/README.md` still mentions Chroma in one sentence; the vector store is pgvector.

Dockerfile comments still mention a historical “inline POST /api/chat” generation path; current Compose runs generation in the **worker**.

---

## Development guidelines

Evidence in the repo (not invented process):

- **Python:** Ruff line length 100, py311 target; mypy gradual (strict on `config`, `logging_setup`, `auth.*`).
- **Commits:** pre-commit hooks (Ruff format/fix, mypy scoped to auth/config, ESLint, tsc, gitleaks). Do not commit `.env` or `.env.docker`.
- **Layout:** application Python only under `backend/`; keep `PYTHONPATH=backend`. See [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md).
- **Config:** add tunables to `Settings` so misconfig fails at import.
- **Auth:** new Flask routes are denied until listed in `PUBLIC_ENDPOINTS` (coverage test guards this).
- **Branching / PR templates:** none in the repository.

Roadmap (from existing docs):

| Phase | Status | Summary |
|-------|--------|---------|
| 0 | Done | Auth, security, config, Alembic |
| 1 | Done | Multi-stage Dockerfile, Compose |
| 2 | Done | Celery, Redis cancel/logs, MinIO, 202 chat |
| 3 | Done | Postgres + pgvector, TEI-ready embeddings |
| 4 | 4a done; 4b chart in git | kubeadm lab; Helm install still pending in project notes |
| 5 / 5b | Done | Keycloak in-app login |
| 6a | Done | Metrics + Langfuse + Grafana |
| 6b–8 | Pending | LLMOps loop, CI/GitOps, secrets/DR |

---

## Further documentation

| Path | Contents |
|------|----------|
| [docs/README.md](docs/README.md) | Index of reports and internship materials |
| [docs/general_introduction.md](docs/general_introduction.md) | Problem, objectives, scope |
| [docs/production_progress_report.md](docs/production_progress_report.md) | Living phase report |
| [docs/rapport_refactor_langgraph_agent.md](docs/rapport_refactor_langgraph_agent.md) | Agent refactor |
| [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md) | Import and path conventions |
| [deploy/helm/ansibleai/README.md](deploy/helm/ansibleai/README.md) | Helm install, SLO, rollback |
| [deploy/ansible/README.md](deploy/ansible/README.md) | kubeadm bootstrap |
| [deploy/keycloak/README.md](deploy/keycloak/README.md) | SSO modes and Compose profile |
| [deploy/observability/README.md](deploy/observability/README.md) | Metrics and Langfuse |
| [tests/e2e/README.md](tests/e2e/README.md) | Golden dataset and scoring |
| [specs/phase5_keycloak_sso_design.md](specs/phase5_keycloak_sso_design.md) | Phase 5 design |
| [specs/phase5b_embedded_login_design.md](specs/phase5b_embedded_login_design.md) | Embedded login design |

---

## License

No `LICENSE` file is present in the repository. This is an academic / PFE project; treat the source as unpublished unless the authors state otherwise.
