# AnsibleAI Frontend UI/UX Redesign Plan

Phased, review-gated plan. **No implementation until the current phase is approved.**

This document is the working agreement for the redesign. It is based on the current frontend (React 19 + TypeScript + Vite, custom CSS in `app.css` / `onboarding.css`, Radix, existing navy/orange tokens). It does **not** invent pages, metrics, or features.

---

## How we will work

1. One phase at a time.
2. After each phase: you review in Design Mode (`npm run dev:design`).
3. You approve, request changes, or skip items before the next phase starts.
4. Visual work stays in `frontend/`. Backend, APIs, chat/thread/auth logic, and Design Mode behavior stay intact.

**Review command (every phase):**

```bash
cd frontend
npm run dev:design
```

Open http://localhost:5173 and use the Design Mode inspector to walk screens and states.

---

## Hard constraints (all phases)

| Keep | Do not |
|------|--------|
| Navy / orange / status palette (`--navy`, `--a1` `#ee8923`, `--a2`, `--ok` / `--warn` / `--err`) | New brand colors, purple/blue/red palettes, heavy gradients |
| Inter + Fira Code for the product UI | Replacing product fonts with a new identity |
| Existing screens and actions | New features (favorites, notifications, user admin, thread rename UI, extra filters) |
| Design/Mock Mode | Breaking mock users, scenes, or the inspector |
| API calls, sockets, providers | Backend / Python / Docker / Helm |

**Note:** Thread **rename** exists on the API client only. There is no rename control in the UI today. We will **not** add it.

**Note:** There is no separate admin dashboard. Admin is the Docs pane (check-updates, re-scrape, rollback, scrape log) when `role === admin` and `app_admin_ui` is on.

---

## Current architecture (what actually exists)

There is **no client-side router**. `AuthGate` then a single workspace:

```text
Anonymous          → LoginPage (login / register)
must_change_password → ForcePasswordChange
Authenticated      → AppShell
                     ├── AppHeader
                     ├── ThreadSidebar | ChatMain | SidePanel (Analytics | Docs)
                     ├── AppFooter
                     └── OnboardingPage overlay (optional)
                     Account menu → AccountPanel dialog
```

**Surfaces to redesign (inventory):**

| Area | Components |
|------|------------|
| Auth | `LoginPage`, `ForcePasswordChange`, `ChangePasswordForm` |
| Shell | `AppHeader`, `AppFooter`, `App.tsx` layout |
| Threads | `ThreadSidebar`, `ConfirmDialog` |
| Chat | `WelcomeScreen`, `MessageList`, `MessageBubble`, `ChatComposer`, `PlaybookCard`, `ValidationCard`, `AgentThinking`, `SourceChip` |
| Account | `AccountMenu`, `AccountPanel` |
| Side panel | `SidePanel`, `StatsPane`, `ModuleBarChart`, `ValidationBreakdown`, `DocsPane` |
| Onboarding | `OnboardingPage` + `onboarding.css` |
| Shared UI | `Button` (underused), `Icons`, `ConfirmDialog` |
| Styles | `:root` tokens in `app.css` (~1,700 lines), `onboarding.css`, little Tailwind usage in product screens |
| Design Mode | Inspector only — **out of product visual scope**; must keep working |

---

## Audit snapshot (Phase 0 findings)

These are the problems the redesign will systematically address. They are observations, not work yet.

### Visual system

- Tokens exist (`--navy`, `--a1`, `--s1`–`--s3`, `--border`, `--txt`, `--muted`) but spacing, radius, and type sizes are ad hoc (`0.58rem` tags, mixed `px`/`rem`, inconsistent padding).
- `Button` variants exist in TypeScript; most screens still use one-off classes (`btn-new`, `btn-send`, `btn-ghost`, `btn-gen-sm`, `auth-submit`).
- Tailwind is installed; the product UI is almost entirely custom CSS. We will **extend CSS tokens**, not rewrite the app in Tailwind utility soup.

### Layout and navigation

- Three-column grid (threads / chat / docs-analytics) is sound on desktop.
- Below **960px**, both the thread list and the side panel are `display: none` with **no replacement** (no drawer, no menu). Mobile is effectively chat-only and incomplete.
- Header mixes brand, live thread title, RAG badge, help, panel toggle, and account. Hierarchy is crowded on laptop widths (badge and tag already hide at 1100px).

### Chat

- Welcome grid is useful but dense; suggestion cards compete with the composer.
- User vs assistant distinction is clear; long assistant answers, YAML, validation, sources, and tool traces stack as many cards.
- `AgentThinking` is visually rich (pipeline SVG, live terminal). Risk: noisy during long waits. Polish toward calmer status, keep the pipeline.
- Composer pending/stop states already exist; they need tighter alignment with the rest of the system.

