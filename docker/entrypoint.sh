#!/usr/bin/env bash
# =================================================================
#  Container entrypoint — selects a role for this process.
#
#  The role comes from the first argument, falling back to $APP_ROLE
#  and finally to "api":
#
#      docker run ansibleai/app                  -> api
#      docker run ansibleai/app worker           -> celery agent worker
#      docker run ansibleai/app migrate          -> migrations + admin seed
#      docker run ansibleai/app smoke            -> post-deploy auth gate
#      docker run ansibleai/app exec <cmd...>    -> arbitrary command
#
#  One image with several roles keeps the Kubernetes manifests in the
#  later phases trivial: the migration Job and the API Deployment differ
#  only by `args`.
# =================================================================
set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*" >&2; }

role="${1-}"
if [ -n "$role" ]; then
    shift
else
    role="${APP_ROLE:-api}"
fi

case "$role" in
api)
    # Schema safety is not skipped, just relocated: app.py refuses to
    # boot against an un-migrated database, so a mis-ordered rollout
    # fails here rather than corrupting data. Migrations themselves are
    # the `migrate` role's job, never the API's — concurrent replicas
    # running Alembic would race.
    log "starting gunicorn (workers=${GUNICORN_WORKERS:-1}, class=${GUNICORN_WORKER_CLASS:-gevent})"
    exec gunicorn --config /app/backend/gunicorn.conf.py "$@" app:app
    ;;

migrate)
    log "applying database migrations"
    alembic upgrade head

    if [ -n "${BOOTSTRAP_ADMIN_EMAIL:-}" ]; then
        # Idempotent: re-running promotes and re-activates the account and
        # resets its password, which is the documented lockout recovery.
        log "seeding bootstrap administrator"
        python scripts/seed_admin.py
    else
        log "BOOTSTRAP_ADMIN_EMAIL unset - skipping administrator seed"
    fi

    log "migrate role complete"
    ;;

worker)
    # Runs the agent. Time limits and acknowledgement policy come from
    # celery_app.py rather than the command line, so a worker started by
    # hand behaves exactly like one started by the Deployment.
    #
    # Concurrency is low by design: each task is an LLM generation loop,
    # so the ceiling is what the model server can serve, not what this
    # container can schedule.
    #
    # --without-gossip/--without-mingle drop the worker-to-worker chatter
    # that is pure overhead when workers neither share state nor need to
    # discover each other.
    log "starting celery worker (concurrency=${CELERY_WORKER_CONCURRENCY:-2})"
    # cwd + PYTHONPATH: billiard's prefork children do not inherit a
    # reliable empty-string sys.path entry for WORKDIR. See Dockerfile.
    cd /app
    export PYTHONPATH="/app/backend${PYTHONPATH:+:$PYTHONPATH}"
    exec celery -A tasks worker \
        --loglevel="${CELERY_LOG_LEVEL:-info}" \
        --concurrency="${CELERY_WORKER_CONCURRENCY:-2}" \
        --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-50}" \
        --without-gossip \
        --without-mingle \
        "$@"
    ;;

smoke)
    # Post-deploy gate. Exits non-zero on the first failed expectation.
    log "running authentication smoke test"
    exec python scripts/smoke_auth.py "$@"
    ;;

healthcheck)
    # Dispatched from the image HEALTHCHECK. Roles are not interchangeable
    # here: the worker serves no HTTP, so probing /healthz against it fails
    # forever and marks a perfectly functional container unhealthy.
    #
    # Docker runs this as a fresh process, so it cannot see anything the
    # entrypoint exported — the role has to come from the container's own
    # environment, which is why the worker service sets APP_ROLE.
    case "${APP_ROLE:-api}" in
    worker)
        # Round-trips the broker and the worker's own control queue, so
        # this fails if either the process or Redis is gone.
        exec celery -A tasks inspect ping \
            --destination "celery@$(hostname)" \
            --timeout "${CELERY_PING_TIMEOUT:-5}" >/dev/null 2>&1
        ;;
    *)
        # Liveness only: /readyz also reports the vector index, which is
        # empty until it has been built, and an unbuilt index should not
        # make the container restart forever.
        exec python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${PORT:-5000}/healthz', timeout=4).status == 200 else 1)"
        ;;
    esac
    ;;

exec)
    # Escape hatch for one-off maintenance (shell, alembic revision, the
    # RAG index build) without a second image or a mutated entrypoint.
    if [ "$#" -eq 0 ]; then
        log "ERROR: 'exec' needs a command, e.g. exec python backend/rag/pipeline.py --build"
        exit 64
    fi
    exec "$@"
    ;;

*)
    log "ERROR: unknown role '${role}'. Valid roles: api, worker, migrate, smoke, healthcheck, exec."
    exit 64
    ;;
esac
