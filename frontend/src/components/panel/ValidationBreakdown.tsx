import type { StatsPayload } from '@/lib/types';

interface Segment {
  key: string;
  label: string;
  value: number;
  color: string;
}

function buildSegments(stats: StatsPayload | null): Segment[] {
  const total = stats?.total ?? 0;
  const valid = stats?.valid ?? 0;
  const warns = stats?.warns ?? 0;
  const invalid = stats?.invalid ?? 0;

  const clean = Math.max(0, valid - warns);
  const unvalidated = Math.max(0, total - valid - invalid);

  const segments: Segment[] = [
    { key: 'clean', label: 'Clean', value: clean, color: 'var(--ok)' },
    { key: 'warn', label: 'Valid with warnings', value: warns, color: 'var(--warn)' },
    { key: 'invalid', label: 'Invalid', value: invalid, color: 'var(--err)' },
  ];

  if (unvalidated > 0) {
    segments.push({
      key: 'pending',
      label: 'Not validated',
      value: unvalidated,
      color: 'var(--muted)',
    });
  }

  return segments;
}

export function ValidationBreakdown({ stats }: { stats: StatsPayload | null }) {
  const segments = buildSegments(stats);
  const total = stats?.total ?? 0;
  const sum = segments.reduce((acc, s) => acc + s.value, 0);

  if (!total) {
    return <div className="ui-empty">No playbooks generated yet</div>;
  }

  return (
    <div className="val-breakdown">
      <div className="val-breakdown-bar" role="img" aria-label="Validation breakdown by outcome">
        {segments.map((s) =>
          s.value > 0 ? (
            <div
              key={s.key}
              className="val-breakdown-seg"
              style={{
                flex: s.value,
                background: s.color,
              }}
              title={`${s.label}: ${s.value}`}
            />
          ) : null,
        )}
      </div>
      <div className="val-breakdown-meta">
        <span>
          <b>{sum}</b> of <b>{total}</b> playbooks accounted for
        </span>
      </div>
      <div className="val-breakdown-legend">
        {segments.map((s) => (
          <div key={s.key} className="val-breakdown-row">
            <span className="val-breakdown-dot" style={{ background: s.color }} />
            <span className="val-breakdown-label">{s.label}</span>
            <span className="val-breakdown-count">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
