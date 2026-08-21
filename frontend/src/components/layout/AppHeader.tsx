import { useChat } from '@/app/providers/ChatProvider';
import { useOnboarding } from '@/app/providers/OnboardingProvider';
import { usePanel } from '@/app/providers/PanelProvider';
import { AccountMenu } from '@/components/auth/AccountMenu';
import { CodeBracketsIcon, HelpIcon, MenuIcon, PanelIcon } from '@/components/ui/Icons';
import { useLayout } from '@/app/providers/LayoutProvider';

/**
 * Persistent workspace header: brand on the left, the live thread context in
 * the center, and global actions (docs status, guide, panel, account) right.
 * Rendered above the dynamic main area; it never unmounts while signed in.
 */
export function AppHeader() {
  const { title } = useChat();
  const { ragStatus, toggleCollapsed, collapsed } = usePanel();
  const { openGuide } = useOnboarding();
  const { threadsOpen, toggleThreads } = useLayout();

  const ragReady = ragStatus?.available && (ragStatus.chunks ?? 0) > 0;
  const ragLabel = ragReady
    ? `Docs index ready · ${ragStatus.chunks.toLocaleString()} chunks`
    : 'Docs index unavailable';

  return (
    <header className="app-header">
      <button
        type="button"
        className={`ui-btn ui-btn-icon app-header-nav${threadsOpen ? ' is-active' : ''}`}
        onClick={toggleThreads}
        aria-label={threadsOpen ? 'Hide conversations' : 'Show conversations'}
        aria-expanded={threadsOpen}
        aria-controls="threads-drawer"
      >
        <MenuIcon />
      </button>
      <div className="app-header-brand">
        <span className="app-header-logo" aria-hidden>
          <CodeBracketsIcon size={15} />
        </span>
        <span className="app-header-name">
          <span>Ansible</span>AI
        </span>
      </div>

      <div className="app-header-context">
        <div className="app-header-title">{title}</div>
      </div>

      <div className="app-header-actions">
        <div className={`rag-badge${ragReady ? ' ready' : ''}`} title={ragLabel}>
          <span className="live-dot" aria-hidden />
          <span>{ragReady ? `${ragStatus.chunks.toLocaleString()} docs` : 'Docs offline'}</span>
        </div>
        <button
          type="button"
          className="ui-btn ui-btn-icon"
          onClick={openGuide}
          title="Open the app guide"
          aria-label="Open the app guide"
        >
          <HelpIcon />
        </button>
        <button
          type="button"
          className={`ui-btn ui-btn-icon${collapsed ? '' : ' is-active'}`}
          onClick={toggleCollapsed}
          title={collapsed ? 'Show analytics panel' : 'Hide analytics panel'}
          aria-label={collapsed ? 'Show analytics panel' : 'Hide analytics panel'}
          aria-pressed={!collapsed}
        >
          <PanelIcon />
        </button>
        <AccountMenu placement="down" />
      </div>
    </header>
  );
}
