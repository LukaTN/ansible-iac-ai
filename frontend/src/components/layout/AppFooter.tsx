import { usePanel } from '@/app/providers/PanelProvider';
import { useSocket } from '@/app/providers/SocketProvider';
import { BookIcon } from '@/components/ui/Icons';

/**
 * Persistent status bar below the main area: real-time connection, docs
 * index health, and composer shortcuts. Slim by design — it never steals
 * vertical space from the conversation.
 */
export function AppFooter() {
  const { connected } = useSocket();
  const { ragStatus } = usePanel();

  const ragReady = ragStatus?.available && (ragStatus.chunks ?? 0) > 0;

  return (
    <footer className="app-footer">
      <div className="app-footer-group">
        <span
          className={`socket-indicator ${connected ? 'connected' : 'disconnected'}`}
          aria-hidden
        />
        <span role="status">{connected ? 'Live' : 'Reconnecting'}</span>
      </div>

      <div className="app-footer-group app-footer-rag" title="Knowledge base index">
        <BookIcon size={11} />
        <span>
          {ragReady
            ? `${ragStatus.chunks.toLocaleString()} chunks`
            : 'Index offline'}
        </span>
      </div>

      <div className="app-footer-spacer" />

      <div className="app-footer-group app-footer-keys" aria-hidden>
        <span className="app-footer-key">
          <kbd className="kbd">Enter</kbd> send
        </span>
        <span className="app-footer-key">
          <kbd className="kbd">Shift</kbd> + <kbd className="kbd">Enter</kbd> new line
        </span>
      </div>

      <div className="app-footer-group app-footer-mark">AnsibleAI · Grounded IaC</div>
    </footer>
  );
}
