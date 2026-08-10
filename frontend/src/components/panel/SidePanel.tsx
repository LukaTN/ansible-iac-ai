import { usePanel } from '@/app/providers/PanelProvider';
import { ChevronIcon } from '@/components/ui/Icons';
import { StatsPane } from './StatsPane';
import { DocsPane } from './DocsPane';

export function SidePanel() {
  const { tab, collapsed, setTab, toggleCollapsed } = usePanel();

  return (
    <aside className={`side${collapsed ? ' collapsed' : ''}`}>
      <div className="side-tabs">
        <button
          type="button"
          className={`side-tab${tab === 'stats' ? ' active' : ''}`}
          onClick={() => setTab('stats')}
        >
          Analytics
        </button>
        <button
          type="button"
          className={`side-tab${tab === 'docs' ? ' active' : ''}`}
          onClick={() => setTab('docs')}
        >
          Docs
        </button>
        <button type="button" className="side-collapse" onClick={toggleCollapsed} title="Collapse">
          <ChevronIcon />
        </button>
      </div>

      <div className="side-body">
        <div className={`side-pane${tab === 'stats' ? ' active' : ''}`}>
          <StatsPane />
        </div>
        <div className={`side-pane${tab === 'docs' ? ' active' : ''}`}>
          <DocsPane />
        </div>
      </div>
    </aside>
  );
}
