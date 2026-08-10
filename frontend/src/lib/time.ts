export function parseServerTs(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  const s = ts.trim();
  const hasTz = /(Z|[+-]\d{2}:?\d{2})$/.test(s);
  return new Date(hasTz ? s : `${s}Z`);
}

export function relTime(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = parseServerTs(ts);
  if (!d || Number.isNaN(d.getTime())) return '';
  let diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 0) diff = 0;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString();
}
