import type { StatsPayload } from '@/lib/types';

export function ModuleBarChart({ stats }: { stats: StatsPayload | null }) {
  const mods = stats?.modules || [];
  const maxC = mods[0]?.count || 1;

  if (!mods.length) {
    return <div className="ui-empty">No module usage recorded yet</div>;
  }

  return (
    <div className="bar-chart">
      {mods.slice(0, 8).map((m) => {
        const shortName = m.module.split('.').pop() || m.module;
        const pct = Math.round((m.count / maxC) * 100);
        return (
          <div key={m.module} className="bar-item" title={m.module}>
            <div className="bar-lbl">{shortName}</div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${pct}%` }} />
            </div>
            <span className="bar-val">{m.count}</span>
          </div>
        );
      })}
    </div>
  );
}
