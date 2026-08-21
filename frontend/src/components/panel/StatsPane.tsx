import { useEffect } from 'react';
import { usePanel } from '@/app/providers/PanelProvider';
import { ModuleBarChart } from './ModuleBarChart';
import { ValidationBreakdown } from './ValidationBreakdown';

export function StatsPane() {
  const { stats, loadOverview, tab } = usePanel();

  useEffect(() => {
    if (tab === 'stats') loadOverview();
  }, [tab, loadOverview]);

  const total = stats?.total ?? 0;
  const valid = stats?.valid ?? 0;
  const rate = total ? Math.round((valid / total) * 100) : null;

  return (
    <>
      <p className="panel-intro">
        Session analytics for generated playbooks. Open from the sidebar when you need validation trends or module
        usage — not shown in the chat header.
      </p>

      <h3 className="slabel">Playbook outcomes</h3>
      <div className="big-stats">
        <div className="bstat">
          <div className="bstat-val accent">{total}</div>
          <div className="bstat-lbl">Generated</div>
        </div>
        <div className="bstat">
          <div className="bstat-val ok">{valid}</div>
          <div className="bstat-lbl">Passed validation</div>
          {rate != null ? <div className="bstat-sub">{rate}% of generated</div> : null}
        </div>
        <div className="bstat">
          <div className="bstat-val warn">{stats?.warns ?? 0}</div>
          <div className="bstat-lbl">With warnings</div>
        </div>
        <div className="bstat">
          <div className="bstat-val err">{stats?.invalid ?? 0}</div>
          <div className="bstat-lbl">Failed</div>
        </div>
      </div>

      <h3 className="slabel">Outcome breakdown</h3>
      <div className="chart-card">
        <ValidationBreakdown stats={stats} />
      </div>

      <h3 className="slabel">Most used modules</h3>
      <div className="chart-card">
        <ModuleBarChart stats={stats} />
      </div>
    </>
  );
}
