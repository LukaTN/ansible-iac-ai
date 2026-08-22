# AnsibleAI — repository layout

```
ansible-iac-ai/
├── README.md
├── pyproject.toml             # ruff / mypy / pytest (pythonpath = backend)
├── requirements.txt
├── requirements-dev.txt
├── alembic.ini                # script_location = backend/migrations
├── package.json               # thin wrapper → frontend npm scripts
├── Dockerfile
├── docker-compose.yml
├── docker-compose.observability.yml
├── .env.example / .env.docker.example
│
├── backend/                   # ALL Python application code
│   ├── app.py                 # Flask API entrypoint
│   ├── config.py              # pydantic-settings (fail-fast)
│   ├── models.py              # SQLAlchemy models
│   ├── celery_app.py          # Celery broker config
│   ├── tasks.py               # ansibleai.generation.run
│   ├── realtime.py            # Socket.IO emits (API + worker)
│   ├── logstream.py           # SSE scrape logs (Redis streams)
│   ├── storage.py             # Playbook artifacts (local / MinIO)
│   ├── logging_setup.py       # structlog
│   ├── gunicorn.conf.py
│   ├── agent/                 # LangGraph agent
│   ├── auth/                  # Login, CSRF, sessions, audit
│   ├── rag/                   # Indexer, retriever, generator, evaluator
│   ├── pipeline/              # KB scrape / parse / validate
│   ├── observability/         # Prometheus metrics + tracing
│   └── migrations/            # Alembic revisions
│
├── frontend/                  # React 19 + Vite + TypeScript SPA
├── static/dist/               # Vite build output (served by Flask)
├── scripts/                   # seed_admin, smoke_auth, eval runners
├── tests/                     # pytest (+ tests/e2e golden dataset)
├── docker/                    # entrypoint.sh, ansible-collections.yml
├── deploy/observability/      # prometheus, Grafana, Langfuse compose
├── deploy/ansible/            # kubeadm lab bootstrap (Phase 4a complete)
├── deploy/helm/ansibleai/     # Phase 4b application chart
├── deploy/gitops/             # Phase 7 Argo CD Applications
├── .github/workflows/         # Phase 7 ci / image / eval-gate
├── docs/                      # Reports, presentations, internship materials
├── data/                      # Local KB / scrape artifacts (gitignored)
├── output/                    # Local playbook scratch (gitignored)
└── reports/                   # Pipeline / e2e reports (gitignored)
```

## How imports resolve

Application packages stay short (`from config import settings`, `from agent…`).
Put `backend/` on `PYTHONPATH` (pytest and Docker already do this).

| Context | Resolution |
|---------|------------|
| pytest | `pythonpath = ["backend"]` in `pyproject.toml` |
| Docker | `PYTHONPATH=/app/backend`, cwd `/app` (repo root) |
| Local scripts | each script inserts `…/backend` on `sys.path` |
| Local API | `PYTHONPATH=backend python backend/app.py` |

Runtime paths like `data/`, `output/`, and `.env` are relative to the **repository root**.

## What not to put at the repo root

- Application Python → `backend/`
- Presentation decks → `docs/presentations/`
- Internship documents → `docs/internship/`
- Generated playbooks → `output/` (or MinIO)
- Eval / scrape reports → `reports/`
- Secrets → `.env` / `.env.docker` (never commit; use the `*.example` templates)
