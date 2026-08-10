"""
=============================================================
  AnsibleAI — authentication smoke test

    python scripts/smoke_auth.py [--base-url http://127.0.0.1:5000]

  Exercises the flow a browser performs against a *running* server:
  probe health, confirm anonymous requests are refused, fail a login,
  succeed, use a protected endpoint, verify CSRF enforcement, log out,
  and confirm the session is dead afterwards.

  Intended as a post-deploy gate: run it after `alembic upgrade head`
  and `seed_admin.py` in CI or against a fresh environment. Exits
  non-zero on the first failed expectation so a pipeline stops.

  Credentials come from BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD
  so no secret is ever passed on the command line.
=============================================================
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import requests

from config import settings

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f'  ({detail})' if detail else ''}")
    if not ok:
        failures.append(label)


def csrf_header(session: requests.Session) -> dict[str, str]:
    """Current CSRF token, re-read each time because it rotates on login."""
    return {"X-CSRFToken": session.cookies.get("csrf_token") or ""}


def run(base: str, email: str, password: str) -> int:
    s = requests.Session()

    print("\n-- probes --")
    check("GET /healthz is public", s.get(f"{base}/healthz", timeout=10).status_code == 200)
    ready = s.get(f"{base}/readyz", timeout=60)
    check(
        "GET /readyz answers without auth", ready.status_code in (200, 503), str(ready.status_code)
    )
    if ready.status_code != 200:
        print(f"       readiness detail: {ready.text[:300]}")

    print("\n-- anonymous access --")
    check(
        "protected endpoint is 401 when anonymous",
        s.get(f"{base}/api/threads", timeout=15).status_code == 401,
    )
    me = s.get(f"{base}/api/auth/me", timeout=15)
    check(
        "/api/auth/me is 200 with authenticated:false",
        me.status_code == 200 and me.json().get("authenticated") is False,
    )
    check(
        "SPA shell is served so the login page can render",
        s.get(f"{base}/", timeout=15).status_code == 200,
    )

    print("\n-- csrf --")
    token = s.get(f"{base}/api/auth/csrf", timeout=15).json().get("csrf_token")
    check("CSRF token issued", bool(token))
    check("csrf_token cookie is set", "csrf_token" in s.cookies)
    check(
        "login without the CSRF header is rejected",
        s.post(
            f"{base}/api/auth/login", json={"email": email, "password": password}, timeout=30
        ).status_code
        == 403,
    )

    print("\n-- login --")
    bad = s.post(
        f"{base}/api/auth/login",
        json={"email": email, "password": "definitely-not-the-password"},
        headers=csrf_header(s),
        timeout=30,
    )
    check("wrong password is 401", bad.status_code == 401, bad.json().get("code", ""))
    unknown = s.post(
        f"{base}/api/auth/login",
        json={"email": "nobody-here@example.invalid", "password": password},
        headers=csrf_header(s),
        timeout=30,
    )
    check(
        "unknown email is indistinguishable from a wrong password",
        unknown.status_code == bad.status_code and unknown.text == bad.text,
    )

    good = s.post(
        f"{base}/api/auth/login",
        json={"email": email, "password": password},
        headers=csrf_header(s),
        timeout=30,
    )
    check(
        "correct password logs in", good.status_code == 200, f"{good.status_code} {good.text[:120]}"
    )
    if good.status_code != 200:
        return 1
    check("no password material in the response", "password" not in good.text.lower())

    print("\n-- authenticated use --")
    threads = s.get(f"{base}/api/threads", timeout=30)
    check("protected endpoint now succeeds", threads.status_code == 200, str(threads.status_code))
    check("GET /stats succeeds", s.get(f"{base}/stats", timeout=60).status_code == 200)
    check(
        "unknown thread id is 404 (no existence oracle)",
        s.get(f"{base}/api/threads/99999999", timeout=30).status_code == 404,
    )
    check(
        "write without the CSRF header is refused",
        s.post(f"{base}/docs/check-updates", json={}, timeout=30).status_code == 403,
    )

    print("\n-- logout --")
    check(
        "logout succeeds",
        s.post(f"{base}/api/auth/logout", headers=csrf_header(s), timeout=30).status_code == 200,
    )
    check(
        "session is dead afterwards",
        s.get(f"{base}/api/threads", timeout=30).status_code == 401,
    )

    print()
    if failures:
        print(f"FAILED {len(failures)} check(s): {failures}")
        return 1
    print("All authentication smoke checks passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    args = parser.parse_args()

    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password or ""
    if not email or not password:
        print(
            "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD must be set to run this check.",
            file=sys.stderr,
        )
        return 2

    try:
        return run(args.base_url.rstrip("/"), email, password)
    except requests.RequestException as exc:
        print(f"Could not reach {args.base_url}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
