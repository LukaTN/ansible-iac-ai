"""
=============================================================
  AnsibleAI — Password hashing and policy

  argon2id is used rather than bcrypt: bcrypt silently truncates at
  72 bytes, which turns a long passphrase into a much weaker secret
  without any error.

  Policy follows NIST SP 800-63B: enforce length, screen against
  known-breached and context-specific values, and drop composition
  rules (forced symbols push users toward predictable patterns).
=============================================================
"""

from __future__ import annotations

import hmac
import re
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from config import settings

# OWASP-recommended argon2id baseline: 19 MiB memory, 2 iterations,
# 1 lane. Raise memory_cost before time_cost if hardening further.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Verified when the supplied email matches no account, so a failed login
# costs the same time whether or not the account exists. Without this,
# response latency alone reveals which emails are registered.
_DUMMY_HASH = _hasher.hash("timing-equalization-placeholder")

MAX_PASSWORD_BYTES = 1024

# Highest-frequency passwords from public breach corpora. Not a
# substitute for a full breach list, but it blocks the values that
# dominate real credential-stuffing attempts.
_COMMON_PASSWORDS = frozenset(
    {
        "123456",
        "123456789",
        "12345678",
        "1234567890",
        "1234567",
        "password",
        "password1",
        "password123",
        "passw0rd",
        "p@ssw0rd",
        "qwerty",
        "qwerty123",
        "qwertyuiop",
        "azerty",
        "azertyuiop",
        "111111",
        "000000",
        "123123",
        "abc123",
        "iloveyou",
        "admin",
        "administrator",
        "root",
        "toor",
        "letmein",
        "welcome",
        "welcome1",
        "monkey",
        "dragon",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "superman",
        "trustno1",
        "changeme",
        "secret",
        "default",
        "test",
        "test123",
        "ansible",
        "ansible123",
        "devops",
        "kubernetes",
        "docker",
        "motdepasse",
        "soleil",
        "bonjour",
    }
)


class PasswordPolicyError(ValueError):
    """Raised when a candidate password fails policy. Message is user-facing."""


def hash_password(password: str) -> str:
    _guard_length(password)
    return _hasher.hash(_normalize(password))


def verify_password(password: str, password_hash: str | None) -> bool:
    """
    Check a password against a stored hash in constant-ish time.

    A missing hash (external-identity account, or unknown email) still
    performs a real argon2 verification against a dummy hash so the
    timing signature is identical.
    """
    candidate = _normalize(password or "")
    if not password_hash:
        _verify_dummy(candidate)
        return False
    try:
        return _hasher.verify(password_hash, candidate)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str = "") -> None:
    """Burn equivalent CPU for an unknown account."""
    _verify_dummy(_normalize(password or ""))


def needs_rehash(password_hash: str | None) -> bool:
    """True when a stored hash predates the current argon2 parameters."""
    if not password_hash:
        return False
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password(password: str, *, email: str = "", display_name: str = "") -> None:
    """
    Raise PasswordPolicyError if `password` is unacceptable.

    `email` and `display_name` are screened against because passwords
    derived from the account itself are trivially guessable.
    """
    if not password:
        raise PasswordPolicyError("Password is required.")

    _guard_length(password)

    minimum = settings.password_min_length
    if len(password) < minimum:
        raise PasswordPolicyError(f"Password must be at least {minimum} characters.")

    folded = _normalize(password).casefold()

    if _matches_common_password(folded):
        raise PasswordPolicyError(
            "That password is too close to a commonly used one. Choose another."
        )

    if len(set(folded)) < 5:
        raise PasswordPolicyError("Password must use at least 5 distinct characters.")

    if re.fullmatch(r"(.+?)\1+", folded):
        raise PasswordPolicyError("Password must not be a repeated sequence.")

    for context in _context_tokens(email, display_name):
        if len(context) >= 4 and context in folded:
            raise PasswordPolicyError("Password must not contain your name or email address.")


# ─────────────────────────────────────────────
#  Internals
# ─────────────────────────────────────────────


_LEET_MAP: dict[int, str] | None = None


def _matches_common_password(folded: str) -> bool:
    """
    Screen a password against the common-password list, defeating the two
    evasions people actually use: appending digits ("password123") and
    character substitution ("P@ssw0rd").

    Trailing filler is removed *before* de-leeting, because translating
    digits first would turn "passw0rd1234" into nonsense rather than
    "password".
    """
    candidates = {folded}

    # "password123" / "qwerty!!" → "password" / "qwerty"
    trimmed = re.sub(r"[^a-z]+$", "", folded)
    if trimmed:
        candidates.add(trimmed)

    for base in tuple(candidates):
        de_leet = base.translate(_leet_map())
        candidates.add(de_leet)
        letters_only = re.sub(r"[^a-z]", "", de_leet)
        if letters_only:
            candidates.add(letters_only)

    return any(c in _COMMON_PASSWORDS for c in candidates)


def _leet_map() -> dict[int, str]:
    """Common character substitutions, built once."""
    global _LEET_MAP
    if _LEET_MAP is None:
        _LEET_MAP = str.maketrans(
            {
                "0": "o", "1": "l", "3": "e", "4": "a", "5": "s",
                "7": "t", "8": "b", "@": "a", "$": "s", "!": "i",
                "|": "l", "+": "t",
            }
        )
    return _LEET_MAP


def _normalize(password: str) -> str:
    """
    NFKC-normalize so a password typed with a different but canonically
    equivalent Unicode encoding still verifies (NIST 800-63B §5.1.1.2).
    """
    return unicodedata.normalize("NFKC", password)


def _guard_length(password: str) -> None:
    # argon2 has no practical input limit, but an unbounded body would
    # let an attacker burn CPU with a multi-megabyte "password".
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")


def _verify_dummy(candidate: str) -> None:
    # Always mismatches; the point is to spend the CPU. Logging here would
    # emit a line on every login attempt with an unknown email address.
    try:
        _hasher.verify(_DUMMY_HASH, candidate)
    except Exception:  # noqa: S110
        pass


def _context_tokens(email: str, display_name: str) -> list[str]:
    tokens: list[str] = []
    local = (email or "").split("@", 1)[0]
    for raw in (local, display_name or ""):
        for part in re.split(r"[^A-Za-z0-9]+", raw.casefold()):
            if part:
                tokens.append(part)
    return tokens


def secure_equals(left: str, right: str) -> bool:
    """Constant-time string comparison for tokens."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


__all__ = [
    "MAX_PASSWORD_BYTES",
    "PasswordPolicyError",
    "hash_password",
    "needs_rehash",
    "secure_equals",
    "validate_password",
    "verify_dummy_password",
    "verify_password",
]
