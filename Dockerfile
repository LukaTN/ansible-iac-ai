# syntax=docker/dockerfile:1.7

# =================================================================
#  AnsibleAI — application image
#
#  One image, several roles (dispatched by docker/entrypoint.sh):
#    api      gunicorn + gevent worker  (default)
#    migrate  alembic upgrade head, then seed the bootstrap admin
#    smoke    scripts/smoke_auth.py as a post-deploy gate
#    worker   reserved for the Phase 2 Celery worker
#
#  A single image rather than the separate Dockerfile.api /
#  Dockerfile.worker the plan sketched: the API itself shells out to
#  ansible-lint today (generation runs inline in POST /api/chat), so
#  both roles need identical dependencies. One image means one build,
#  one vulnerability scan and one SBOM, and Phase 2 can add a worker
#  service pointing at the same tag with a different command.
#
#  Python is pinned to 3.12 rather than the 3.13 used for local
#  development, because gevent and the chromadb/onnxruntime stack have
#  broader manylinux wheel coverage there. Phase 7 CI runs pytest on the
#  source tree with Python 3.12 (tests/ are not in the runtime image;
#  see .dockerignore). The same requirements.txt is installed here.
# =================================================================


# ---------- Stage 1: build the SPA ----------
FROM node:20-alpine AS frontend

WORKDIR /build/frontend

# Manifest and lockfile first: `npm ci` is then cached until the
# dependencies themselves change, not on every source edit.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./

# `npm run build` is `tsc --noEmit && vite build`, so a type error fails
# the image build. vite.config.ts writes to ../static/dist, which lands
# at /build/static/dist.
RUN npm run build


# ---------- Stage 2: Python dependencies ----------
FROM python:3.12-slim AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Toolchain for any dependency without a matching wheel (gevent's C
# extensions in particular). It stays in this stage and never reaches
# the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# A venv rather than the system site-packages, so the runtime stage
# copies one self-contained directory.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r /tmp/requirements.txt

# Ansible collections for the lint gate. ansible-core ships only
# ansible.builtin, so without these every amazon.aws / azure / k8s module
# the agent generates fails syntax-check[unknown-module] and the repair
# loop burns all its iterations on an environment problem.
COPY docker/ansible-collections.yml /tmp/ansible-collections.yml
RUN /opt/venv/bin/ansible-galaxy collection install \
      -r /tmp/ansible-collections.yml \
      -p /opt/ansible/collections


# ---------- Stage 3: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Celery's prefork children do not reliably keep the image WORKDIR on
    # sys.path (the empty '' entry tracks cwd, which billiard can change).
    # Without this, `from agent import ...` inside a task raises
    # ModuleNotFoundError even though the package is at /app/backend/agent.
    PYTHONPATH=/app/backend \
    APP_ROLE=api \
    PORT=5000 \
    # ansible-lint is installed here, so no WSL hop as on Windows.
    ANSIBLE_LINT_MODE=native \
    # The root filesystem is read-only in production; every path below
    # is backed by a tmpfs mount. ansible-core in particular refuses to
    # run without a writable HOME.
    HOME=/home/app \
    ANSIBLE_HOME=/tmp/ansible \
    # Collections baked into the image (see docker/ansible-collections.yml);
    # without this path ansible-lint resolves only ansible.builtin.
    ANSIBLE_COLLECTIONS_PATH=/opt/ansible/collections \
    XDG_CACHE_HOME=/tmp/cache \
    TMPDIR=/tmp

# git: ansible-lint resolves its project root and exclusion rules via
#      git, and warns on every run without it.
# tini: PID 1 that reaps the threads the app spawns for backend warm-up
#       and knowledge-base scraping.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --home-dir /home/app app

COPY --from=deps /opt/venv /opt/venv
COPY --from=deps /opt/ansible/collections /opt/ansible/collections

WORKDIR /app

# Application source (backend/ holds Python packages; scripts/, docker/,
# alembic.ini, data/parsed stay at the repo root). .dockerignore keeps
# .env, the vector index, the scrape cache and generated playbooks out;
# data/parsed is deliberately included so the image is usable on its own.
# Files stay root-owned and are read-only to the app user.
COPY --chown=root:root . .

# The freshly built SPA. static/dist is excluded from the build context
# precisely so a stale local build cannot shadow this.
COPY --from=frontend --chown=root:root /build/static/dist ./static/dist

RUN chmod +x /app/docker/entrypoint.sh \
    # Mount points for the writable volumes. Creating them here means a
    # read-only root filesystem still satisfies the os.makedirs(exist_ok=True)
    # calls in app.py even when a directory is not mounted.
 && mkdir -p /app/output /app/reports /app/data/chromadb \
             /app/data/kb_versions /app/data/raw_html \
 && chown -R app:app /app/output /app/reports /app/data

USER app
EXPOSE 5000

# Delegated to the entrypoint so the probe matches the role. The worker
# serves no HTTP; probing /healthz against it would mark a healthy
# container unhealthy forever, and any `depends_on: service_healthy`
# pointing at it would never be satisfied.
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD ["/app/docker/entrypoint.sh", "healthcheck"]

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
CMD []
