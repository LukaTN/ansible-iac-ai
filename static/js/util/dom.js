/* =============================================================
   util/dom.js — tiny DOM helpers (no framework, no deps).
   ============================================================= */

/** HTML-escape a value for safe interpolation into templates. */
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** Shorthand for `document.getElementById`. */
export const $ = (id) => document.getElementById(id);

/** `document.querySelectorAll` → real Array. */
export const $$ = (sel, root = document) => Array.from((root || document).querySelectorAll(sel));

/**
 * Attach ONE click listener to `root` that dispatches `[data-action]`
 * matches to `handlers[action]`. Each handler receives
 * `(el, ev)` where `el` is the nearest ancestor carrying the
 * `data-action` attribute (useful because clicks often bubble from
 * inner icons).
 *
 * Returns an unsubscribe function so modules can detach during
 * teardown / hot reload.
 */
export function delegate(root, handlers) {
  const node = typeof root === 'string' ? $(root) : root;
  if (!node) return () => {};
  const onClick = (ev) => {
    const el = ev.target.closest('[data-action]');
    if (!el || !node.contains(el)) return;
    const action = el.dataset.action;
    const fn = handlers[action];
    if (typeof fn === 'function') fn(el, ev);
  };
  node.addEventListener('click', onClick);
  return () => node.removeEventListener('click', onClick);
}
