/* =============================================================
   store.js — tiny pub/sub store.

   Each feature module owns its own slice (chat, threads, panel).
   `set()` accepts either a patch object (shallow-merged into the
   current state) or a function `(prev) => next`. Subscribers fire
   synchronously after every mutation.

   This is intentionally minimal — no middleware, no selectors, no
   immutability contract. The goal is to make state ownership
   explicit, not to reinvent Redux.
   ============================================================= */

export function createStore(initial) {
  let state = initial;
  const subs = new Set();

  return {
    get: () => state,

    set(patch) {
      const next = typeof patch === 'function'
        ? patch(state)
        : { ...state, ...patch };
      if (next === state) return;
      state = next;
      subs.forEach((fn) => {
        try { fn(state); } catch (err) { console.error('store subscriber', err); }
      });
    },

    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}
