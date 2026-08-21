import { usePanel } from '@/app/providers/PanelProvider';
import { ChevronIcon } from '@/components/ui/Icons';
import { StatsPane } from './StatsPane';
import { DocsPane } from './DocsPane';

export function SidePanel() {
  const { tab, collapsed, setTab, toggleCollapsed } = usePanel();

  return (
    <aside
      className={`side${collapsed ? ' collapsed' : ''}`}
      aria-label="Analytics and documentation"
      aria-hidden={collapsed}
    >
      <div className="side-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'stats'}
          className={`side-tab${tab === 'stats' ? ' active' : ''}`}
          onClick={() => setTab('stats')}
        >
          Analytics
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'docs'}
          className={`side-tab${tab === 'docs' ? ' active' : ''}`}
          onClick={() => setTab('docs')}
        >
          Docs
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
