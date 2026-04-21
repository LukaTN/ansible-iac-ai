/* =============================================================
   chat/state.js — store slice for the active conversation.

   Shape:
     currentId       : number | null    active thread id
     messages        : Array<Message>   messages of the active thread
     pendingThreads  : Set<number>      saved threads awaiting agent reply
     pendingNew      : boolean          a /api/chat call was started
                                        before the thread existed yet
   ============================================================= */

import { createStore } from '../store.js';

export const chatStore = createStore({
  currentId     : null,
  messages      : [],
  pendingThreads: new Set(),
  pendingNew    : false,
});

/** True if the currently visible thread has an in-flight request. */
export function isCurrentThreadPending(s = chatStore.get()) {
  if (s.currentId === null) return s.pendingNew;
  return s.pendingThreads.has(s.currentId);
}

/** Marks the origin thread as pending (originId null = new chat). */
export function markPending(originId) {
  const s = chatStore.get();
  if (originId === null) {
    if (!s.pendingNew) chatStore.set({ pendingNew: true });
  } else if (!s.pendingThreads.has(originId)) {
    const next = new Set(s.pendingThreads);
    next.add(originId);
    chatStore.set({ pendingThreads: next });
  }
}

/** Clears pending state for both the origin and the assigned id. */
export function clearPending(originId, assignedId) {
  const s = chatStore.get();
  const patch = {};
  if (originId === null && s.pendingNew) patch.pendingNew = false;
  if (originId !== null && s.pendingThreads.has(originId)) {
    patch.pendingThreads = new Set(s.pendingThreads);
    patch.pendingThreads.delete(originId);
  }
  if (assignedId != null) {
    const cur = patch.pendingThreads || s.pendingThreads;
    if (cur.has(assignedId)) {
      patch.pendingThreads = new Set(cur);
      patch.pendingThreads.delete(assignedId);
    }
  }
  if (Object.keys(patch).length) chatStore.set(patch);
}