### Threads

- List + search + new chat + footer (Analytics / Docs / delete all) is the real nav.
- Delete is always visible on every row (clutter). Hover/focus actions would be cleaner.
- Empty states (“No chats yet” / “No matches”) are labels, not guidance.

### Account / analytics / docs

- Account is a “personnel file / dossier” overlay — distinctive, but dense and unlike the workspace.
- Analytics KPIs and charts exist; cards and labels can be clearer without new metrics.
- Docs pane is a long stack of cards (KB, rollback, terminal, health, changelog). Admin vs member difference is real; scanning is hard.

### Onboarding

- Four steps (`welcome`, `loop`, `tour`, `craft`) with a separate display font (Chakra Petch) and blueprint aesthetic.
- Goal: keep the briefing content and steps; calm motion, clearer progress, align chrome with the product (do not turn it into a second brand).

### Accessibility / motion

- Some icon-only controls have `title`; focus rings and reduced-motion are uneven.
- Onboarding and generation use continuous animation. Need `prefers-reduced-motion`.

---

## Design direction (for all later phases)

**Feel:** Calm operator console for IaC — structured, dense where it must be (code), airy where people decide (empty chat, auth, dialogs). Comparable in *discipline* to Linear / GitHub / Vercel, **not** in their colors.

**Surfaces:** Deep navy canvas → slightly lifted `--s1` chrome → `--s2`/`--s3` cards. Orange (`--a1`) for primary actions and brand accent only. `--a2` for informational links/focus. Status colors only for status.

**We will not:** neon glow, glassmorphism, extra gradients, new accent hues, or decorative motion.

---

## Phase map

```text
Phase 1  Design system (tokens + shared primitives)
   ↓ review
Phase 2  Application shell (header, threads column, footer, layout)
   ↓ review
Phase 3  Auth (login, register, force password)
   ↓ review
Phase 4  Chat (empty, messages, playbooks, composer, generation)
   ↓ review
Phase 5  Account
   ↓ review
Phase 6  Analytics + Documentation (incl. admin docs controls)
   ↓ review
Phase 7  Onboarding
   ↓ review
Phase 8  States + responsive + accessibility polish
   ↓ review
Phase 9  Final consistency pass
   ↓ sign-off
```

Phases 3–7 are the “core pages” split so each review is a coherent screen, not a giant dump.

---

# Phase 1 — Design system

**Goal:** One token and component language the rest of the UI can adopt. Little visible “new product” yet; some global type/spacing will already look more even.

### In scope

- Refine `:root` in `app.css` (and shared usage):
  - **Type scale:** page title, section, body, secondary, label, caption, code, metadata
  - **Spacing scale:** 4/8-based (e.g. 4, 8, 12, 16, 24, 32)
  - **Radius:** sm / md / lg / pill
  - **Borders / shadows:** fewer, subtler, consistent
  - **Focus ring** using existing `--a2` / `--a1`
  - **Motion:** short durations + `prefers-reduced-motion`
- Align `Button` variants with CSS (primary, secondary/ghost, destructive, icon) and start using them where a change is local and safe
- Standardize input/search/textarea/label/error patterns (auth + composer + search will consume these in later phases)
- Shared patterns for: badge, empty hint, dialog overlay (build on `ConfirmDialog`)
- Document the token names in a short comment block at the top of `app.css` (not a new brand guide site)

### Out of scope

- Redesigning individual pages
- Changing onboarding fonts yet
- Touching Design Mode inspector styling except if a token rename would break it (avoid that)

### Likely files

- `frontend/src/styles/app.css`
- `frontend/src/styles/globals.css` (only if needed)
- `frontend/src/components/ui/Button.tsx`
- `frontend/src/components/ui/ConfirmDialog.tsx` (light alignment)
- `frontend/src/lib/cn.ts` (unchanged unless needed)

### Review you will do

- Spot-check login + workspace: colors still navy/orange
- Buttons/inputs that already picked up tokens look consistent
- No layout or behavior regressions
- Design Mode inspector still opens and switches scenes

**Stop here for approval.**

---

# Phase 2 — Application shell

**Goal:** Header, thread column, footer, and the three-pane grid feel like one product chrome.

### In scope

- **Header:** clearer brand vs current thread vs actions; compact height; RAG badge as status, not a second title; help and panel toggles as a consistent icon-button set
- **Thread sidebar:** grouping (new chat / search / list / footer tools), active/hover states, truncate long titles, timestamps, **hover/focus-revealed delete** instead of always-on trash
- **Footer:** keep live sync + docs index + shortcuts; reduce competing labels
- **Side panel chrome:** tabs (Analytics / Docs) + collapse — visual only; same `tab` / `collapsed` behavior
- Grid gaps, column widths, scroll regions

