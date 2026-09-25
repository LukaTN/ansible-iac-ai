import { usePanel } from '@/app/providers/PanelProvider';
import { ChevronIcon } from '@/components/ui/Icons';
import { StatsPane } from './StatsPane';

/** Analytics side panel. Docs/corpus management lives in the main workspace. */
export function SidePanel() {
  const { collapsed, toggleCollapsed } = usePanel();

  return (
    <aside
      className={`side${collapsed ? ' collapsed' : ''}`}
      aria-label="Analytics"
      aria-hidden={collapsed}
    >
      <div className="side-tabs" role="tablist">
        <button type="button" role="tab" aria-selected className="side-tab active">
          Analytics
        </button>
        <button
          type="button"
          className="ui-btn ui-btn-icon side-collapse"
          onClick={toggleCollapsed}
          title="Collapse panel"
          aria-label="Collapse panel"
        >
          <ChevronIcon />
        </button>
      </div>

      <div className="side-body">
        <div className="side-pane active">
          <StatsPane />
        </div>
      </div>
    </aside>
  );
}
