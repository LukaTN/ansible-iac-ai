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
            "ts"         : self.created_at.isoformat(),
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
            "triggered_at": self.triggered_at.isoformat(),
            "status": self.status,
            "triggered_by": self.triggered_by,
            "kb_version": self.kb_version,
            "modules_updated": self.modules_updated or [],
            "modules_failed": self.modules_failed or [],
            "summary": self.summary or {},
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
            "scraped_at": self.scraped_at.isoformat(),
            "param_count": self.param_count,
            "example_count": self.example_count,
            "required_count": self.required_count,
            "health_score": self.health_score,
            "content_hash": self.content_hash,
            "diff_summary": self.diff_summary,
        }