"""
=============================================================
  AI-Powered IaC — Database Models
  ORM : Flask-SQLAlchemy
  DB  : MySQL via PyMySQL
=============================================================
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


def _iso_utc(dt: datetime | None) -> str | None:
    """
    Serialize a naive UTC datetime as an ISO 8601 string with the explicit
    'Z' suffix so JavaScript's `new Date(...)` parses it as UTC instead of
    treating it as local time. We store everything via `datetime.utcnow()`
    so the values are guaranteed to be in UTC despite being naive.
    """
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds") + "Z"


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
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

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
    triggered_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
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
    title      = db.Column(db.String(255), nullable=False, default="New chat")
    created_at = db.Column(db.DateTime,    default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
    created_at = db.Column(db.DateTime,    default=datetime.utcnow, nullable=False)

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
    scraped_at       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
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