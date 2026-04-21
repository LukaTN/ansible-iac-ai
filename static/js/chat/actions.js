/* =============================================================
   chat/actions.js — user-driven chat behaviors.

   Exposes `init({ onSent })` which wires:
     • sendMessage (textarea + send button)
     • Ctrl/Cmd+Enter keyboard shortcut
     • textarea autogrow on input
     • delegated clicks inside #chat-feed (copy, toggle-source)
     • welcome-chip suggestions

   `onSent` is called after a successful /api/chat round-trip so
   that the threads sidebar and stats can refresh without creating
   a circular import (threads → chat → threads).
   ============================================================= */

import { $, delegate } from '../util/dom.js';
import { api } from '../api.js';
import {
  chatStore,
  isCurrentThreadPending,
  markPending,
  clearPending,
} from './state.js';
import { renderFeed } from './render.js';

function autoGrow(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 240) + 'px';
}

function updateSendButton() {
  const btn = $('send-btn');
  if (btn) btn.disabled = isCurrentThreadPending();
}

export function updateThreadHint() {
  const hint = $('thread-hint');
  if (!hint) return;
  hint.textContent = chatStore.get().currentId ? 'Continues this chat' : 'Starts a new chat';
}

async function sendMessage(onSent) {
  const input = $('msg-input');
  const text  = (input?.value || '').trim();
  if (!text || isCurrentThreadPending()) return;

  const s = chatStore.get();
  const originThreadId = s.currentId;          // may be null (new chat)
  const tmpId          = `tmp-${Date.now()}`;
  const now            = new Date().toISOString();

  // Optimistic user bubble + pending flag for the origin thread.
  chatStore.set((prev) => ({
    ...prev,
    messages: [...prev.messages, { id: tmpId, role: 'user', content: text, ts: now }],
  }));
  markPending(originThreadId);
  renderFeed();

  input.value = '';
  autoGrow(input);
  updateSendButton();

  const originIsVisible = (assignedId) => {
    const cur = chatStore.get().currentId;
    if (originThreadId !== null) return cur === originThreadId;
    return cur === null || cur === assignedId;
  };

  const appendError = (msg) => {
    chatStore.set((prev) => ({
      ...prev,
      messages: [...prev.messages, {
        id: `err-${Date.now()}`, role: 'assistant',
        content: msg, ts: new Date().toISOString(),
      }],
    }));
    renderFeed();
  };

  try {
    const data = await api.chat.send(originThreadId, text);
    const thread     = data.thread;
    const assignedId = thread.id;
    clearPending(originThreadId, assignedId);

    if (originIsVisible(assignedId)) {
      chatStore.set((prev) => {
        const idx = prev.messages.findIndex((m) => m.id === tmpId);
        const next = prev.messages.slice();
        if (idx >= 0) next[idx] = data.user_message;
        else          next.push(data.user_message);
        next.push(data.assistant_message);
        return { ...prev, currentId: assignedId, messages: next };
      });
      const title = $('chat-title');
      if (title) title.textContent = thread.title || 'Chat';
      renderFeed();
    }

    if (typeof onSent === 'function') {
      try { await onSent({ assignedId, visible: originIsVisible(assignedId) }); }
      catch (err) { console.error('onSent', err); }
    }
    if (originIsVisible(assignedId)) updateThreadHint();
  } catch (e) {
    console.error(e);
    clearPending(originThreadId, null);
    if (originIsVisible(null)) {
      const body = e && (e.body?.error || e.message);
      const networkLike = !e || e.status == null;
      appendError(networkLike
        ? '⚠ Cannot reach the server. Is the backend running?'
        : '⚠ ' + (body || 'Request failed.'));
    }
  } finally {
    updateSendButton();
  }
}

function suggestChip(el) {
  const input = $('msg-input');
  if (!input) return;
  input.value = (el.textContent || '').trim();
  autoGrow(input);
  input.focus();
}

function copyPlaybook(el) {
  const id = el.dataset.target;
  const pre = id && document.getElementById(id);
  if (!pre) return;
  navigator.clipboard.writeText(pre.textContent).then(() => {
    const prev = el.textContent;
    el.textContent = 'copied!';
    setTimeout(() => { el.textContent = prev; }, 1200);
  });
}

function toggleSource(el) {
  const id = el.dataset.target;
  const body = id && document.getElementById(id);
  if (!body) return;
  const open = body.classList.toggle('open');
  el.querySelector('.chev')?.classList.toggle('open', open);
}

/**
 * Wire chat-related DOM events. Must be called once after DOM ready.
 */
export function init({ onSent } = {}) {
  // Feed-scoped delegation (copy + source toggle).
  delegate('chat-feed', {
    'copy-playbook' : copyPlaybook,
    'toggle-source' : toggleSource,
  });

  // Welcome-chip suggestions.
  delegate('welcome', {
    'suggest-chip': suggestChip,
  });

  // Send button.
  const sendBtn = $('send-btn');
  sendBtn?.addEventListener('click', () => sendMessage(onSent));

  // Textarea: autogrow + Ctrl/Cmd+Enter send.
  const input = $('msg-input');
  input?.addEventListener('input', () => autoGrow(input));
  input?.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      sendMessage(onSent);
    }
  });
  autoGrow(input);

  // Re-render feed when any chat slice changes.
  chatStore.subscribe(() => { renderFeed(); updateSendButton(); });

  renderFeed();
  updateSendButton();
}
