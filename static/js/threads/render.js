/* =============================================================
   threads/render.js — the left sidebar list.

   Reads from both `threadsStore` (data) and `chatStore.currentId`
   (to highlight the active row). Uses `data-action` + data-id so
   clicks are delegated from the list root.
   ============================================================= */

import { $, esc } from '../util/dom.js';
import { relTime } from '../util/time.js';
import { threadsStore } from './state.js';
import { chatStore } from '../chat/state.js';

export function renderThreadList() {
  const list = $('threads-list');
  if (!list) return;
  const { items, filter } = threadsStore.get();
  const q = filter.trim().toLowerCase();
  const currentId = chatStore.get().currentId;
  const rows = items.filter((t) => !q || (t.title || '').toLowerCase().includes(q));

  if (!rows.length) {
    list.innerHTML = '<div class="threads-empty">' + (q ? 'No matches' : 'No chats yet') + '</div>';
    return;
  }

  list.innerHTML = rows.map((t) => `
    <button class="thread-row ${t.id === currentId ? 'active' : ''}"
            data-action="open-thread" data-id="${t.id}">
      <div class="thread-row-main">
        <div class="thread-row-title">${esc(t.title || 'New chat')}</div>
        <div class="thread-row-meta">${t.message_count} msg · ${relTime(t.updated_at)}</div>
      </div>
      <span class="thread-del" data-action="delete-thread" data-id="${t.id}" title="Delete">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>
        </svg>
      </span>
    </button>
  `).join('');
}
