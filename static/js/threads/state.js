/* =============================================================
   threads/state.js — sidebar slice (thread summaries + filter).

   Kept deliberately separate from `chatStore` so that typing in
   the search box doesn't re-render the chat feed.
   ============================================================= */

import { createStore } from '../store.js';

export const threadsStore = createStore({
  items : [],  // Array<ThreadSummary>
  filter: '',  // current search query
});