### Out of scope

- Chat message internals, login page, onboarding, account dossier
- Adding a thread rename control
- Full mobile drawers (Phase 8), unless a trivial overflow fix is required so desktop review is not broken

### Likely files

- `AppHeader.tsx`, `AppFooter.tsx`, `ThreadSidebar.tsx`, `SidePanel.tsx`
- `app.css` (layout, threads, header, footer, `.side`)
- `App.tsx` only if markup landmarks (`header` / `nav` / `main`) need tightening

### Review you will do

- New chat, search, select thread, delete one, delete all (confirm)
- Open/close Analytics and Docs from sidebar footer and header panel button
- Account menu still opens
- Help still opens onboarding
- Desktop and ~1200px laptop

**Stop here for approval.**

---

# Phase 3 — Authentication

**Goal:** Login, register, and forced password change feel like the same product as the workspace.

### In scope

- `LoginPage` layout, hierarchy, alerts (error / session expired / pending approval / invite-only / busy)
- Register vs sign-in switch
- `ForcePasswordChange` + `ChangePasswordForm` alignment
- Focus, labels, password fields, disabled/busy
- Empty/error copy polish **without** changing `formatAuthError` semantics

### Out of scope

- Auth API, CSRF, OIDC URLs
- Account dossier (Phase 5)

### Likely files

- `LoginPage.tsx`, `AccountPanel.tsx` (forced-password section only if shared form styles)
- Auth-related rules in `app.css`

### Review you will do (Design Mode)

- Pages: Login, Register, Force password
- Login extras: Busy, Error, Pending approval, Session expired, Invite-only
- Form still submits in mock (member/admin emails)

**Stop here for approval.**

---

# Phase 4 — Chat

**Goal:** The main product surface — empty, active, generating, playbook, validation — is easy to scan and calm.

### In scope

- **Welcome / empty:** what the app does, how to start, existing suggestion prompts (same copy/intents)
- **Message bubbles:** user vs assistant; markdown; less stacked chrome
- **PlaybookCard:** readable YAML, copy feedback, filename/module hierarchy
- **ValidationCard / sources / tool trace:** scannable, secondary to the answer
- **Composer:** send/stop, pending bar, keyboard hint, disabled state
- **AgentThinking:** keep pipeline meaning; reduce visual noise; respect reduced motion

### Out of scope

- Changing generation steps, socket events, or message payload fields
- Rewriting playbook YAML or assistant text

### Likely files

- `WelcomeScreen.tsx`, `ChatMain.tsx`, `ChatComposer.tsx`, `MessageList.tsx`, `MessageBubble.tsx`
- `PlaybookCard.tsx`, `ValidationCard.tsx`, `SourceChip.tsx`, `AgentThinking.tsx`
- Chat sections of `app.css`
- `markdown.tsx` only for presentation wrappers, not parser behavior

### Review you will do (Design Mode → Chat)

- Empty, Active, Generating, Completed, Failed, Cancelled, Awaiting user
- Copy playbook, send (canned mock reply), Stop during generating
- Scroll and long YAML

**Stop here for approval.**

---

# Phase 5 — Account

**Goal:** Profile, token usage, and password are readable; logout stays clearly separate.

### In scope

- `AccountMenu` (trigger, menu, roles)
- `AccountPanel` dossier: identity, tokens, conversations, password
- Destructive/sign-out vs primary update
- Dialog overlay consistency with confirm dialogs

### Out of scope

- New preferences, themes, or notification settings
- Changing token-budget fields

### Likely files

- `AccountMenu.tsx`, `AccountPanel.tsx`
- Dossier / account CSS in `app.css`

### Review you will do

- Overlay: Account (member and admin)
- Password form still mock-updates
- Escape / overlay click still closes

**Stop here for approval.**

---

# Phase 6 — Analytics, documentation, and admin docs

**Goal:** Side panel content is scannable. Admin actions stay obvious and visually heavier for destructive restore/re-scrape.

### In scope

- **Analytics:** existing KPIs (generated / valid / warnings / failed), breakdown, module bars — clearer cards and empty/zero states
- **Docs (member):** KB metadata, module health
- **Docs (admin):** check for updates, re-scrape, rollback list/restore, scrape terminal, changelog
- Status pills (idle / streaming / failed) using existing status colors
- Confirmations already using `confirm()` / restore — keep behavior; visual weight of Restore / Re-scrape

### Out of scope

- New charts, date pickers, or metrics
- A standalone admin dashboard page (does not exist)

### Likely files

- `StatsPane.tsx`, `ModuleBarChart.tsx`, `ValidationBreakdown.tsx`
- `DocsPane.tsx`, `SidePanel.tsx` (if tab body spacing remains)
- Panel CSS in `app.css`

