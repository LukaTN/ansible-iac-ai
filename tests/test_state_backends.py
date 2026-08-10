"""
Phase 2 moved three pieces of per-request state out of process memory.

These tests cover the parts that can be checked without Redis: the
semantics the memory backend has to share with the Redis one, the key
layout the Redis backends use (a rename would silently split producers
from consumers), and the artifact store's local path.
"""

from __future__ import annotations

import os

import pytest

# ─────────────────────────────────────────────
#  Cancellation
# ─────────────────────────────────────────────

@pytest.fixture
def backend():
    from agent.cancel import MemoryCancelBackend

    return MemoryCancelBackend()


def test_begin_clears_a_stale_cancel_flag(backend):
    """
    A cancel from the previous turn must not kill the next one.

    The flag outlives its job by design — Stop can arrive while the job is
    still queued — so starting a turn is the point where it gets cleared.
    """
    backend.begin(1)
    backend.request_cancel(1)
    assert backend.is_cancelled(1) is True

    backend.begin(1)
    assert backend.is_cancelled(1) is False


def test_request_cancel_reports_whether_a_turn_was_live(backend):
    assert backend.request_cancel(2) is False
    backend.begin(2)
    assert backend.request_cancel(2) is True


def test_end_clears_both_markers(backend):
    backend.begin(3)
    backend.request_cancel(3)
    backend.end(3)
    assert backend.is_running(3) is False
    assert backend.is_cancelled(3) is False


def test_redis_cancel_keys_are_namespaced_and_distinct():
    """
    The API sets these keys and the worker reads them, so the names are a
    cross-process contract, not an implementation detail.
    """
    from agent.cancel import RedisCancelBackend

    assert RedisCancelBackend._run_key(7) == "ansibleai:gen:run:7"
    assert RedisCancelBackend._cancel_key(7) == "ansibleai:gen:cancel:7"


def test_is_cancelled_survives_an_unreachable_backend(monkeypatch):
    """
    A Redis outage must not abort generations nobody asked to stop.

    Failing open is the lesser evil here: the cost of a wrong "cancelled"
    is minutes of discarded GPU work.
    """
    from agent import cancel

    class _Broken:
        def is_cancelled(self, _thread_id):
            raise ConnectionError("redis is down")

    cancel.reset_backend(_Broken())
    try:
        assert cancel.is_cancelled(1) is False
        cancel.check(1)  # must not raise
    finally:
        cancel.reset_backend(None)


# ─────────────────────────────────────────────
#  Scrape log streaming
# ─────────────────────────────────────────────

def test_memory_log_stream_delivers_published_lines():
    from logstream import MemoryLogStream

    stream = MemoryLogStream()
    stream.create(11)
    stream.publish(11, "Checking amazon.aws.ec2_instance ...")

    reader = stream.tail(11)
    assert next(reader) == "Checking amazon.aws.ec2_instance ..."


def test_publish_timestamps_and_never_raises(monkeypatch):
    import logstream

    class _Broken:
        def create(self, _session_id):
            pass

        def publish(self, _session_id, _line):
            raise ConnectionError("redis is down")

        def tail(self, _session_id):
            return iter(())

    logstream.reset_backend(_Broken())
    try:
        # A broken log stream must not abort the scrape it describes.
        logstream.publish(12, "still going")
    finally:
        logstream.reset_backend(None)


def test_log_stream_lines_carry_a_timestamp():
    import logstream
    from logstream import MemoryLogStream

    backend = MemoryLogStream()
    logstream.reset_backend(backend)
    try:
        logstream.publish(13, "Downloading")
        line = next(backend.tail(13))
    finally:
        logstream.reset_backend(None)

    assert line.endswith("Downloading")
    assert line.startswith("[") and line[3] == ":"


def test_redis_log_stream_key_is_namespaced():
    from logstream import RedisLogStream

    assert RedisLogStream._key(21) == "ansibleai:doclog:21"


# ─────────────────────────────────────────────
#  Playbook artifacts
# ─────────────────────────────────────────────

def test_working_file_round_trips_and_is_discardable():
    """
    ansible-lint takes a path, so every draft has to hit a real file. It
    is scratch, not a deliverable, and has to be removable afterwards.
    """
    import storage

    path = storage.write_working_file("playbook_test.yml", "---\n- hosts: all")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "---\n- hosts: all\n"

    storage.discard_working_file(path)
    assert not os.path.exists(path)
    storage.discard_working_file(path)  # idempotent


def test_artifact_key_is_date_partitioned_per_thread():
    """The prefix is what makes a retention rule expressible in Phase 8."""
    import storage

    key = storage.artifact_key(42, "playbook_install_nginx_20260804.yml")
    assert key.startswith("playbooks/")
    assert "/thread-42/" in key
    assert key.endswith("playbook_install_nginx_20260804.yml")


def test_local_store_round_trip(tmp_path):
    from storage import LocalArtifactStore

    store = LocalArtifactStore(str(tmp_path))
    ref = store.put("playbooks/2026/08/04/thread-1/p.yml", "---\n")

    assert ref.backend == "local"
    assert store.get("playbooks/2026/08/04/thread-1/p.yml") == "---\n"
    assert store.get("playbooks/missing.yml") is None


def test_store_playbook_degrades_instead_of_failing_the_turn():
    """
    The YAML is already on the chat message, so a storage outage costs the
    archive copy — not the answer the user waited minutes for.
    """
    import storage

    class _Broken:
        def put(self, _key, _content):
            raise ConnectionError("minio is down")

        def get(self, _key):
            return None

    storage.reset_store(_Broken())
    try:
        assert storage.store_playbook(1, "p.yml", "---\n") is None
    finally:
        storage.reset_store(None)
