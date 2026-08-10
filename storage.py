"""
=============================================================
  AnsibleAI — playbook artifact storage

  Two different needs, previously served by the same directory:

  1. A *working file*. ansible-lint takes a path, not a string, so every
     draft/repair iteration has to materialise the YAML somewhere on
     disk. This is scratch: it lives in the temp filesystem, it is
     overwritten on each repair pass, and it is deleted once the turn
     settles.

  2. A *durable artifact*. The record of what was generated. On a single
     machine `output/` was fine. Once the agent runs in a Celery worker
     and the API has several replicas, a file on the worker's local disk
     is unreachable from the process serving the user and gone the
     moment the pod restarts — so it goes to object storage instead.

  Note that the YAML itself is also persisted on the chat message, which
  is what the UI renders. Object storage is the archive, not the read
  path, which is why an upload failure degrades to a warning rather than
  failing a generation that has already succeeded.
=============================================================
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog

from config import settings

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ArtifactRef:
    """Where a stored playbook ended up."""

    uri: str
    backend: str
    key: str

    def as_dict(self) -> dict[str, str]:
        return {"uri": self.uri, "backend": self.backend, "key": self.key}


# ─────────────────────────────────────────────
#  Working files (scratch, always local)
# ─────────────────────────────────────────────

def playbook_filename(user_request: str) -> str:
    """
    Timestamped, slugified name for a generated playbook.

    Matches the convention rag/generator.py has always used, so archives
    written before and after this change sort together.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    short_name = re.sub(r"[^a-z0-9]+", "_", user_request.lower())[:30].strip("_")
    return f"playbook_{short_name}_{timestamp}.yml"


def working_dir() -> str:
    """
    Scratch directory for files the linter needs to open.

    Under TMPDIR, which the container backs with a tmpfs mount — the root
    filesystem is read-only, so this cannot be a path inside /app.
    """
    path = os.path.join(tempfile.gettempdir(), "ansibleai", "playbooks")
    os.makedirs(path, exist_ok=True)
    return path


def write_working_file(filename: str, content: str) -> str:
    """Materialise YAML for ansible-lint and return its path."""
    path = os.path.join(working_dir(), os.path.basename(filename))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content.rstrip("\n") + "\n")
    return path


def discard_working_file(path: str | None) -> None:
    """Remove a scratch file once the turn is over. Never raises."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        # Already gone, or never written. Either way there is nothing to
        # report: tmpfs reclaims the space regardless.
        pass


# ─────────────────────────────────────────────
#  Durable artifacts
# ─────────────────────────────────────────────

class ArtifactStore(Protocol):
    def put(self, key: str, content: str) -> ArtifactRef: ...
    def get(self, key: str) -> str | None: ...


class LocalArtifactStore:
    """
    Writes under output/, as the app always has.

    Correct for a single process on one machine and nowhere else, which
    is why it is the development default and not a deployment option.
    """

    def __init__(self, root: str) -> None:
        self._root = root

    def _path(self, key: str) -> str:
        return os.path.join(self._root, key.replace("/", os.sep))

    def put(self, key: str, content: str) -> ArtifactRef:
        path = self._path(key)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return ArtifactRef(uri=f"file://{path}", backend="local", key=key)

    def get(self, key: str) -> str | None:
        path = self._path(key)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return handle.read()


class S3ArtifactStore:
    """
    Any S3-compatible endpoint; MinIO in the compose stack.

    The bucket is created on first use so a fresh environment does not
    need a manual provisioning step before the first generation.
    """

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            region_name=settings.s3_region,
            # path-style addressing: MinIO does not serve
            # bucket.host virtual-host URLs without extra DNS setup.
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self._bucket)
                log.info("artifacts.bucket_created", bucket=self._bucket)
            except ClientError:
                # A parallel worker may have won the race, or the
                # credentials may lack CreateBucket. put() will surface the
                # real problem with a useful message.
                log.warning("artifacts.bucket_unavailable", bucket=self._bucket, exc_info=True)

    def put(self, key: str, content: str) -> ArtifactRef:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="application/yaml",
        )
        return ArtifactRef(uri=f"s3://{self._bucket}/{key}", backend="s3", key=key)

    def get(self, key: str) -> str | None:
        from botocore.exceptions import ClientError

        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError:
            return None
        return response["Body"].read().decode("utf-8")


_store: ArtifactStore | None = None
_store_lock = threading.Lock()


def get_store() -> ArtifactStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                if settings.artifact_backend == "s3":
                    _store = S3ArtifactStore()
                    log.info("artifacts.backend", backend="s3", bucket=settings.s3_bucket)
                else:
                    _store = LocalArtifactStore(settings.artifact_local_dir)
                    log.info("artifacts.backend", backend="local", root=settings.artifact_local_dir)
    return _store


def reset_store(store: ArtifactStore | None = None) -> None:
    """Swap the store. Tests use this; nothing else should."""
    global _store
    with _store_lock:
        _store = store


def artifact_key(thread_id: int, filename: str) -> str:
    """
    Date-partitioned key.

    The prefix is what makes a lifecycle rule ("expire playbooks older
    than N days") expressible in Phase 8 without listing the whole bucket.
    """
    day = datetime.now(UTC).strftime("%Y/%m/%d")
    return f"playbooks/{day}/thread-{thread_id}/{os.path.basename(filename)}"


def store_playbook(thread_id: int, filename: str, content: str) -> ArtifactRef | None:
    """
    Archive a finished playbook. Returns None if archiving failed.

    Failure is not propagated: the generation already succeeded and its
    YAML is on the chat message, so losing the archive copy is a
    degradation to log, not a reason to show the user an error.
    """
    if not filename or not content:
        return None
    key = artifact_key(thread_id, filename)
    try:
        ref = get_store().put(key, content)
    except Exception:
        log.warning("artifacts.store_failed", thread_id=thread_id, key=key, exc_info=True)
        return None
    log.info("artifacts.stored", thread_id=thread_id, uri=ref.uri)
    return ref


__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "artifact_key",
    "discard_working_file",
    "get_store",
    "playbook_filename",
    "reset_store",
    "store_playbook",
    "working_dir",
    "write_working_file",
]
