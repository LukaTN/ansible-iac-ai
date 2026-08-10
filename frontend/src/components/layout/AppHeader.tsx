import { useChat } from '@/app/providers/ChatProvider';
import { useOnboarding } from '@/app/providers/OnboardingProvider';
import { usePanel } from '@/app/providers/PanelProvider';
import { AccountMenu } from '@/components/auth/AccountMenu';
import { CodeBracketsIcon, HelpIcon, PanelIcon } from '@/components/ui/Icons';

/**
 * Persistent workspace header: brand on the left, the live thread context in
 * the center, and global actions (docs status, guide, panel, account) right.
 * Rendered above the dynamic main area; it never unmounts while signed in.
 */
export function AppHeader() {
  const { title } = useChat();
  const { ragStatus, toggleCollapsed, collapsed } = usePanel();
  const { openGuide } = useOnboarding();

  const ragReady = ragStatus?.available && (ragStatus.chunks ?? 0) > 0;
  const ragLabel = ragReady
    ? `Docs index ready · ${ragStatus.chunks.toLocaleString()} chunks`
    : 'Docs index unavailable';

  return (
    <header className="app-header">
      <div className="app-header-brand">
        <span className="app-header-logo">
          <CodeBracketsIcon size={15} />
        </span>
        <span className="app-header-name">
          <span>Ansible</span>AI
        </span>
        <span className="app-header-tag">IaC Assistant</span>
      </div>

      <div className="app-header-context">
        <div className="app-header-title">{title}</div>
        <div className="app-header-sub">
          Ansible playbook assistant · grounded on indexed module documentation
        </div>
      </div>

      <div className="app-header-actions">
        <div className={`rag-badge${ragReady ? ' ready' : ''}`} title={ragLabel}>
          <span className="live-dot" aria-hidden />
          <span>{ragLabel}</span>
        </div>
        <button
          type="button"
          className="tb-panel-btn"
          onClick={openGuide}
          title="Open the app guide"
          aria-label="Open the app guide"
        >
          <HelpIcon />
        </button>
        <button
          type="button"
          className={`tb-panel-btn${collapsed ? '' : ' active'}`}
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
