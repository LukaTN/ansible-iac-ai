"""
=============================================================
  AI-Powered IaC — Database Models
  ORM : Flask-SQLAlchemy
  DB  : PostgreSQL 16 + pgvector
=============================================================
"""

from datetime import UTC, datetime, timedelta

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow() -> datetime:
    """
    Naive UTC timestamp.

    `datetime.utcnow()` is deprecated from Python 3.12; this keeps the
    same naive-UTC storage convention the schema already relies on
    without the deprecation warning.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def iso_utc(dt: datetime | None) -> str | None:
    """
    Serialize a naive UTC datetime as an ISO 8601 string with the explicit
    'Z' suffix so JavaScript's `new Date(...)` parses it as UTC instead of
    treating it as local time. Everything is stored via `utcnow()` so the
    values are guaranteed to be in UTC despite being naive.
    """
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds") + "Z"


_iso_utc = iso_utc


# ─────────────────────────────────────────────
#  Identity
# ─────────────────────────────────────────────

ROLE_USER = "user"
ROLE_ADMIN = "admin"
VALID_ROLES = (ROLE_USER, ROLE_ADMIN)

PROVIDER_LOCAL = "local"
PROVIDER_KEYCLOAK = "keycloak"


class User(db.Model):
    """
    An application account.

    Deliberately decoupled from *how* the account authenticates. Phase 0
    uses a local argon2id password; Phase 5 links the same row to
    Keycloak by filling `provider` / `external_id`, so migrating to SSO
    needs no schema change and no re-onboarding.
    """

    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email         = db.Column(db.String(255), nullable=False, unique=True, index=True)
    display_name  = db.Column(db.String(120), nullable=False, default="")

    # Null for accounts that authenticate only through an external
    # identity provider (the Phase 5 end state).
    password_hash = db.Column(db.String(255), nullable=True)

    role          = db.Column(db.String(20), nullable=False, default=ROLE_USER)
    # False while a self-registered account awaits admin approval, or
    # after an admin suspends it. Flask-Login refuses to log in an
    # inactive user, so this doubles as the deactivation switch.
    is_active     = db.Column(db.Boolean, nullable=False, default=True)
    email_verified_at = db.Column(db.DateTime, nullable=True)

    # ── Identity provider linkage (populated in Phase 5) ──
    provider      = db.Column(db.String(40), nullable=False, default=PROVIDER_LOCAL)
    external_id   = db.Column(db.String(255), nullable=True, index=True)

    # ── Brute-force state ──
    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until       = db.Column(db.DateTime, nullable=True)

    # ── Session invalidation ──
    # Bumped on password change or forced logout. The value is embedded in
    # the Flask-Login session identifier, so incrementing it invalidates
    # every existing session for this user regardless of session backend.
    session_epoch = db.Column(db.Integer, nullable=False, default=1)

    password_changed_at = db.Column(db.DateTime, nullable=True)
    last_login_at       = db.Column(db.DateTime, nullable=True)
    created_at          = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at          = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    threads = db.relationship(
        "ChatThread",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ── Flask-Login interface ──
    # UserMixin is intentionally not used: `get_id` embeds the session
    # epoch, which UserMixin's default implementation does not.

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return f"{self.id}:{self.session_epoch}"

    # ── Role helpers ──

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    # ── Lockout helpers ──

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > utcnow()

    def lock_for(self, minutes: int) -> None:
        self.locked_until = utcnow() + timedelta(minutes=minutes)

    def register_failed_login(self, threshold: int, lock_minutes: int) -> bool:
        """Count a failed attempt; lock the account past the threshold.

        Returns True when this attempt triggered a lock.
        """
        self.failed_login_count = (self.failed_login_count or 0) + 1
        if self.failed_login_count >= threshold:
            self.lock_for(lock_minutes)
            self.failed_login_count = 0
            return True
        return False

    def register_successful_login(self) -> None:
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = utcnow()

    def invalidate_sessions(self) -> None:
        """Revoke every existing session for this user."""
        self.session_epoch = (self.session_epoch or 1) + 1

    def to_dict(self) -> dict:
        """Public representation. Never includes the password hash."""
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name or self.email.split("@")[0],
            "role": self.role,
            "is_active": self.is_active,
            "provider": self.provider,
            "has_password": bool(self.password_hash),
            "can_change_password": bool(self.password_hash)
            or self.provider == PROVIDER_KEYCLOAK,
            "created_at": _iso_utc(self.created_at),
            "last_login_at": _iso_utc(self.last_login_at),
        }

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email} role={self.role}>"


class AuditEvent(db.Model):
    """
    Security-relevant activity: authentication outcomes, account changes,
    and destructive admin actions.

    `actor_email` is denormalized on purpose so the trail survives the
    deletion of the user it refers to.
    """

    __tablename__ = "audit_events"

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id     = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_email = db.Column(db.String(255), nullable=True)
    event       = db.Column(db.String(60), nullable=False, index=True)
    outcome     = db.Column(db.String(20), nullable=False, default="success")
    ip          = db.Column(db.String(45), nullable=True)   # fits IPv6
    user_agent  = db.Column(db.String(255), nullable=True)
    request_id  = db.Column(db.String(64), nullable=True)
    detail      = db.Column(db.JSON, nullable=True)
    created_at  = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "actor_email": self.actor_email,
            "event": self.event,
            "outcome": self.outcome,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "request_id": self.request_id,
            "detail": self.detail or {},
            "ts": _iso_utc(self.created_at),
        }


class Generation(db.Model):
    """
    Stores every playbook generation attempt.
    Equivalent of a Spring Boot @Entity class.
    """
    __tablename__ = "generations"

    id         = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    request    = db.Column(db.Text,        nullable=False)
    module     = db.Column(db.String(120), nullable=False)
    filename   = db.Column(db.String(255), nullable=True)
    playbook   = db.Column(db.Text,        nullable=True)
    is_valid   = db.Column(db.Boolean,     default=False)
    warnings   = db.Column(db.Integer,     default=0)
    errors     = db.Column(db.Integer,     default=0)
    module_ref = db.Column(db.JSON,        nullable=True)
    created_at = db.Column(db.DateTime,    default=utcnow)

    def to_dict(self):
        """Serialize to JSON-safe dict for API responses."""
        return {
            "id"         : self.id,
            "request"    : self.request,
            "module"     : self.module,
            "file"       : self.filename,
            "playbook"   : self.playbook,
            "valid"      : self.is_valid,
            "warnings"   : self.warnings,
            "errors"     : self.errors,
            "module_ref" : self.module_ref,
            "ts"         : _iso_utc(self.created_at),
        }


class ScrapeSession(db.Model):
    """
    Tracks documentation scrape/re-scrape executions.
    """
    __tablename__ = "scrape_sessions"

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    triggered_at    = db.Column(db.DateTime, default=utcnow, nullable=False)
    status          = db.Column(db.String(40), nullable=False, default="running")  # running|success|failed|partial
    triggered_by    = db.Column(db.String(120), nullable=True, default="ui")
    kb_version      = db.Column(db.String(255), nullable=True)  # backup filename created before this session
    modules_updated = db.Column(db.JSON, nullable=True)         # [slug,...]
    modules_failed  = db.Column(db.JSON, nullable=True)         # [{slug, error}, ...]
    summary         = db.Column(db.JSON, nullable=True)         # check/rescrape summary, diffs, counts, etc.

    def to_dict(self):
        return {
            "id": self.id,
            "triggered_at": _iso_utc(self.triggered_at),
            "status": self.status,
            "triggered_by": self.triggered_by,
            "kb_version": self.kb_version,
            "modules_updated": self.modules_updated or [],
            "modules_failed": self.modules_failed or [],
            "summary": self.summary or {},
        }


class ChatThread(db.Model):
    """
    A chat conversation between the user and the agent.
    Title is auto-generated from the first user message.
    """
    __tablename__ = "chat_threads"

    id         = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    # Owner. Every thread query must filter on this; without it any
    # authenticated user could read any other user's conversations.
    user_id    = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title      = db.Column(db.String(255), nullable=False, default="New chat")
    created_at = db.Column(db.DateTime,    default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime,    default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", back_populates="threads")

    messages = db.relationship(
        "ChatMessage",
        backref="thread",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at.asc()",
    )

    def to_dict(self, include_messages: bool = False) -> dict:
        data = {
            "id"           : self.id,
            "title"        : self.title,
            "created_at"   : _iso_utc(self.created_at),
            "updated_at"   : _iso_utc(self.updated_at),
            "message_count": len(self.messages) if self.messages is not None else 0,
        }
        if include_messages:
            data["messages"] = [m.to_dict() for m in self.messages]
        return data


class ChatMessage(db.Model):
    """
    One message inside a chat thread.
    Role is either 'user' or 'assistant'.
    Assistant messages may carry an optional playbook, validation result,
    module reference, and RAG metadata.
    """
    __tablename__ = "chat_messages"

    id         = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    thread_id  = db.Column(db.Integer,     db.ForeignKey("chat_threads.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    role       = db.Column(db.String(20),  nullable=False)  # 'user' | 'assistant'
    content    = db.Column(db.Text,        nullable=False, default="")
    playbook   = db.Column(db.Text,        nullable=True)
    filename   = db.Column(db.String(255), nullable=True)
    module     = db.Column(db.String(120), nullable=True)
    validation = db.Column(db.JSON,        nullable=True)
    module_ref = db.Column(db.JSON,        nullable=True)
    rag_meta   = db.Column(db.JSON,        nullable=True)
    tool_trace = db.Column(db.JSON,        nullable=True)  # list of tool calls executed by the agent
    created_at = db.Column(db.DateTime,    default=utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id"        : self.id,
            "thread_id" : self.thread_id,
            "role"      : self.role,
            "content"   : self.content,
            "playbook"  : self.playbook,
            "filename"  : self.filename,
            "module"    : self.module,
            "validation": self.validation,
            "module_ref": self.module_ref,
            "rag_meta"  : self.rag_meta,
            "tool_trace": self.tool_trace,
            "ts"        : _iso_utc(self.created_at),
        }


class ModuleVersion(db.Model):
    """
    Stores per-module version snapshots metadata after scrape.
    """
    __tablename__ = "module_versions"

    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)
    scrape_session_id = db.Column(db.Integer, nullable=True, index=True)
    module_slug      = db.Column(db.String(255), nullable=False, index=True)
    scraped_at       = db.Column(db.DateTime, default=utcnow, nullable=False)
    param_count      = db.Column(db.Integer, nullable=False, default=0)
    example_count    = db.Column(db.Integer, nullable=False, default=0)
    required_count   = db.Column(db.Integer, nullable=False, default=0)
    health_score     = db.Column(db.Integer, nullable=False, default=0)
    content_hash     = db.Column(db.String(80), nullable=False)  # sha256 hex
    diff_summary     = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "scrape_session_id": self.scrape_session_id,
            "module_slug": self.module_slug,
            "scraped_at": _iso_utc(self.scraped_at),
            "param_count": self.param_count,
            "example_count": self.example_count,
            "required_count": self.required_count,
            "health_score": self.health_score,
            "content_hash": self.content_hash,
            "diff_summary": self.diff_summary,
        }
