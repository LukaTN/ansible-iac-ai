"""
Phase 2 contract: POST /api/chat accepts a turn, it does not answer one.

These tests pin the parts that are easy to regress by accident — the 202
status, the absence of an assistant message in the response, the run
marker being set *before* the enqueue, and every task exit path leaving
the thread settled rather than stuck "thinking".
"""

from __future__ import annotations

import pytest

from tests.fixtures_app import STRONG_PASSWORD


@pytest.fixture(autouse=True)
def _fresh_cancel_backend():
    """
    Isolate the cancellation registry between tests.

    It is a process-wide singleton, so a thread left marked running by one
    test would make the next one look like a duplicate send.
    """
    from agent import cancel

    cancel.reset_backend(cancel.MemoryCancelBackend())
    yield
    cancel.reset_backend(None)


@pytest.fixture
def enqueued(monkeypatch):
    """Capture what the route hands to Celery instead of running it."""
    import tasks

    calls: list[dict] = []

    class _Result:
        id = "test-job-id"

    def _fake_delay(**kwargs):
        calls.append(kwargs)
        return _Result()

    monkeypatch.setattr(tasks.run_generation, "delay", _fake_delay)
    return calls


@pytest.fixture
def user_client(app, make_user):
    make_user("chat@example.com", STRONG_PASSWORD)
    c = app.test_client()
    resp = c.post(
        "/api/auth/login",
        json={"email": "chat@example.com", "password": STRONG_PASSWORD},
    )
    assert resp.status_code == 200, resp.get_json()
    return c


# ─────────────────────────────────────────────
#  Route contract
# ─────────────────────────────────────────────

def test_chat_returns_202_without_an_answer(user_client, enqueued):
    resp = user_client.post("/api/chat", json={"message": "install nginx"})

    assert resp.status_code == 202
    body = resp.get_json()
    assert body["job_id"] == "test-job-id"
    assert body["user_message"]["content"] == "install nginx"
    # The whole point: the reply does not exist yet and must not be faked.
    assert "assistant_message" not in body


def test_chat_enqueues_the_turn_it_persisted(app, user_client, enqueued):
    from models import ChatThread, db

    resp = user_client.post("/api/chat", json={"message": "install nginx"})
    thread_id = resp.get_json()["thread"]["id"]
    with app.app_context():
        owner_id = db.session.get(ChatThread, thread_id).user_id

    # Keyword arguments, so the worker cannot silently transpose them.
    assert enqueued == [
        {"thread_id": thread_id, "user_id": owner_id, "message": "install nginx"}
    ]


def test_run_marker_is_set_before_the_worker_picks_up(user_client, enqueued):
    """
    A Stop pressed while the job is still queued has to be recorded.

    That only works if the marker exists at enqueue time, not at task
    start, so this asserts the ordering rather than the end state.
    """
    from agent import cancel

    resp = user_client.post("/api/chat", json={"message": "install nginx"})
    thread_id = resp.get_json()["thread"]["id"]

    assert cancel.is_running(thread_id) is True
    assert cancel.request_cancel(thread_id) is True
    assert cancel.is_cancelled(thread_id) is True


def test_second_send_while_generating_is_rejected(user_client, enqueued):
    first = user_client.post("/api/chat", json={"message": "install nginx"})
    thread_id = first.get_json()["thread"]["id"]

    second = user_client.post(
        "/api/chat", json={"thread_id": thread_id, "message": "and postgres"}
    )

    assert second.status_code == 409
    assert second.get_json()["code"] == "already_running"
    # The rejected message must not have been queued or persisted.
    assert len(enqueued) == 1


