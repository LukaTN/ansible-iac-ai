# Frontend Design Mode

Mock data is for **UI/UX development only**. It does not replace the Flask backend, database, RAG index, Celery worker, or Socket.IO server.

## Purpose

Inspect every existing frontend screen, overlay, and important UI state without installing or running Python, PostgreSQL, Redis, Celery, Ollama, MinIO, Keycloak, Docker, or Kubernetes.

When Design Mode is off, the SPA talks to the real API exactly as before.

## Enable Design Mode

From `frontend/`:

```bash
npm install
npm run dev:design
```

That runs Vite with `--mode design`, which loads `frontend/.env.design`:

```text
VITE_DESIGN_MODE=true
```

Equivalent:

```bash
# PowerShell
$env:VITE_DESIGN_MODE="true"; npm run dev

# bash
VITE_DESIGN_MODE=true npm run dev
```

Open http://localhost:5173 — the amber **Design Mode** inspector is on the right.

Root shortcut (from the repository root):

```bash
npm run dev:design
```

## Start frontend

Design Mode (no backend):

```bash
cd frontend
npm install
npm run dev:design
```

Normal mode (proxies `/api`, `/stats`, `/rag`, `/docs`, `/socket.io` to Flask on `:5000`):

```bash
cd frontend
npm run dev
```

## Available mock users

Switch these from the inspector **User** section, or sign in on the login form.

| Persona | Email (form login) | Role | Notes |
|---------|-------------------|------|--------|
| Anonymous | — | — | Login / register screens |
| Member | `designer@example.com` | `user` | Capped token budget in Account |
| Admin | `designer.admin@example.com` | `admin` | Docs check-updates / re-scrape / rollback |
| Must change password | `temp.designer@example.com` | `user` | Forced password screen |

Any other email with a password signs in as a member. Password `wrong-password` shows the invalid-credentials error. `pending@example.com` on Register shows the pending-approval notice. Passwords are fake; nothing is sent to a server.

## Available pages

The app has **no client-side router**. AuthGate then a single workspace shell. The inspector maps onto those real surfaces:

| Inspector | What you see |
|-----------|----------------|
| Login | `LoginPage` sign-in |
| Register | Same page, create-account mode |
| Force password | `ForcePasswordChange` |
| Workspace | Header, thread sidebar, chat, optional side panel, footer |

Inside the workspace:

- Empty / active / generating / completed / failed / cancelled / awaiting-user **chat**
- Thread list, search, delete one, delete all (confirm dialogs)
- Account dossier (profile, tokens, password)
- Analytics side panel
- Documentation side panel (member vs admin)
- Four-step onboarding overlay

## Available UI states

**Chat:** Empty (welcome), Active (Kubernetes playbook), Generating (Nginx + agent pipeline), Completed (S3 playbook + validation + sources), Failed (gate errors), Cancelled, Awaiting user (clarifying questions).

**User:** Anonymous, Member, Admin, Must change password.

**Documentation:** Healthy KB, Needs update (changed modules), Scraping (live terminal), Failed scrape, Empty KB.

**Side panel:** Collapsed, Analytics, Docs.

**Overlays:** Account, Onboarding, Delete chat confirm, Delete all confirm.

**Login extras:** Busy, Error, Pending approval, Session expired, Invite-only.

**RAG badge:** Ready (chunk count) or offline.

## Disable Design Mode

- Stop the `dev:design` process and run `npm run dev` instead, **or**
- Set `VITE_DESIGN_MODE=false` (or remove it) and restart Vite.

`vite build` uses production mode and does **not** set `VITE_DESIGN_MODE`, so the inspector is not shown in the Flask-served SPA.

## Important limitations

- No real generation, RAG retrieval, Celery jobs, or ansible-lint.
- Socket.IO is a local EventEmitter; there is no server connection.
- Docs SSE is simulated; Check / Re-scrape buttons mutate mock state only.
- Thread rename exists on the API client but has **no UI** in this app.
- There is no separate admin dashboard, user-management page, or invite UI.
- Sending a chat message in Design Mode appends a canned assistant reply after ~1.2s (still mock).
- CSRF cookies, session cookies, and Keycloak are unused.
