/* =============================================================
   util/time.js — timestamp parsing & humanization.

   The backend serializes `datetime.utcnow()` with an explicit 'Z'
   suffix, but we defend against cases where it's missing: JS would
   otherwise parse a naked ISO string as local time and produce the
   infamous "1h ago" on a fresh row bug.
   ============================================================= */

/** Parse an ISO timestamp coming from the server as UTC. */
export function parseServerTs(ts) {
  if (!ts) return null;
  if (typeof ts !== 'string') return new Date(ts);
  const s = ts.trim();
  const hasTz = /(Z|[+-]\d{2}:?\d{2})$/.test(s);
  return new Date(hasTz ? s : s + 'Z');
}

/** Short, humanized relative time ("just now", "3m ago", "2d ago"…). */
export function relTime(ts) {
  if (!ts) return '';
  const d = parseServerTs(ts);
  if (!d || isNaN(d.getTime())) return '';
  let diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 0) diff = 0;
  if (diff < 60)        return 'just now';
  if (diff < 3600)      return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400)     return Math.floor(diff / 3600) + 'h ago';
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + 'd ago';
  return d.toLocaleDateString();
}
