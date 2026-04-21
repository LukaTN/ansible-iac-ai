/* =============================================================
   threads/actions.js — sidebar behaviors.

   Public surface:
     loadThreads()    — refresh sidebar data from the server
     openThread(id)   — load a thread into the feed
     newThread()      — start an empty, unsaved conversation
     deleteThread(id) — remove a single thread
     clearAllThreads()— remove everything
     init({ onOverviewRefresh })
       — wires delegated listeners on the sidebar root, the "New"
         button, the search box, and the footer buttons.
   ============================================================= */

import { $, delegate } from '../util/dom.js';
import { api } from '../api.js';
import { threadsStore } from './state.js';
import { chatStore } from '../chat/state.js';
import { renderThreadList } from './render.js';
import { renderFeed } from '../chat/render.js';
import { updateThreadHint } from '../chat/actions.js';
import { togglePanel } from '../panel/ui.js';

export async function loadThreads() {
  try {
    const data = await api.threads.list();
    threadsStore.set({ items: Array.isArray(data) ? data : [] });
    renderThreadList();
  } catch (e) {
    console.error('threads', e);
  }
}

export async function openThread(id) {
  try {
    const data = await api.threads.open(id);
    chatStore.set({ currentId: id, messages: data.messages || [] });
    const title = $('chat-title');
    if (title) title.textContent = data.title || 'Chat';
    renderThreadList();
    renderFeed();
    updateThreadHint();
  } catch (e) { console.error(e); }
}

export function newThread() {
  chatStore.set({ currentId: null, messages: [] });
  const title = $('chat-title');
  if (title) title.textContent = 'New chat';
  renderThreadList();
  renderFeed();
  updateThreadHint();
  $('msg-input')?.focus();
}

export async function deleteThread(id) {
  if (!confirm('Delete this chat?')) return;
  try {
    await api.threads.delete(id);
    if (chatStore.get().currentId === id) newThread();
    await loadThreads();
  } catch (e) { console.error(e); }
}

export async function clearAllThreads(onOverviewRefresh) {
  if (!confirm('Delete ALL chats?')) return;
  try {
    await api.threads.clear();
    threadsStore.set({ items: [] });
    newThread();
    if (typeof onOverviewRefresh === 'function') await onOverviewRefresh();
  } catch (e) { console.error(e); }
}

function filterThreads(v) {
  threadsStore.set({ filter: v || '' });
  renderThreadList();
}

/**
 * Wire threads sidebar events. Must be called once after DOM ready.
 *
 * `onOverviewRefresh` is called after clearAllThreads so the stats
 * panel can refresh (avoids a circular panel → threads import).
 */
export function init({ onOverviewRefresh } = {}) {
  // Sidebar row delegation.
  delegate('threads-list', {
    'open-thread': (el) => openThread(Number(el.dataset.id)),
    'delete-thread': (el, ev) => {
      ev.stopPropagation();
      deleteThread(Number(el.dataset.id));
    },
  });

  // Sidebar header + footer delegation (scoped to the whole aside).
  delegate(document.querySelector('.threads'), {
    'new-thread'  : () => newThread(),
    'clear-all'   : () => clearAllThreads(onOverviewRefresh),
    'toggle-stats': () => togglePanel('stats'),
    'toggle-docs' : () => togglePanel('docs'),
  });

  // Search input.
  $('thread-search')?.addEventListener('input', (ev) => filterThreads(ev.target.value));

  // Re-render the sidebar whenever threads OR the current thread changes.
  threadsStore.subscribe(renderThreadList);
  chatStore.subscribe(renderThreadList);

  renderThreadList();
}
