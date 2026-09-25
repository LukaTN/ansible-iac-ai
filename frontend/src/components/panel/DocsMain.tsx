import { useAuth } from '@/app/providers/AuthProvider';
import { usePanel } from '@/app/providers/PanelProvider';
import { DocsPane } from './DocsPane';

/**
 * Full-workspace docs & corpus management. Replaces the chat column for
 * administrators; members never reach this view.
 */
export function DocsMain() {
  const { isAdmin } = useAuth();
  const { closeDocs } = usePanel();

  if (!isAdmin) return null;

  return (
    <main className="docs-workspace" aria-label="Docs and corpus management">
      <div className="docs-workspace-head">
        <div>
          <h1 className="docs-workspace-title">Docs &amp; corpus</h1>
          <p className="docs-workspace-sub">
            Knowledge-base health, scrape updates, backups, and module coverage used when drafting
            playbooks.
          </p>
        </div>
        <button type="button" className="ui-btn ui-btn-ghost" onClick={closeDocs}>
          Back to chat
        </button>
      </div>
      <div className="docs-workspace-body">
        <DocsPane />
      </div>
    </main>
  );
}
