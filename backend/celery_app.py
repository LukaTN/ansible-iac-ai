"""
=============================================================
  AnsibleAI — Celery application

  Deliberately free of Flask imports so that `celery -A tasks worker`
  boots without building the web application, and so importing this
  module from the API costs nothing but a broker handle.

  The configuration here is shaped by one fact: a single task is an LLM
  generation loop that costs minutes of GPU time. Everything that would
  normally make a queue more robust — retries, late acknowledgement,
  prefetching — makes this workload worse, because the failure mode is
  not "a message was lost" but "we paid twice".
=============================================================
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Guarantee backend packages are importable even when Celery's prefork
# pool has moved the process cwd away from /app (empty '' on sys.path
# tracks cwd, which is how ForkPoolWorker lost `agent` in production).
# cwd stays at the repository root so relative paths like data/ resolve.
_BACKEND_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
os.chdir(_REPO_ROOT)

from celery import Celery

from config import settings

celery = Celery("ansibleai")

celery.conf.update(
    broker_url=settings.broker_url,
    result_backend=settings.result_backend,

    # JSON only. Pickle would let anyone who can write to the broker
    # execute code in the worker.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    timezone="UTC",
    enable_utc=True,

    # ── Delivery semantics ───────────────────────────────────────
    # Acknowledge on delivery, not on completion. With acks_late a worker
    # killed at minute nine of a ten-minute generation would hand the job
    # to another worker and spend the whole cost again. Losing the job and
    # letting the user resend is the cheaper, more predictable outcome.
    task_acks_late=False,
    task_reject_on_worker_lost=False,

    # One reserved message per worker process. The default of four lets a
    # single worker sit on a queue of long jobs while its peers idle.
    worker_prefetch_multiplier=settings.celery_prefetch_multiplier,

    # ── Time limits ──────────────────────────────────────────────
    # Soft raises SoftTimeLimitExceeded inside the task, which is what
    # lets it persist a failure note and tell the browser. Hard kills the
    # process if that cleanup hangs too.
    task_soft_time_limit=settings.celery_soft_time_limit,
    task_time_limit=settings.celery_time_limit,

    # Lets the API distinguish "queued" from "running".
    task_track_started=True,

    # Results only ever carry a small status dict; the real payload goes
    # to the database and to the client over Socket.IO.
    result_expires=3600,

    # Development convenience: run the task inline in the caller. config.py
    # refuses this outside development.
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=False,

    # Celery 6 changes this default; setting it silences the warning and
    # keeps a worker started before Redis from exiting immediately.
    broker_connection_retry_on_startup=True,
)


__all__ = ["celery"]
