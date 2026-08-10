# AnsibleAI — repository layout

```
ansible-iac-ai/
├── app.py                 # Flask API entrypoint
├── config.py              # pydantic-settings (fail-fast)
├── models.py              # SQLAlchemy models
├── celery_app.py          # Celery broker config
├── tasks.py               # ansibleai.generation.run
├── realtime.py            # Socket.IO emits (API + worker)
├── logstream.py           # SSE scrape logs (Redis streams)
├── storage.py             # Playbook artifacts (local / MinIO)
├── logging_setup.py       # structlog
├── gunicorn.conf.py
├── Dockerfile
├── docker-compose.yml
├── docker/entrypoint.sh   # roles: api | worker | migrate | smoke | exec
├── docker-compose.observability.yml  # Phase 6a Prometheus + Grafana
├── deploy/observability/  # prometheus.yml, Grafana datasource, Langfuse compose
├── alembic.ini
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml         # ruff / mypy
├── package.json           # thin wrapper → frontend npm scripts
│
├── agent/                 # LangGraph agent (reason → tools → draft → gate → repair)
├── auth/                  # Login, CSRF, sessions, audit
├── frontend/              # React 19 + Vite + TypeScript SPA
├── pipeline/              # KB scrape / parse / validate
├── rag/                   # Indexer, retriever, generator, evaluator
├── migrations/            # Alembic revisions
├── scripts/               # seed_admin, smoke_auth, eval runners
├── tests/                 # pytest (+ tests/e2e golden dataset)
├── static/dist/           # Vite build output (served by Flask)
├── data/
│   ├── parsed/            # Structured module JSON (source of truth for KB)
│   └── raw_html/          # Scraped docs HTML (vectors live in Postgres/pgvector)
├── docs/                  # Reports, presentations, internship materials
├── output/                # Local playbook scratch (gitignored; MinIO in compose)
└── reports/               # Pipeline / e2e reports (gitignored)
```

## What not to put at the repo root

- Presentation decks → `docs/presentations/`
- Internship documents → `docs/internship/`
- Generated playbooks → `output/` (or MinIO)
- Eval / scrape reports → `reports/`
- Secrets → `.env` / `.env.docker` (never commit; use the `*.example` templates)
