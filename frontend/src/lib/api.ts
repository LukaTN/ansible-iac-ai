import type {
  AuthState,
  ChatAcceptedResponse,
  ChatJobStatus,
  DocsStatus,
  RagStatus,
  RollbackVersion,
  ScrapeSession,
  StatsPayload,
  Thread,
} from './types';

class ApiError extends Error {
  status: number;
  code?: string;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = (body as { code?: string })?.code;
    this.body = body;
  }
}

async function parse(res: Response): Promise<unknown> {
  const txt = await res.text();
  try {
    return JSON.parse(txt);
  } catch {
    return txt;
  }
}

/* ─── Session expiry ───
   Any request may come back 401 once the server-side session is revoked
   (logout elsewhere, password change, epoch bump). A single subscriber —
   the AuthProvider — resets the UI to the login screen, so individual
   call sites do not each need to handle it. */
type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

/* ─── CSRF ───
   The server sets a readable `csrf_token` cookie; we echo it in a header
   on every state-changing request (double-submit pattern). A cross-origin
   page can force the browser to send the cookie but cannot read it to
   populate the header. */
const CSRF_COOKIE = 'csrf_token';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);

function readCsrfCookie(): string | null {
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${CSRF_COOKIE}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : null;
}

/** Ensure a CSRF cookie exists (or force-refresh it) before a write. */
export async function primeCsrf(force = false): Promise<void> {
  if (!force && readCsrfCookie()) return;
  try {
    await fetch('/api/auth/csrf', {
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch {
    // Offline; the write will fail and surface its own error.
  }
}

function isCsrfFailure(status: number, body: unknown): boolean {
  if (status !== 400 && status !== 403) return false;
  const code = (body as { code?: string } | null)?.code;
  if (code === 'csrf') return true;
  const msg = String((body as { error?: string } | null)?.error ?? '');
  return /csrf/i.test(msg);
}

/** Auth endpoints where 401 means bad credentials, not a dead session. */
function isCredentialChallenge(url: string): boolean {
  return (
    url.includes('/api/auth/login') ||
    url.includes('/api/auth/register') ||
    url.includes('/api/auth/password/change')
  );
}

async function json<T>(url: string, init?: RequestInit, retried = false): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  const headers = new Headers(init?.headers);

  if (!SAFE_METHODS.has(method)) {
    // Force a fresh token when none is present. After login the server
    // refreshes the cookie on the response; subsequent writes use that.
    await primeCsrf(!readCsrfCookie());
    const token = readCsrfCookie();
    if (token) headers.set('X-CSRFToken', token);
  }

  const res = await fetch(url, {
    ...init,
    headers,
    // Sessions are cookie-based, so credentials must ride along.
    credentials: 'same-origin',
  });
  const body = await parse(res);

  if (!res.ok) {
    // Stale cookie / rotated session: refresh once and retry the write.
    if (!retried && !SAFE_METHODS.has(method) && isCsrfFailure(res.status, body)) {
      await primeCsrf(true);
      return json<T>(url, init, true);
    }

    const errBody = body as { error?: string; code?: string };
    // Credential failures must not kick the user to the login screen as if
    // their session died — they are still on that screen typing a password.
    if (res.status === 401 && !isCredentialChallenge(url)) {
      onUnauthorized?.();
    }
    throw new ApiError(errBody?.error || `HTTP ${res.status}`, res.status, body);
  }
  return body as T;
}

function postJson<T>(url: string, payload?: unknown, init?: RequestInit): Promise<T> {
  return json<T>(url, {
    method: 'POST',
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers as object) },
    body: JSON.stringify(payload ?? {}),
  });
}

export const api = {
  auth: {
    me: () => json<AuthState>('/api/auth/me'),
    login: (email: string, password: string) =>
      postJson<AuthState>('/api/auth/login', { email, password }),
    register: (email: string, password: string, displayName?: string) =>
      postJson<AuthState>('/api/auth/register', {
        email,
        password,
        display_name: displayName,
      }),
    logout: () => postJson<AuthState>('/api/auth/logout'),
    changePassword: (currentPassword: string, newPassword: string) =>
      postJson<AuthState & { message?: string }>('/api/auth/password/change', {
        current_password: currentPassword,
        new_password: newPassword,
      }),
  },

  threads: {
    list: () => json<Thread[]>('/api/threads'),
    open: (id: number) => json<Thread>(`/api/threads/${id}`),
    delete: (id: number) => json<{ deleted: number }>(`/api/threads/${id}`, { method: 'DELETE' }),
    clear: () => json<{ cleared: boolean }>('/api/threads', { method: 'DELETE' }),
    rename: (id: number, title: string) =>
      json<Thread>(`/api/threads/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      }),
  },

  chat: {
    /** Queues a turn. Resolves as soon as the server accepts it (202). */
    send: (threadId: number | null, message: string, init?: RequestInit) =>
      postJson<ChatAcceptedResponse>('/api/chat', { thread_id: threadId, message }, init),
    cancel: (threadId: number) =>
      postJson<{ thread_id: number; cancelling: boolean }>('/api/chat/cancel', {
        thread_id: threadId,
      }),
    /** Polling fallback for when the socket is not delivering events. */
    status: (threadId: number) => json<ChatJobStatus>(`/api/chat/status/${threadId}`),
  },

  stats: {
    get: () => json<StatsPayload>('/stats'),
  },

  rag: {
    status: () => json<RagStatus>('/rag/status'),
  },

  docs: {
    status: () => json<DocsStatus>('/docs/status'),
    check: () => postJson<{ session_id: number }>('/docs/check-updates', {}),
    rescrape: (modules: string[]) => postJson<{ session_id: number }>('/docs/rescrape', { modules }),
    rollbackList: () => json<{ versions: RollbackVersion[] }>('/docs/rollback/list'),
    restore: (filename: string) => postJson<{ restored: string }>('/docs/rollback/restore', { filename }),
    sessions: (limit = 10) => json<ScrapeSession[]>(`/docs/sessions?limit=${encodeURIComponent(limit)}`),
    session: (id: number) => json<{ session: ScrapeSession }>(`/docs/sessions/${id}`),
    streamUrl: (id: number) => `/docs/stream/${id}`,
  },
};

export { ApiError };
