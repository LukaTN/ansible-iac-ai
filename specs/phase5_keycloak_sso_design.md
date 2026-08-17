# Feature: Phase 5 — Keycloak SSO

## Requirements (EARS)

- While `AUTH_MODE=local`, when a user signs in with email and password, the system shall behave exactly as Phase 0 (no Keycloak required).
- While `AUTH_MODE=hybrid` or `oidc` and Keycloak is configured, when the user chooses SSO, the system shall run an Authorization Code + PKCE flow, verify tokens, and establish the existing Flask-Login session.
- While a Keycloak identity is new, when the ID token contains a verified email that already exists locally, the system shall link `provider=keycloak` and `external_id=sub` to that row (no second account).
- While a Keycloak identity is new and the email is unknown, when `email_verified` is true, the system shall create an application user with a null password hash.
- While an access token is presented as `Authorization: Bearer`, when the JWT is valid against Keycloak JWKS, the system shall authenticate the request without a session cookie (API + Socket.IO).
- While Keycloak groups include `ansibleai-admins`, when the user signs in via OIDC, the system shall set `users.role=admin`. Linked local admins shall not be demoted automatically.
- While `AUTH_MODE=oidc`, when a password login is attempted, the system shall accept it only for break-glass emails listed in `AUTH_BREAK_GLASS_EMAILS`.
- While a daily token budget is configured (`USER_DAILY_TOKEN_BUDGET>0`), when a generation would exceed the remaining budget, the system shall refuse the turn and record usage on the Langfuse trace when tracing is on.

## Architecture

### [Frontend]
- Login page loads `GET /api/auth/config` (public).
- If `oidc_enabled`, show **Sign in with SSO** (full-page redirect to `/api/auth/oidc/login`).
- Hide password form when `local_login_enabled` is false; hide register when `registration_enabled` is false.
- Hide **Change password** when `user.has_password` is false.
- Loading/error: SSO is a navigation, not a fetch; local form keeps existing alerts.
- Accessibility: SSO control is a real link/button with a clear label.

### [Backend]
- `GET /api/auth/config` — non-secret auth capabilities.
- `GET /api/auth/oidc/login` — start OIDC (state, nonce, PKCE in server session).
- `GET /api/auth/oidc/callback` — code exchange, ID-token verify, account link, session rotation.
- Confidential client: secret stays on the API; SPA never sees it.
- `login_manager.request_loader` — Bearer JWT → User (no session written).
- Socket.IO `connect` accepts `{ token }` when the cookie session is absent.
- Worker: Redis daily token counter per `user_id`; Langfuse metadata `token_budget_*`.

### [Security]
- Auth: default-deny unchanged; new public endpoints are only config + OIDC start/callback.
- Authz: Phase 0 role gates unchanged; groups map onto `role`.
- Input: email from token is validated; `email_verified` required; nonce/state/PKCE required.
- Output: `User.to_dict()` still omits hashes; adds `has_password` boolean only.
- Rate limit: OIDC login uses the login limiter; token exchange failures audited.
- CSRF: GET callback; cookie session after login still CSRF-protected. Bearer-only requests have no cookie session.
- Audit: `auth.oidc.success` / `auth.oidc.failure` / `auth.oidc.linked`.
- Secrets: client secret and Keycloak admin password via `.env.docker`, never in the SPA.

## Auth modes

| `AUTH_MODE` | Password | SSO | Registration |
|-------------|----------|-----|--------------|
| `local` (default) | yes | no | existing policy |
| `hybrid` | yes | yes | existing policy |
| `oidc` | break-glass emails only | yes | disabled |

## Keycloak (Compose)

- Realm `ansibleai`, client `ansibleai-web` (confidential, standard flow + PKCE).
- Groups `ansibleai-admins` / `ansibleai-users`; groups mapper on the ID/access token.
- Issuer in the browser: `http://localhost:8080/realms/ansibleai`.
- In-cluster token/JWKS URL: `http://keycloak:8080` (`OIDC_INTERNAL_BASE_URL`).

oauth2-proxy at ingress is the Kubernetes edge pattern (documented under `deploy/keycloak/`); Compose uses in-app OIDC so the existing session cookie and CSRF model stay intact.

## Implementation plan

- [x] Design document
- [x] Config + models constants
- [x] OIDC client, linking, JWT bearer, token budgets
- [x] Routes + security hooks + worker enforcement
- [x] Keycloak realm + Compose service
- [x] Frontend
- [x] Tests
