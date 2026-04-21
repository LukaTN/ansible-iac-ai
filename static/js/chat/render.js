/* =============================================================
   chat/render.js — the chat feed + message cards.

   Pure rendering: reads from `chatStore`, writes HTML strings to
   `#chat-feed`. Side-effect free otherwise. Event wiring lives
   in `chat/actions.js` (delegated listeners attached once in
   `init()`).
   ============================================================= */

import { $, esc } from '../util/dom.js';
import { relTime } from '../util/time.js';
import { renderMarkdown } from '../util/markdown.js';
import { chatStore, isCurrentThreadPending } from './state.js';

export function renderFeed() {
  const feed = $('chat-feed');
  if (!feed) return;
  const welcome = $('welcome');
  const s = chatStore.get();
  const pendingHere = isCurrentThreadPending(s);

  if (!s.messages.length && !pendingHere) {
    if (welcome) welcome.style.display = '';
    feed.querySelectorAll('.msg, .typing').forEach((el) => el.remove());
    return;
  }
  if (welcome) welcome.style.display = 'none';

  feed.innerHTML = s.messages.map(renderMessage).join('');
  if (pendingHere) feed.insertAdjacentHTML('beforeend', renderTyping());

  requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
}

function renderMessage(m) {
  if (m.role === 'user') {
    return `
      <div class="msg msg-user">
        <div class="msg-bubble user-bubble">${renderMarkdown(m.content)}</div>
        <div class="msg-meta">${relTime(m.ts)}</div>
      </div>`;
  }

  // Heuristic: when the assistant hasn't produced a playbook but the
  // tool trace shows a search was made and the text ends in a
  // question, visually mark the bubble as "awaiting user".
  const awaiting = !m.playbook && Array.isArray(m.tool_trace)
    && m.tool_trace.some((t) => t.tool === 'search_docs')
    && /\?\s*$|\?\s*\n|required|provide|need a few|which|what/i.test((m.content || '').slice(-200));
  const bubbleCls = awaiting ? 'assistant-bubble awaiting' : 'assistant-bubble';
  const parts = [`<div class="msg-bubble ${bubbleCls}">${renderMarkdown(m.content || '')}</div>`];

  if (m.playbook)   parts.push(renderPlaybookBlock(m));
  if (m.validation) parts.push(renderValidationBlock(m.validation));
  if (m.module_ref && m.module_ref.found) parts.push(renderSourceChip(m.module_ref));
  else if (m.rag_meta && m.rag_meta.primary_module) parts.push(renderRagMeta(m.rag_meta));

  return `
    <div class="msg msg-agent">
      <div class="msg-avatar">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
      </div>
      <div class="msg-body">
        ${parts.join('')}
        <div class="msg-meta">${relTime(m.ts)}</div>
      </div>
    </div>`;
}

function renderTyping() {
  return `
    <div class="msg msg-agent typing">
      <div class="msg-avatar">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
      </div>
      <div class="msg-body">
        <div class="msg-bubble assistant-bubble">
          <div class="typing-dots"><span></span><span></span><span></span></div>
          <div class="typing-hint">Thinking · planning tools · consulting docs…</div>
        </div>
      </div>
    </div>`;
}

function renderPlaybookBlock(m) {
  const name = m.filename || 'playbook.yml';
  const id   = `pb-${m.id || Math.random().toString(36).slice(2)}`;
  return `
    <div class="code-card">
      <div class="code-hdr">
        <div class="code-hdr-left">
          <div class="dots"><div class="dot r"></div><div class="dot y"></div><div class="dot g"></div></div>
          <span class="fname">${esc(name)}</span>
          ${m.module ? `<span class="mod-tag-inline">${esc(m.module)}</span>` : ''}
        </div>
        <button class="copy-btn" data-action="copy-playbook" data-target="${id}">copy</button>
      </div>
      <pre id="${id}">${esc(m.playbook)}</pre>
    </div>`;
}

function renderValidationBlock(v) {
  if (!v) return '';
  let cls = 'ok', label, icon;
  const errors = v.errors || [];
  const warnings = v.warnings || [];
  const passed = v.passed_msgs || [];
  if (errors.length) {
    cls = 'bad'; icon = '✕'; label = `Invalid · ${errors.length} error(s)`;
  } else if (warnings.length) {
    cls = 'warn'; icon = '!'; label = `Valid with ${warnings.length} warning(s)`;
  } else {
    cls = 'ok'; icon = '✓'; label = 'All checks passed';
  }
  const checks = [
    ...passed.map((t)   => `<div class="chk ok"><span>✓</span><span>${esc(t)}</span></div>`),
    ...warnings.map((t) => `<div class="chk warn"><span>!</span><span>${esc(t)}</span></div>`),
    ...errors.map((t)   => `<div class="chk bad"><span>✕</span><span>${esc(t)}</span></div>`),
  ].join('');
  return `
    <div class="val-card-inline">
      <div class="val-status ${cls}"><span class="val-icon">${icon}</span><span>${esc(label)}</span></div>
      ${checks ? `<div class="val-checks">${checks}</div>` : ''}
    </div>`;
}

function renderSourceChip(ref) {
  const req = (ref.required_params || []).slice(0, 6).map((p) =>
    `<span class="src-pill"><b>${esc(p.name)}</b><span>${esc(p.type || 'any')}</span></span>`
  ).join('');
  const opt = (ref.optional_params || []).slice(0, 6).map((p) =>
    `<span class="src-pill opt"><b>${esc(p.name)}</b><span>${esc(p.type || 'any')}</span></span>`
  ).join('');
  const id = `src-${Math.random().toString(36).slice(2)}`;
  return `
    <div class="source-chip">
      <div class="source-head" data-action="toggle-source" data-target="${id}">
        <div class="source-head-left">
          <div class="source-icon">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>
            </svg>
          </div>
          <div>
            <div class="source-title">Source · <b>${esc(ref.module)}</b></div>
            <div class="source-sub">${esc((ref.category || 'k8s').toUpperCase())} · ${ref.total_params || '?'} parameters</div>
          </div>
        </div>
        <svg class="chev" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
      </div>
      <div class="source-body" id="${id}">
        ${ref.description ? `<p class="source-desc">${esc(ref.description)}</p>` : ''}
        ${ref.doc_url ? `<a class="source-link" href="${esc(ref.doc_url)}" target="_blank" rel="noreferrer">Open official documentation →</a>` : ''}
        ${req ? `<div class="src-row"><div class="src-row-label">Required</div><div class="src-row-pills">${req}</div></div>` : ''}
        ${opt ? `<div class="src-row"><div class="src-row-label">Optional</div><div class="src-row-pills">${opt}</div></div>` : ''}
      </div>
    </div>`;
}

function renderRagMeta(meta) {
  if (!meta) return '';
  const parts = [];
  if (meta.primary_module)      parts.push(`<span><b>Module</b> ${esc(meta.primary_module)}</span>`);
  if (meta.primary_collection)  parts.push(`<span><b>Collection</b> ${esc(meta.primary_collection)}</span>`);
  if (meta.primary_score != null) parts.push(`<span><b>Score</b> ${meta.primary_score}</span>`);
  if (meta.chunks != null)      parts.push(`<span><b>Chunks</b> ${meta.chunks}</span>`);
  if (!parts.length) return '';
  const link = meta.source_url
    ? ` · <a class="rag-meta-link" href="${esc(meta.source_url)}" target="_blank" rel="noreferrer">docs</a>`
    : '';
  return `<div class="rag-meta-strip">${parts.join('<span class="dotsep">·</span>')}${link}</div>`;
}
