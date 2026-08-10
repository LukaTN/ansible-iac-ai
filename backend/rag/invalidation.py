"""
=============================================================
  AnsibleAI — Cache invalidation via Redis pub/sub

  After a re-index (or rescrape + reindex), one pod publishes an
  invalidation event. Every API and worker process subscribed to the
  channel clears its in-memory caches (vectorstore proxy, KB dict,
  BM25 index, collection allow-list).

  Usage:
    publish_invalidation()       — call after reindex
    start_invalidation_listener()— call on app/worker startup

  The listener runs in a daemon thread so it never blocks the main
  loop. A missed message (e.g. process was restarting) is harmless:
  the caches are lazy and rebuild on next use.
=============================================================
"""

from __future__ import annotations

import threading

import structlog

from config import settings

log = structlog.get_logger(__name__)

CHANNEL = "ansibleai:cache_invalidation"


def publish_invalidation() -> bool:
    """
    Notify all pods that caches should be cleared.
    Returns True on success, False if Redis is unreachable.
    """
    import redis

    url = settings.redis_url
    if not url:
        log.warning("invalidation.no_redis_url")
        return False

    try:
        r = redis.from_url(url)
        subscribers = r.publish(CHANNEL, "invalidate")
        log.info("invalidation.published", subscribers=subscribers)
        return True
    except Exception as exc:
        log.warning("invalidation.publish_failed", error=str(exc))
        return False


def _on_invalidation_message(message) -> None:
    """Handle an invalidation event by clearing local caches."""
    from agent.collections import reload_collection_allowlist
    from agent.tools import invalidate_caches
    from rag.sparse_index import reset_cache as reset_sparse_cache

    log.info("invalidation.received", channel=CHANNEL)
    invalidate_caches()
    reset_sparse_cache()
    reload_collection_allowlist()


def start_invalidation_listener() -> threading.Thread | None:
    """
    Subscribe to the invalidation channel in a background thread.
    Returns the thread (for testing), or None if Redis is unavailable.
    """
    import redis

    url = settings.redis_url
    if not url:
        return None

    def _listen() -> None:
        try:
            r = redis.from_url(url)
            pubsub = r.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(CHANNEL)
            log.info("invalidation.listener_started", channel=CHANNEL)
            for message in pubsub.listen():
                if message and message.get("type") == "message":
                    _on_invalidation_message(message)
        except Exception as exc:
            log.warning("invalidation.listener_died", error=str(exc))

    t = threading.Thread(target=_listen, name="cache-invalidation", daemon=True)
    t.start()
    return t


__all__ = ["publish_invalidation", "start_invalidation_listener"]
