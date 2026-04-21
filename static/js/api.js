/* =============================================================
   api.js — single source of truth for every HTTP call the app
   makes. When a backend route changes, edit it here only.

   All helpers return parsed JSON; failures are surfaced as a
   thrown `Error` with the response body attached so the caller
   can decide whether to show it.
   ============================================================= */

async function _parse(res) {
  const txt = await res.text();
  try { return JSON.parse(txt); } catch { return txt; }
}

async function _json(url, init) {
  const res = await fetch(url, init);
  const body = await _parse(res);
  if (!res.ok) {
    const err = new Error((body && body.error) || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

function _postJson(url, payload) {
  return _json(url, {
    method : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body   : JSON.stringify(payload || {}),
  });
}

export const api = {
  threads: {
    list  : ()   => _json('/api/threads'),
    open  : (id) => _json(`/api/threads/${id}`),
    delete: (id) => _json(`/api/threads/${id}`, { method: 'DELETE' }),
    clear : ()   => _json('/api/threads', { method: 'DELETE' }),
  },

  chat: {
    send: (threadId, message) =>
      _postJson('/api/chat', { thread_id: threadId, message }),
  },

  stats: {
    get: () => _json('/stats'),
  },

  rag: {
    status: () => _json('/rag/status'),
  },

  docs: {
    status        : ()         => _json('/docs/status'),
    check         : ()         => _postJson('/docs/check-updates', {}),
    rescrape      : (modules)  => _postJson('/docs/rescrape', { modules }),
    rollbackList  : ()         => _json('/docs/rollback/list'),
    restore       : (filename) => _postJson('/docs/rollback/restore', { filename }),
    sessions      : (limit=10) => _json(`/docs/sessions?limit=${encodeURIComponent(limit)}`),
    session       : (id)       => _json(`/docs/sessions/${id}`),
    streamUrl     : (id)       => `/docs/stream/${id}`,
  },
};