### Review you will do (Design Mode)

- Side panel: Collapsed / Analytics / Docs
- Docs: Healthy, Needs update, Scraping, Failed, Empty KB
- User: Member vs Admin (admin-only blocks)
- Check / re-scrape / restore still run against mocks

**Stop here for approval.**

---

# Phase 7 — Onboarding

**Goal:** Same four steps, clearer progress, calmer motion, chrome aligned with the product.

### In scope

- Progress (“step 2 of 4”), primary/back/skip or equivalent **existing** controls
- Hierarchy of titles vs body vs diagrams
- Reduce competing animation; `prefers-reduced-motion`
- Keep step content and terminology (agent loop, workspace tour, prompt craft)

### Out of scope

- New steps, videos, or a different onboarding flow
- Replacing Inter in the main app; onboarding display font may be **toned down**, not swapped for a trendy third family unless you ask

### Likely files

- `OnboardingPage.tsx`, `onboarding.css`

### Review you will do

- Overlay: Onboarding; walk all four steps; close; reopen from header help / account menu

**Stop here for approval.**

---

# Phase 8 — States, responsive, accessibility

**Goal:** Empty / loading / error / generating already touched per screen get a pass; layout works on tablet and mobile without inventing features.

### In scope

**States**

- Auth boot spinner
- Thread empty / no search matches
- Stats/docs empty
- Generation failed / cancelled (clarity of “what happened / next step” using existing copy where possible)
- Disabled buttons and pending composer

**Responsive (adapt, don’t only shrink)**

| Width | Intent |
|-------|--------|
| Desktop (≥1200) | Threads \| Chat \| optional panel |
| Laptop (~960–1200) | Slightly narrower columns; panel as today |
| Tablet (~600–960) | Chat first; **thread list and docs/analytics reachable** (drawer or overlay using existing openPanel / newThread / thread list — no new destinations) |
| Mobile | Header + chat + composer; drawers for threads and panel; footer condensed |

**Accessibility**

- Visible focus
- Icon-only `aria-label`s
- Dialog labels (already partly there)
- Keyboard: menus, dialogs, composer
- Contrast on muted text vs navy (adjust opacity/token use, not hue)

### Out of scope

- New navigation destinations
- A native mobile app

### Likely files

- Layout CSS media queries in `app.css`
- `ThreadSidebar`, `SidePanel`, `AppHeader`, `App.tsx` (open/close drawers — UI state only)
- Small a11y attributes on existing buttons

### Review you will do

- Resize 1440 / 1024 / 768 / 390
- Keyboard through login, thread list, composer, account, confirm dialog
- Design Mode still usable (inspector may overlap small screens; acceptable)

**Stop here for approval.**

---

# Phase 9 — Final polish and sign-off

**Goal:** One coherent product. Catch leftover one-off classes, alignment, and motion.

### In scope

- Cross-screen audit: type, spacing, radius, buttons, icons
- Remove obsolete CSS only when unused
- Confirm Design Mode scenes
- `tsc` / lint on touched frontend
- Checklist in § Final quality bar

### Out of scope

- New work called out as “nice to have later”

### Review you will do

Full inspector walk + tablet/mobile + “it feels like one app.”

**Stop here for final sign-off.**

---

## What we will not do (backlog / non-goals)

- Thread rename UI (API-only today)
- User management, invites, billing, notifications
- Replacing Flask-served SPA routing with React Router
- Redesigning the Design Mode inspector as a product surface
- New icon packs or UI kits
- Dark/light theme toggle (not in the product)

---

## Final quality bar (Phase 9)

### Visual

- [ ] Existing navy / orange / status palette preserved
- [ ] Type, spacing, radius, borders, buttons, forms, icons consistent

### UX

- [ ] Primary actions obvious; delete/restore clearly destructive
- [ ] Empty, loading, error, generating states usable
- [ ] Feedback on copy / send / stop / delete

### Responsive & a11y

- [ ] Desktop, laptop, tablet, mobile
- [ ] Keyboard, focus, labels, contrast

### Technical

- [ ] Design Mode works
- [ ] No backend changes
- [ ] No new npm dependencies unless a phase review explicitly agrees
- [ ] No intentional functionality removal

---

## After each phase — engineer checklist

1. `npm run dev:design` — screens render
2. Inspector scenes for that phase still apply
3. Buttons and existing handlers still fire
4. No new TypeScript errors in `frontend`
5. You review before the next phase starts

---

## Suggested first approval

**Approve Phase 1 (design system)** to begin implementation.  
If you want a different order (e.g. Chat before Auth), say so before Phase 1 starts.
