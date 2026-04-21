/* =============================================================
   panel/state.js — right-panel slice (which tab is visible,
   RAG availability, docs check cache, active SSE connection).
   ============================================================= */

import { createStore } from '../store.js';

export const panelStore = createStore({
  tab          : 'stats',       // 'stats' | 'docs'
  collapsed    : false,
  ragAvailable : false,
  lastCheck    : null,          // last /docs/check-updates result
  changedSlugs : [],            // slugs flagged as changed
  evtSource    : null,          // active EventSource for SSE
});

/**
 * Close (and drop) the current EventSource, if any. Safe to call
 * even when no stream is active. This is what prevents the "SSE
 * stays open forever" leak when the user flips tabs.
 */
export function closeDocsStream() {
  const es = panelStore.get().evtSource;
  if (es) {
    try { es.close(); } catch (e) {}
    panelStore.set({ evtSource: null });
  }
}
