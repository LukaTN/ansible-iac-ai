/* =============================================================
   panel/docs.js — Docs tab (KB status, rollback, health, scrape
   log with SSE live feed, changelog).

   The SSE EventSource is stored on `panelStore.evtSource` so it
   can be closed when the user switches tabs, leaves the panel, or
   starts a new rescrape. This fixes the long-standing leak where
   the browser kept the connection open forever.
   ============================================================= */

import { $, esc } from '../util/dom.js';
import { api } from '../api.js';
import { panelStore, closeDocsStream } from './state.js';

export function docsClearLog() {
  const t = $('docs-terminal');
  if (t) t.textContent = '';
}

function _docsAppendLog(line) {
  const term = $('docs-terminal');
  if (!term) return;
  term.textContent += (term.textContent ? '\n' : '') + line;
  term.scrollTop = term.scrollHeight;
}

function _docsSetLiveStatus(txt, kind) {
  const el = $('docs-live-status');
  if (!el) return;
  el.textContent = txt;
  el.className = 'pill ' + (kind || 'idle');
}

function _docsConnectStream(sessionId) {
  closeDocsStream();
  _docsSetLiveStatus('streaming', 'ok');
  const es = new EventSource(api.docs.streamUrl(sessionId));
  panelStore.set({ evtSource: es });
  es.onmessage = (ev) => {
    const line = (ev.data || '').replaceAll('\\n', '\n');
    if (line.includes('STREAM_END')) {
      _docsSetLiveStatus('done', 'idle');
      closeDocsStream();
      return;
    }
    _docsAppendLog(line);
  };
  es.addEventListener('ping', () => {});
  es.onerror = () => _docsSetLiveStatus('disconnected', 'warn');
}

export async function docsLoadStatus() {
  try {
    const data = await api.docs.status();
    $('kb-generated-at').textContent = data.kb_metadata?.generated_at || '—';
    $('kb-total-mods').textContent   = data.kb_metadata?.total_modules ?? '—';

    const list = $('docs-health-list');
    const rows = (data.module_health || []).slice(0, 12);
    if (!rows.length) { list.innerHTML = '<div class="no-data">No KB loaded.</div>'; return; }
    list.innerHTML = rows.map((r) => {
      const bad = r.health_score < 70;
      return `<div class="doc-row">
        <div class="doc-row-left">
          <div class="doc-row-title">${esc(r.slug)}</div>
          <div class="doc-row-sub">params=${r.param_count} · examples=${r.example_count} · required=${r.required_count}</div>
        </div>
        <div class="score ${bad ? 'bad' : 'ok'}">${r.health_score}%</div>
      </div>`;
    }).join('');
  } catch (e) { console.error(e); }
}

export async function docsLoadRollback() {
  try {
    const data = await api.docs.rollbackList();
    const list = $('docs-rollback-list');
    const vers = data.versions || [];
    if (!vers.length) { list.innerHTML = '<div class="no-data">No backups yet.</div>'; return; }
    list.innerHTML = vers.slice(0, 10).map((v) => `
      <div class="doc-row">
        <div class="doc-row-left">
          <div class="doc-row-title">${esc(v.filename)}</div>
          <div class="doc-row-sub">${new Date(v.modified_at).toLocaleString()} · ${(v.size / 1024).toFixed(1)} KB</div>
        </div>
        <button class="btn-ghost" data-action="docs-restore" data-filename="${esc(v.filename)}">Restore</button>
      </div>`).join('');
  } catch (e) { console.error(e); }
}

export async function docsRestore(filename) {
  if (!confirm(`Restore ${filename}?`)) return;
  try {
    const data = await api.docs.restore(filename);
    await docsLoadStatus();
    alert('Restored: ' + data.restored);
  } catch (e) {
    alert((e && (e.body?.error || e.message)) || 'Restore failed');
  }
}

export async function docsLoadSessions() {
  try {
    const sessions = await api.docs.sessions(10);
    const box = $('docs-changelog');
    if (!sessions.length) { box.innerHTML = '<div class="no-data">No sessions yet.</div>'; return; }
    const latest = sessions[0];
    const det    = await api.docs.session(latest.id);
    const diffs  = det.session?.summary?.diffs || det.session?.summary?.changed || [];
    if (!diffs.length) { box.innerHTML = `<div class="no-data">Latest session #${latest.id} has no diffs.</div>`; return; }
    box.innerHTML = diffs.slice(0, 10).map((d) => `
      <div class="doc-row">
        <div class="doc-row-left">
          <div class="doc-row-title">${esc(d.module_slug || d.slug)}</div>
          <div class="doc-row-sub">${esc(d.diff_summary || 'changed')}</div>
        </div>
        ${d.health_score != null ? `<div class="score ${d.health_score < 70 ? 'bad' : 'ok'}">${d.health_score}%</div>` : ''}
      </div>`).join('');
  } catch (e) { console.error(e); }
}

export async function docsCheckUpdates() {
  docsClearLog();
  _docsSetLiveStatus('running', 'warn');
  $('docs-check-btn').disabled = true;
  $('docs-rescrape-btn').disabled = true;
  $('docs-changed-list').innerHTML = '<div class="no-data">Checking…</div>';

  let data;
  try {
    data = await api.docs.check();
  } catch (e) {
    _docsSetLiveStatus('failed', 'bad');
    $('docs-check-btn').disabled = false;
    return alert((e && (e.body?.error || e.message)) || 'Check failed');
  }
  _docsConnectStream(data.session_id);
  const out = await docsWaitSession(data.session_id);
  const changed = out.session?.summary?.changed || [];
  const slugs = changed.map((c) => c.slug);
  panelStore.set({ lastCheck: out, changedSlugs: slugs });

  const list = $('docs-changed-list');
  list.innerHTML = !changed.length
    ? '<div class="no-data">No changes detected.</div>'
    : changed.map((c) => `
        <div class="doc-row">
          <div class="doc-row-left">
            <div class="doc-row-title">${esc(c.slug)}</div>
            <div class="doc-row-sub">remote=${(c.remote_hash || '').slice(0, 10)}… · local=${(c.local_hash || '').slice(0, 10)}…</div>
          </div>
          <span class="pill warn">changed</span>
        </div>`).join('');
  $('docs-rescrape-btn').disabled = !slugs.length;
  $('docs-check-btn').disabled = false;
  await docsLoadSessions();
}

export async function docsRescrapeChanged() {
  const { changedSlugs } = panelStore.get();
  if (!changedSlugs.length) return;
  if (!confirm(`Re-scrape ${changedSlugs.length} changed module(s)?`)) return;
  docsClearLog();
  _docsSetLiveStatus('running', 'warn');
  $('docs-rescrape-btn').disabled = true;

  let data;
  try {
    data = await api.docs.rescrape(changedSlugs);
  } catch (e) {
    _docsSetLiveStatus('failed', 'bad');
    $('docs-rescrape-btn').disabled = false;
    return alert((e && (e.body?.error || e.message)) || 'Re-scrape failed');
  }
  _docsConnectStream(data.session_id);
  await docsWaitSession(data.session_id);
  await docsLoadStatus();
  await docsLoadRollback();
  await docsLoadSessions();
  _docsSetLiveStatus('done', 'idle');
}

async function docsWaitSession(sessionId) {
  for (let i = 0; i < 240; i++) {
    const data = await api.docs.session(sessionId);
    const st = data.session?.status;
    if (st && st !== 'running') return data;
    await new Promise((r) => setTimeout(r, 500));
  }
  return await api.docs.session(sessionId);
}
