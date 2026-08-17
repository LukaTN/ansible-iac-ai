# Phase 5 / 5b — Keycloak identity

**Status: complete** (August 2026). Members sign in on **AnsibleAI** with
email and password. Flask calls Keycloak’s token endpoint (resource-owner
password grant). The browser does **not** open Keycloak’s login theme.

Admins use the **Keycloak console only** (create users, temporary
passwords, SMTP). AnsibleAI has no invite screen and no admin chrome
when `AUTH_MODE` is `hybrid` or `oidc`.

oauth2-proxy remains the Kubernetes ingress pattern and is **not**
required on the laptop stack.

## Auth modes

| `AUTH_MODE` | AnsibleAI login | Identity | Registration | App admin UI |
| ----------- | --------------- | -------- | ------------ | ------------ |
| `local` (default) | local argon2id | AnsibleAI | existing policy | yes (`role=admin`) |
| `hybrid` / `oidc` | email + password (Keycloak) | Keycloak | invite-only | hidden |
| break-glass emails | local argon2id | local hash | n/a | n/a |

Default stays `local` so tests and `python app.py` need no Keycloak.
`OIDC_BROWSER_REDIRECT=true` re-enables the old hosted-UI escape hatch.

## Compose (laptop)

```bash
# 1. Copy secrets and set:
#    AUTH_MODE=hybrid
#    OIDC_CLIENT_SECRET=ansibleai-dev-oidc-secret
#    KEYCLOAK_ADMIN_PASSWORD=...
#    AUTH_BREAK_GLASS_EMAILS=admin@ansibleai.local
cp .env.docker.example .env.docker

# 2. Start the app + Keycloak
docker compose --env-file .env.docker --profile sso up --build

# 3. Open
#    App:      http://localhost:5000   (members)
#    Keycloak: http://localhost:8080   (admins: /admin / KEYCLOAK_ADMIN_PASSWORD)
```

First Keycloak boot imports `realm-ansibleai.json` (realm `ansibleai`,
client `ansibleai-web` with Direct Access Grants + a service account).
Re-import does **not** overwrite an existing realm; change the client
secret in both the JSON (fresh volume) and `.env.docker`.

If the realm already exists from Phase 5, enable **Direct access grants**
on `ansibleai-web`, turn **Service accounts** on, and grant the service
account `manage-users` / `view-users` on `realm-management`. Alternatively
set `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` on the API so it can use
`admin-cli` in the master realm.

### Invite a member

1. Keycloak → realm **ansibleai** → **Users** → **Add user** (email as username).
2. **Credentials** → set a password and leave **Temporary** ON.
3. Tell the person to open AnsibleAI, sign in with that email and temporary
   password, then choose a new password **on AnsibleAI**. They never see a
   Keycloak page.

### Hostname split

| Who                   | URL                                                      |
| --------------------- | -------------------------------------------------------- |
| Browser / token `iss` | `http://localhost:8080/realms/ansibleai` (`OIDC_ISSUER`) |
| API token + JWKS      | `http://keycloak:8080` (`OIDC_INTERNAL_BASE_URL`)        |

### Demo user (local only)

Imported with the realm:

- email: `sso-admin@ansibleai.local`
- password: `ansibleai-sso-dev-only` (permanent)
- signs into AnsibleAI as a **member** (`OIDC_MAP_APP_ADMIN` defaults off)

Change that password in the Keycloak console before any shared use.

### No SMTP on the laptop

The imported realm has **Verify email** off, **self-registration** off,
and an empty SMTP config. Configure SMTP in Keycloak when you want
invitation emails from the console.

If you already imported an older realm with verify-email on:

1. Admin console → realm **ansibleai** → **Realm settings** → **Login**
2. Turn **Verify email** off → Save
3. **Users** → pick the user → set **Email verified** ON → clear any
   **Required user actions** except **Update Password** when you intend
   a temporary password

Set `OIDC_REQUIRE_EMAIL_VERIFIED=false` in `.env.docker` so the API
accepts users whose Keycloak email is still unmarked (Compose has no
mail server). In real deployments leave it `true` and configure SMTP.

### Account linking

On first password login Keycloak `sub` is stored on `users.external_id`
and `provider=keycloak`. A matching **verified** email reuses the existing
row. Local password hashes are cleared except for `AUTH_BREAK_GLASS_EMAILS`.

Keycloak group `ansibleai-admins` is **not** mapped to AnsibleAI
`role=admin` unless `OIDC_MAP_APP_ADMIN=true`. Identity admins stay in
the Keycloak console.

### Token budgets

`USER_DAILY_TOKEN_BUDGET` (0 = unlimited) is enforced in the Celery
worker. Members see used / remaining / cap under **Account**. Remaining
budget is also attached to the Langfuse `generate-playbook` trace as
`token_budget_*`.

## Kubernetes (later)

- Run Keycloak as a Deployment (or the Bitnami/operator chart) with
  Postgres from CloudNativePG.
- Put oauth2-proxy at ingress only if you later drop ROPC.
- Keep in-app JWT verification for API and Socket.IO (`Authorization: Bearer` and `connect({ token })`).
- Client secret via External Secrets + Vault (Phase 8).

## Disaster recovery

- Back up the Keycloak Postgres database (`keycloak`) with the same PITR
  story as the app DB (Phase 8).
- Realm JSON in git is the **bootstrap**, not the live source of users.
- Break-glass: `AUTH_MODE=hybrid` or `oidc` plus
  `AUTH_BREAK_GLASS_EMAILS` still accepts argon2id passwords for those
  addresses.

## MFA / LDAP

Not enabled in the imported realm. Resource-owner password grant cannot
complete OTP or social IdPs. If those are required later, turn
`OIDC_BROWSER_REDIRECT` back on or replace ROPC.