def test_broker_failure_clears_the_marker_and_reports_503(user_client, monkeypatch):
    import tasks
    from agent import cancel

    def _boom(**_kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(tasks.run_generation, "delay", _boom)

    resp = user_client.post("/api/chat", json={"message": "install nginx"})

    assert resp.status_code == 503
    assert resp.get_json()["code"] == "enqueue_failed"
    # Leaving the marker set would lock the thread out of every later send.
    assert cancel.is_running(resp.get_json()["thread_id"]) is False


def test_empty_message_is_rejected_before_anything_is_queued(user_client, enqueued):
    resp = user_client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400
    assert enqueued == []


# ─────────────────────────────────────────────
#  Status endpoint (the client's polling fallback)
# ─────────────────────────────────────────────

def test_status_reports_running_then_settled(user_client, enqueued):
    from agent import cancel

    resp = user_client.post("/api/chat", json={"message": "install nginx"})
    thread_id = resp.get_json()["thread"]["id"]

    running = user_client.get(f"/api/chat/status/{thread_id}").get_json()
    assert running == {"thread_id": thread_id, "running": True, "cancelling": False}

    cancel.end(thread_id)

    settled = user_client.get(f"/api/chat/status/{thread_id}").get_json()
    assert settled["running"] is False


def test_status_of_someone_elses_thread_is_a_404(app, user_client, make_user, enqueued):
    """404, not 403: a 403 would confirm the thread id exists."""
    thread_id = user_client.post(
        "/api/chat", json={"message": "install nginx"}
    ).get_json()["thread"]["id"]

    make_user("other@example.com", STRONG_PASSWORD)
    other = app.test_client()
    other.post(
        "/api/auth/login",
        json={"email": "other@example.com", "password": STRONG_PASSWORD},
    )

    assert other.get(f"/api/chat/status/{thread_id}").status_code == 404


# ─────────────────────────────────────────────
#  The task itself
# ─────────────────────────────────────────────

def _thread_with_message(app, user_id: int, text: str = "install nginx") -> int:
    from models import ChatMessage, ChatThread, db

    with app.app_context():
        thread = ChatThread(user_id=user_id, title="New chat")
        db.session.add(thread)
        db.session.flush()
        db.session.add(ChatMessage(thread_id=thread.id, role="user", content=text))
        db.session.commit()
        return thread.id


def _assistant_messages(app, thread_id: int) -> list:
    from models import ChatMessage

    with app.app_context():
        return (
            ChatMessage.query.filter_by(thread_id=thread_id, role="assistant")
            .order_by(ChatMessage.id.asc())
            .all()
        )


def test_task_persists_the_answer_and_titles_the_thread(app, make_user, monkeypatch):
    import agent
    from agent.orchestrator import AgentResponse
    from models import ChatThread, db
    from tasks import run_generation

    user_id = make_user("worker@example.com")
    thread_id = _thread_with_message(app, user_id, "install nginx on debian")

    monkeypatch.setattr(
        agent,
        "handle_message",
        lambda **_kwargs: AgentResponse(text="Here you go.", intent="chat"),
    )

    result = run_generation(thread_id=thread_id, user_id=user_id, message="install nginx on debian")

    assert result["status"] == "ok"
    messages = _assistant_messages(app, thread_id)
    assert [m.content for m in messages] == ["Here you go."]
    with app.app_context():
        assert db.session.get(ChatThread, thread_id).title != "New chat"


def test_cancelled_turn_still_writes_a_reply(app, make_user, monkeypatch):
    """
    A cancelled turn must not leave the thread ending on a user message.

    The UI reads "last message is from the user" as "still generating", so
    silence here would look identical to a hung job.
    """
    import agent
    from agent.cancel import GenerationCancelled
    from tasks import CANCELLED_TEXT, run_generation

    user_id = make_user("cancel@example.com")
    thread_id = _thread_with_message(app, user_id)

    def _cancelled(**_kwargs):
        raise GenerationCancelled(thread_id)

    monkeypatch.setattr(agent, "handle_message", _cancelled)

    result = run_generation(thread_id=thread_id, user_id=user_id, message="install nginx")

    assert result["status"] == "cancelled"
    assert [m.content for m in _assistant_messages(app, thread_id)] == [CANCELLED_TEXT]


def test_failed_turn_reports_without_leaking_internals(app, make_user, monkeypatch):
    import agent
    from tasks import FAILED_TEXT, run_generation

    user_id = make_user("fail@example.com")
    thread_id = _thread_with_message(app, user_id)

    def _explode(**_kwargs):
        raise RuntimeError("mysql://root:hunter2@db:3306/ansibleai is unreachable")

    monkeypatch.setattr(agent, "handle_message", _explode)

    result = run_generation(thread_id=thread_id, user_id=user_id, message="install nginx")

    assert result["status"] == "failed"
    saved = _assistant_messages(app, thread_id)[0].content
    assert saved == FAILED_TEXT
    assert "hunter2" not in saved


def test_task_aborts_when_cancelled_while_queued(app, make_user, monkeypatch):
    """Stop pressed before a worker was free must not spend any LLM time."""
    import agent
    from agent import cancel
    from tasks import CANCELLED_TEXT, run_generation

    user_id = make_user("queued@example.com")
    thread_id = _thread_with_message(app, user_id)

    called = []
    monkeypatch.setattr(agent, "handle_message", lambda **kw: called.append(kw))

    cancel.begin(thread_id)
    cancel.request_cancel(thread_id)

    result = run_generation(thread_id=thread_id, user_id=user_id, message="install nginx")

    assert result["status"] == "cancelled"
    assert called == []
    assert [m.content for m in _assistant_messages(app, thread_id)] == [CANCELLED_TEXT]


def test_task_on_a_deleted_thread_is_a_noop(app, make_user, monkeypatch):
    import agent
    from tasks import run_generation

    called = []
    monkeypatch.setattr(agent, "handle_message", lambda **kw: called.append(kw))

    result = run_generation(thread_id=999_999, user_id=make_user("gone@example.com"), message="hi")

    assert result["status"] == "thread_missing"
    assert called == []


def test_task_always_clears_the_run_marker(app, make_user, monkeypatch):
    """Otherwise the thread is locked out of every future send."""
    import agent
    from agent import cancel
    from tasks import run_generation

    user_id = make_user("marker@example.com")
    thread_id = _thread_with_message(app, user_id)

    monkeypatch.setattr(
        agent, "handle_message", lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    cancel.begin(thread_id)

    run_generation(thread_id=thread_id, user_id=user_id, message="install nginx")

    assert cancel.is_running(thread_id) is False
