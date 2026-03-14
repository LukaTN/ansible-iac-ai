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
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        """Serialize to JSON-safe dict for API responses."""
        return {
            "id"        : self.id,
            "request"   : self.request,
            "module"    : self.module,
            "file"      : self.filename,
            "playbook"  : self.playbook,
            "valid"     : self.is_valid,
            "warnings"  : self.warnings,
            "errors"    : self.errors,
            "ts"        : self.created_at.isoformat(),
        }
