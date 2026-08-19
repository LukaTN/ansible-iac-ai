"""
=============================================================
  AnsibleAI — Centralized configuration

  Single source of truth for every environment variable the
  application reads. Validation runs at import time so a
  misconfigured deployment fails at startup rather than on the
  first request that happens to need the missing value.

  Usage:
      from config import settings
      settings.database_url

  Nothing here reads the database or network, so importing this
  module is always safe (including from Alembic and from tests).
=============================================================
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Application package root (…/backend) and repository root (parent).
# Runtime data (data/, output/, .env) lives at the repository root.
BACKEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_ROOT.parent

Environment = Literal["development", "staging", "production"]
SessionBackend = Literal["sqlalchemy", "redis", "filesystem"]
RegistrationMode = Literal["closed", "domain", "open"]
AuthMode = Literal["local", "hybrid", "oidc"]
LogFormat = Literal["json", "console"]
SocketIOAsyncMode = Literal["threading", "gevent", "eventlet"]
# Subsystems that keep per-request state. "memory" confines that state to
# one process, which is correct for local development and for tests, and
# fatal the moment a second replica exists.
StateBackend = Literal["memory", "redis"]
ArtifactBackend = Literal["local", "s3"]


class ConfigError(RuntimeError):
    """Raised when configuration is missing or internally inconsistent."""


class Settings(BaseSettings):
    """Every tunable the app reads, validated once at startup."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Runtime ──────────────────────────────────────────────────
    env: Environment = Field(default="development", alias="APP_ENV")
    debug: bool = False
    port: int = 5000

    # ── Security (required) ──────────────────────────────────────
    # Signs session cookies and CSRF tokens. There is deliberately no
    # default: a shared or predictable key lets anyone forge a session.
    secret_key: str = Field(default="", alias="SECRET_KEY")

    # ── Database (required) ──────────────────────────────────────
    database_url: str = Field(default="", alias="DATABASE_URL")
    db_pool_recycle: int = 300

    # ── Sessions ─────────────────────────────────────────────────
    # Server-side storage is what makes logout actually revoke access.
    # "sqlalchemy" reuses the app database so local dev needs no extra
    # service; production switches to "redis".
    session_backend: SessionBackend = "sqlalchemy"
    redis_url: str = "redis://localhost:6379/0"
    session_lifetime_minutes: int = 60 * 12
    session_idle_timeout_minutes: int = 60 * 2
    session_cookie_name: str = "ansibleai_session"
    # Sent only over HTTPS. Forced on outside development.
    session_cookie_secure: bool = False
    force_https: bool = False

    # ── Registration & accounts ──────────────────────────────────
    # "closed": admins create accounts. "domain": self-serve limited to
    # allowed_email_domains. "open": anyone (not advised — each request
    # costs GPU time).
    registration_mode: RegistrationMode = "domain"
    allowed_email_domains: str = ""
    # New self-registered accounts stay inactive until an admin approves.
    require_admin_approval: bool = True
    password_min_length: int = 12

    # Seeds the first admin on migration. Password is read once and
    # should be rotated after the first login.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # ── Rate limiting ────────────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    # Per-IP budget on the login endpoint, on top of per-account lockout.
    rate_limit_login: str = "10 per minute;60 per hour"
    rate_limit_register: str = "5 per hour"
    rate_limit_chat: str = "30 per hour"
    # Consecutive failures before an account is temporarily locked.
    account_lockout_threshold: int = 8
    account_lockout_minutes: int = 15

    # ── Identity (Phase 5 / 5b) ──────────────────────────────────
    # local: password only (default; tests and host `python app.py`).
    # hybrid / oidc: members type email+password on AnsibleAI; the API
    # authenticates against Keycloak (ROPC). No browser redirect.
    auth_mode: AuthMode = "local"
    oidc_issuer: str = ""
    # In-cluster origin used to fetch tokens/JWKS (e.g. http://keycloak:8080).
    # Token `iss` still has to match oidc_issuer (the browser-facing URL).
    oidc_internal_base_url: str = ""
    oidc_client_id: str = "ansibleai-web"
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:5000/api/auth/oidc/callback"
    oidc_scopes: str = "openid email profile"
    oidc_admin_group: str = "ansibleai-admins"
    oidc_admin_role: str = "ansibleai-admin"
    auth_break_glass_emails: str = ""
    # After a successful SSO link, drop the local hash except break-glass.
    oidc_retire_local_password: bool = True
    # Require Keycloak email_verified=true. Local Compose has no SMTP, so
    # leave this false there; keep true in real deployments.
    oidc_require_email_verified: bool = True
    # Advertise GET /api/auth/oidc/login (Keycloak hosted UI). Default off.
    oidc_browser_redirect: bool = False
    # Map Keycloak ansibleai-admins → users.role=admin. Default off: identity
    # admins stay in the Keycloak console and do not get AnsibleAI admin UI.
    oidc_map_app_admin: bool = False
    # Optional master-realm admin for Admin API (temp password / in-app change).
    # Prefer the confidential client's service account when it has manage-users.
    keycloak_admin: str = Field(default="", alias="KEYCLOAK_ADMIN")
    keycloak_admin_password: str = Field(default="", alias="KEYCLOAK_ADMIN_PASSWORD")
    # Daily LLM token budget per user. 0 = unlimited.
    user_daily_token_budget: int = 0

    # ── Logging ──────────────────────────────────────────────────
    log_level: str = "DEBUG"
    log_format: LogFormat = "console"

    # ── CORS (Vite dev server) ───────────────────────────────────
    # Vite (:5173) for local frontend-dev, and the container-served SPA
    # (:5000). Omitting :5000 makes Socket.IO answer 400 to every browser
    # that opened the Docker UI, which looks like a permanently stuck
    # "Understand" step because progress events never arrive.
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5000,http://127.0.0.1:5000"
    )

    # ── Socket.IO ────────────────────────────────────────────────
    # Must match how the process is served, or the server and the
    # transport disagree about which I/O primitives are safe to block on:
    #   threading  Werkzeug dev server, or gunicorn -k gthread
    #   gevent     gunicorn -k gevent (the container default)
    # Only gevent/eventlet give a native WebSocket upgrade; threading
    # falls back to HTTP long-polling, which works but is chattier.
    socketio_async_mode: SocketIOAsyncMode = "threading"

    # Redis pub/sub channel that carries emits between processes. Required
    # for anything the Celery worker emits to reach a browser, and for a
    # second API replica to serve a client whose socket lives elsewhere.
    # Empty means single-process: emits go straight to local connections.
    socketio_message_queue: str = ""

    # ── Background generation (Celery) ───────────────────────────
    # Empty broker/backend fall back to redis_url so a deployment only has
    # to set one URL; override to split them across databases or brokers.
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    # Runs the task inline in the caller instead of shipping it to a
    # worker. That keeps `python app.py` working with no broker and no
    # worker process, at the cost of holding the request open. Refused
    # outside development, where it would silently reintroduce the
    # multi-minute request that Phase 2 exists to remove.
    celery_task_always_eager: bool = True

    # SoftTimeLimit raises inside the task so it can save a failure note;
    # the hard limit kills the process if that cleanup itself hangs. Must
    # exceed the worst case of agent_request_timeout * agent_max_iterations.
    celery_soft_time_limit: int = 1500
    celery_time_limit: int = 1620
    # Long tasks: hoarding queued messages in one worker starves the others.
    celery_prefetch_multiplier: int = 1
    celery_worker_concurrency: int = 2

    # ── Cross-process state backends ─────────────────────────────
    # Cancellation flags. "memory" only reaches the process that owns the
    # dict, so a cancel served by replica A cannot stop a job on worker B.
    cancel_backend: StateBackend = "memory"
    # Upper bound on how long a cancel or run marker outlives its job, so
    # a crashed worker cannot leave a thread permanently marked running.
    cancel_ttl_seconds: int = 3600

    # SSE log tailing for knowledge-base scrapes.
    log_stream_backend: StateBackend = "memory"
    log_stream_max_entries: int = 5000
    log_stream_ttl_seconds: int = 86400

    # ── Playbook artifacts ───────────────────────────────────────
    # "local" writes to output/, which only the process that generated the
    # file can read back. "s3" targets MinIO or any S3-compatible store.
    artifact_backend: ArtifactBackend = "local"
    artifact_local_dir: str = "output"
    s3_endpoint_url: str = ""
    s3_bucket: str = "ansibleai-playbooks"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # ── Agent LLM (Ollama) ───────────────────────────────────────
    agent_model: str = "qwen2.5-coder:7b"
    agent_max_iterations: int = 4
    agent_request_timeout: int = 300

    # ── Ollama / playbook generation ─────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:14b"
    playbook_model: str = "qwen2.5-coder:14b"
    playbook_max_tokens: int = 2500
    playbook_temperature: float = 0.2

    # ── RAG ──────────────────────────────────────────────────────
    rag_parsed_dir: str = "data/parsed"
    rag_max_chunks_per_collection: int = 0
    rag_max_chunks_per_module: int = 0

    # ── Embeddings (TEI / OpenAI-compatible) ──────────────────────
    # Phase 3: embeddings move from Ollama to an OpenAI-compatible
    # endpoint (Hugging Face TEI, or Ollama's own /v1/embeddings).
    embedding_base_url: str = ""
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    embedding_batch_size: int = 64
    embedding_api_key: str = ""

    # ── Vector store (pgvector) ───────────────────────────────────
    # Index schema version + chunk schema version together decide
    # whether the running code is compatible with the stored vectors.
    # A mismatch blocks startup until a re-embed runs.
    vector_index_version: str = "v3_pgvector"
    vector_collection: str = "ansible_docs"

    # ── ansible-lint ─────────────────────────────────────────────
    ansible_lint_mode: str = "auto"

    # ── Observability (Phase 6a) ─────────────────────────────────
    # Langfuse is opt-in: leave enabled=false until keys exist so a
    # missing stack never breaks generation.
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # From containers on Docker Desktop, host.docker.internal reaches the
    # published Langfuse UI port. On the host process use localhost:3000.
    # SDK v3+ prefers LANGFUSE_BASE_URL; LANGFUSE_HOST remains an alias.
    langfuse_host: str = "http://localhost:3000"
    langfuse_base_url: str = ""
    # Separates staging vs production in the Langfuse UI filter bar.
    langfuse_tracing_environment: str = ""

    # ─────────────────────────────────────────────────────────────
    #  Validation
    # ─────────────────────────────────────────────────────────────

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"must be one of {sorted(allowed)}")
        return level

    @field_validator("password_min_length")
    @classmethod
    def _min_password_length(cls, value: int) -> int:
        # NIST 800-63B puts the floor at 8; 12 is the practical minimum
        # once you drop composition rules, which we do.
        if value < 8:
            raise ValueError("must be at least 8")
        return value

    @model_validator(mode="after")
    def _check_required(self) -> Settings:
        missing: list[str] = []
        if not self.database_url.strip():
            missing.append("DATABASE_URL — e.g. postgresql+psycopg2://user:pass@localhost:5432/ansibleai")
        if not self.secret_key.strip():
            missing.append(
                "SECRET_KEY — generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if missing:
            raise ConfigError(
                "Missing required configuration in .env (or the environment):\n  - "
                + "\n  - ".join(missing)
            )

        if self.env != "development":
            if len(self.secret_key) < 32:
                raise ConfigError("SECRET_KEY must be at least 32 characters outside development.")
            if self.debug:
                raise ConfigError("DEBUG must be off outside development.")
            if self.registration_mode == "open":
                raise ConfigError(
                    "REGISTRATION_MODE=open is not allowed outside development; "
                    "use 'domain' or 'closed'."
                )
            # HTTPS-only cookies and HSTS are non-negotiable once the app
            # is reachable off localhost.
            self.session_cookie_secure = True
            self.force_https = True

        if self.registration_mode == "domain" and not self.email_domains:
            raise ConfigError(
                "REGISTRATION_MODE=domain requires ALLOWED_EMAIL_DOMAINS "
                "(comma-separated, e.g. example.com)."
            )

        redis_consumers = {
            "SESSION_BACKEND": self.session_backend == "redis",
            "RATE_LIMIT_BACKEND": self.rate_limit_backend == "redis",
            "CANCEL_BACKEND": self.cancel_backend == "redis",
            "LOG_STREAM_BACKEND": self.log_stream_backend == "redis",
        }
        if any(redis_consumers.values()) and not self.redis_url.strip():
            selected = ", ".join(name for name, on in redis_consumers.items() if on)
            raise ConfigError(f"REDIS_URL is required when a redis backend is selected ({selected}).")

        if self.env != "development":
            # Eager mode runs the agent inside the HTTP request, which is
            # exactly the multi-minute blocking call Phase 2 removed. It is
            # a development convenience, never a deployment mode.
            if self.celery_task_always_eager:
                raise ConfigError(
                    "CELERY_TASK_ALWAYS_EAGER must be false outside development. "
                    "Run a worker instead: `docker compose run api worker`."
                )
            # Without a message queue, progress emitted by the worker never
            # reaches the API process holding the client's socket, so the UI
            # would hang on "thinking" until the user reloaded.
            if not self.socketio_message_queue.strip():
                raise ConfigError(
                    "SOCKETIO_MESSAGE_QUEUE is required outside development so "
                    "Celery-emitted progress reaches connected clients."
                )
            for name, value in (
                ("CANCEL_BACKEND", self.cancel_backend),
                ("LOG_STREAM_BACKEND", self.log_stream_backend),
            ):
                if value == "memory":
                    raise ConfigError(
                        f"{name}=memory is single-process only and cannot be used "
                        f"outside development."
                    )

        if self.celery_soft_time_limit >= self.celery_time_limit:
            raise ConfigError(
                "CELERY_TIME_LIMIT must exceed CELERY_SOFT_TIME_LIMIT, otherwise the "
                "task is killed before it can record why it timed out."
            )

        if self.artifact_backend == "s3":
            missing_s3 = [
                name
                for name, value in (
                    ("S3_ENDPOINT_URL", self.s3_endpoint_url),
                    ("S3_ACCESS_KEY", self.s3_access_key),
                    ("S3_SECRET_KEY", self.s3_secret_key),
                )
                if not value.strip()
            ]
            if missing_s3:
                raise ConfigError(
                    "ARTIFACT_BACKEND=s3 requires: " + ", ".join(missing_s3)
                )

        if bool(self.bootstrap_admin_email) != bool(self.bootstrap_admin_password):
            raise ConfigError(
                "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD must be set together."
            )

        if self.auth_mode in ("hybrid", "oidc") and not self.oidc_configured:
            raise ConfigError(
                f"AUTH_MODE={self.auth_mode} requires OIDC_ISSUER, "
                "OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, and OIDC_REDIRECT_URI."
            )

        if self.user_daily_token_budget < 0:
            raise ConfigError("USER_DAILY_TOKEN_BUDGET must be >= 0 (0 disables the cap).")

        return self

    # ─────────────────────────────────────────────────────────────
    #  Derived accessors
    # ─────────────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def email_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def oidc_configured(self) -> bool:
        return bool(
            self.oidc_issuer.strip()
            and self.oidc_client_id.strip()
            and self.oidc_client_secret.strip()
            and self.oidc_redirect_uri.strip()
        )

    @property
    def oidc_enabled(self) -> bool:
        return self.auth_mode in ("hybrid", "oidc") and self.oidc_configured

    @property
    def break_glass_emails(self) -> set[str]:
        return {
            e.strip().lower()
            for e in self.auth_break_glass_emails.split(",")
            if e.strip()
        }

    @property
    def local_login_enabled(self) -> bool:
        """Whether the SPA should show the email/password form."""
        if self.auth_mode in ("local", "hybrid"):
            return True
        # oidc: members still type a password on AnsibleAI (ROPC).
        return self.oidc_configured or bool(self.break_glass_emails)

    @property
    def registration_enabled(self) -> bool:
        if self.auth_mode in ("hybrid", "oidc"):
            return False
        return self.registration_mode != "closed"

    @property
    def app_admin_ui(self) -> bool:
        """KB mutation chrome belongs in local mode only (Keycloak is the admin plane)."""
        return self.auth_mode == "local"

    @property
    def oidc_scope_list(self) -> list[str]:
        scopes = [s.strip() for s in self.oidc_scopes.split() if s.strip()]
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        return scopes

    @property
    def broker_url(self) -> str:
        """Celery broker, defaulting to the shared Redis instance."""
        return self.celery_broker_url.strip() or self.redis_url

    @property
    def result_backend(self) -> str:
        """
        Celery result store. Results are only used to surface "did the
        enqueue take", never to deliver output — progress and completion
        travel over Socket.IO instead.
        """
        return self.celery_result_backend.strip() or self.redis_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache Settings, translating pydantic errors into a readable message."""
    try:
        return Settings()
    except ValidationError as exc:
        details = "\n  - ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration:\n  - {details}") from exc


def generate_secret_key() -> str:
    """Convenience for bootstrapping a .env file."""
    return secrets.token_urlsafe(64)


# Import-time validation: a bad config should stop the process here.
settings = get_settings()


def flask_config() -> dict[str, object]:
    """Flask `app.config` values derived from Settings."""
    from datetime import timedelta

    s = settings
    return {
        "SECRET_KEY": s.secret_key,
        "ENV": s.env,
        "DEBUG": s.debug,
        "SQLALCHEMY_DATABASE_URI": s.database_url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "pool_pre_ping": True,
            "pool_recycle": s.db_pool_recycle,
        },
        # ── Session cookie hardening ──
        "SESSION_COOKIE_NAME": s.session_cookie_name,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SECURE": s.session_cookie_secure,
        # Lax (not Strict) so a normal top-level link back into the app
        # still carries the session; the CSRF token covers writes.
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(minutes=s.session_lifetime_minutes),
        "SESSION_REFRESH_EACH_REQUEST": True,
        # ── Flask-Session (server-side store) ──
        "SESSION_TYPE": s.session_backend,
        "SESSION_PERMANENT": True,
        "SESSION_KEY_PREFIX": "ansibleai:sess:",
        # ── CSRF ──
        "WTF_CSRF_TIME_LIMIT": None,
        "WTF_CSRF_SSL_STRICT": not s.is_development,
        # Uploads are not supported; cap bodies so a huge POST cannot
        # exhaust memory.
        "MAX_CONTENT_LENGTH": 2 * 1024 * 1024,
    }


def env_summary() -> dict[str, object]:
    """Non-secret configuration snapshot, safe to log at startup."""
    s = settings
    return {
        "env": s.env,
        "debug": s.debug,
        "db_dialect": s.database_url.split("://", 1)[0] if "://" in s.database_url else "?",
        "session_backend": s.session_backend,
        "registration_mode": s.registration_mode,
        "allowed_email_domains": s.email_domains,
        "rate_limit_backend": s.rate_limit_backend if s.rate_limit_enabled else "disabled",
        "agent_provider": "ollama",
        "agent_model": s.agent_model,
        "playbook_model": s.playbook_model,
        "log_format": s.log_format,
        "cookie_secure": s.session_cookie_secure,
        "auth_mode": s.auth_mode,
        "oidc_enabled": s.oidc_enabled,
        "token_budget": s.user_daily_token_budget,
        "socketio_async_mode": s.socketio_async_mode,
        "socketio_message_queue": bool(s.socketio_message_queue),
        "celery_eager": s.celery_task_always_eager,
        "cancel_backend": s.cancel_backend,
        "log_stream_backend": s.log_stream_backend,
        "artifact_backend": s.artifact_backend,
        "langfuse_enabled": s.langfuse_enabled,
        "langfuse_host": s.langfuse_host,
    }


__all__ = [
    "BACKEND_ROOT",
    "PROJECT_ROOT",
    "ConfigError",
    "Settings",
    "env_summary",
    "flask_config",
    "generate_secret_key",
    "get_settings",
    "settings",
]
