import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '@/app/providers/AuthProvider';
import { usePanel } from '@/app/providers/PanelProvider';
import { api } from '@/lib/api';
import { isDesignMode } from '@/lib/designMode';
import type { DocsModuleHealth, RollbackVersion } from '@/lib/types';
import { mockChangedModules, mockFailedScrapeLines, mockScrapeLogLines } from '@/mocks/data';
import { useDesignModeState } from '@/design-mode/useDesignModeState';

function DocCard({
  title,
  subtitle,
  actions,
  wide,
  children,
}: {
  title: string;
  subtitle: string;
  actions?: React.ReactNode;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`doc-card${wide ? ' doc-card-wide' : ''}`}>
      <div className="doc-card-hdr">
        <div>
          <div className="doc-title">{title}</div>
          <div className="doc-sub">{subtitle}</div>
        </div>
        {actions}
      </div>
      <div className="doc-body">{children}</div>
    </div>
  );
}

async function waitSession(sessionId: number) {
  for (let i = 0; i < 240; i++) {
    const data = await api.docs.session(sessionId);
    const st = data.session?.status;
    if (st && st !== 'running') return data;
    await new Promise((r) => setTimeout(r, 500));
  }
  return api.docs.session(sessionId);
}

function pillClass(kind: string) {
  if (kind === 'ok' || kind === 'streaming') return 'pill ok';
  if (kind === 'warn' || kind === 'running') return 'pill warn';
  if (kind === 'bad' || kind === 'failed') return 'pill bad';
  return 'pill idle';
}

export function DocsPane() {
  const { isAdmin } = useAuth();
  const { workspaceView, connectDocsStream, closeDocsStream } = usePanel();
  const dm = useDesignModeState();
  const [generatedAt, setGeneratedAt] = useState('—');
  const [totalMods, setTotalMods] = useState('—');
  const [health, setHealth] = useState<DocsModuleHealth[]>([]);
  const [rollback, setRollback] = useState<RollbackVersion[]>([]);
  const [changelog, setChangelog] = useState<
    { module_slug?: string; slug?: string; diff_summary?: string; health_score?: number }[]
  >([]);
  const [changed, setChanged] = useState<{ slug: string; remote_hash?: string; local_hash?: string }[]>([]);
  const [changedSlugs, setChangedSlugs] = useState<string[]>([]);
  const [terminal, setTerminal] = useState('');
  const [liveStatus, setLiveStatus] = useState('idle');
  const [checking, setChecking] = useState(false);
  const terminalRef = useRef<HTMLDivElement>(null);

  const appendLog = useCallback((line: string) => {
    setTerminal((prev) => (prev ? `${prev}\n${line}` : line));
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const data = await api.docs.status();
      setGeneratedAt(data.kb_metadata?.generated_at || '—');
      setTotalMods(String(data.kb_metadata?.total_modules ?? '—'));
      setHealth((data.module_health || []).slice(0, 12));
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadRollback = useCallback(async () => {
    try {
      const data = await api.docs.rollbackList();
      setRollback(data.versions || []);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      const sessions = await api.docs.sessions(10);
      if (!sessions.length) {
        setChangelog([]);
        return;
      }
      const det = await api.docs.session(sessions[0].id);
      setChangelog(
        (det.session?.summary?.diffs as typeof changelog) ||
          (det.session?.summary?.changed as typeof changelog) ||
          [],
      );
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    if (workspaceView !== 'docs') return;
    loadStatus();
    loadRollback();
    loadSessions();
  }, [workspaceView, loadStatus, loadRollback, loadSessions]);

  useEffect(() => {
    if (!isDesignMode() || workspaceView !== 'docs') return;
    const scene = dm.docsScene;
    void loadStatus();
    void loadRollback();
    void loadSessions();
    if (scene === 'needsUpdate') {
      setChanged(mockChangedModules);
      setChangedSlugs(mockChangedModules.map((c) => c.slug));
      setLiveStatus('idle');
      setTerminal('');
    } else if (scene === 'scraping') {
      setChanged(mockChangedModules);
      setChangedSlugs(mockChangedModules.map((c) => c.slug));
      setLiveStatus('streaming');
      setTerminal(mockScrapeLogLines.filter((line) => line !== 'STREAM_END').join('\n'));
    } else if (scene === 'failed') {
      setChanged([]);
      setChangedSlugs([]);
      setLiveStatus('failed');
      setTerminal(mockFailedScrapeLines.filter((line) => line !== 'STREAM_END').join('\n'));
    } else {
      setChanged([]);
      setChangedSlugs([]);
      setLiveStatus(scene === 'empty' ? 'idle' : 'ok');
      setTerminal('');
    }
  }, [dm.docsScene, workspaceView, loadStatus, loadRollback, loadSessions]);

  useEffect(() => {
    const el = terminalRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [terminal]);

  if (!isAdmin && !isDesignMode()) return null;

  const handleCheck = async () => {
    setChecking(true);
    setLiveStatus('running');
    setTerminal('Checking remote docs against local scrape…\n');
    try {
      const data = await api.docs.check();
      connectDocsStream(data.session_id, appendLog);
      const done = await waitSession(data.session_id);
      closeDocsStream();
      const summary = done.session?.summary || {};
      const nextChanged = (summary.changed as typeof changed) || [];
      setChanged(nextChanged);
      setChangedSlugs(nextChanged.map((c) => c.slug).filter(Boolean));
      setLiveStatus(done.session?.status === 'failed' ? 'failed' : 'ok');
      await loadStatus();
    } catch (e) {
      setLiveStatus('failed');
      const err = e as { body?: { error?: string }; message?: string };
      alert(err.body?.error || err.message || 'Check failed');
    } finally {
      setChecking(false);
    }
  };

  const handleRescrape = async () => {
    if (!changedSlugs.length) return;
    setLiveStatus('streaming');
    setTerminal(`Re-scraping ${changedSlugs.length} module(s)…\n`);
    try {
      const data = await api.docs.rescrape(changedSlugs);
      connectDocsStream(data.session_id, appendLog);
      const done = await waitSession(data.session_id);
      closeDocsStream();
      setLiveStatus(done.session?.status === 'failed' ? 'failed' : 'ok');
      setChanged([]);
      setChangedSlugs([]);
      await loadStatus();
      await loadSessions();
      await loadRollback();
    } catch (e) {
      setLiveStatus('failed');
      const err = e as { body?: { error?: string }; message?: string };
      alert(err.body?.error || err.message || 'Re-scrape failed');
    }
  };

  const handleRestore = async (filename: string) => {
    if (!confirm(`Restore ${filename}?`)) return;
    try {
      const data = await api.docs.restore(filename);
      await loadStatus();
      alert(`Restored: ${data.restored}`);
    } catch (e) {
      const err = e as { body?: { error?: string }; message?: string };
      alert(err.body?.error || err.message || 'Restore failed');
    }
  };

  return (
    <>
      <DocCard
        title="Knowledge base"
        subtitle="Compare remote HTML SHA with local scrape. Re-scrape only modified modules."
      >
        <div className="doc-actions">
          <button type="button" className="ui-btn ui-btn-primary btn-sm" disabled={checking} onClick={handleCheck}>
            Check for updates
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-danger btn-sm"
            disabled={!changedSlugs.length}
            onClick={handleRescrape}
          >
            Re-scrape changed
          </button>
        </div>
        <div className="doc-kv">
          <div>
            <span className="kv-k">Generated at</span>
            <span className="kv-v">{generatedAt}</span>
          </div>
          <div>
            <span className="kv-k">Total modules</span>
            <span className="kv-v">{totalMods}</span>
          </div>
        </div>
        <div className="doc-list">
          {!changed.length ? (
            <div className="ui-empty">No update check run yet.</div>
          ) : (
            changed.map((c) => (
              <div key={c.slug} className="doc-row">
                <div className="doc-row-left">
                  <div className="doc-row-title">{c.slug}</div>
                  <div className="doc-row-sub">
                    remote={(c.remote_hash || '').slice(0, 10)}… · local={(c.local_hash || '').slice(0, 10)}…
                  </div>
                </div>
                <span className="pill warn">changed</span>
              </div>
            ))
          )}
        </div>
      </DocCard>

      <DocCard
        title="Backups"
        subtitle="Restore a previous KB version."
        actions={
          <button type="button" className="ui-btn ui-btn-ghost btn-sm" onClick={loadRollback}>
            Refresh
          </button>
        }
      >
        <div className="doc-list">
          {!rollback.length ? (
            <div className="ui-empty">No backups yet.</div>
          ) : (
            rollback.slice(0, 10).map((v) => (
              <div key={v.filename} className="doc-row">
                <div className="doc-row-left">
                  <div className="doc-row-title">{v.filename}</div>
                  <div className="doc-row-sub">
                    {new Date(v.modified_at).toLocaleString()} · {(v.size / 1024).toFixed(1)} KB
                  </div>
                </div>
                <button
                  type="button"
                  className="ui-btn btn-sm btn-restore"
                  onClick={() => handleRestore(v.filename)}
                >
                  Restore
                </button>
              </div>
            ))
          )}
        </div>
      </DocCard>

      <DocCard
        title="Module health"
        subtitle="Based on params, examples, required detection. <70% is flagged."
        actions={
          <button type="button" className="ui-btn ui-btn-ghost btn-sm" onClick={loadStatus}>
            Refresh
          </button>
        }
      >
        <div className="doc-list">
          {!health.length ? (
            <div className="ui-empty">No KB loaded.</div>
          ) : (
            health.map((r) => (
              <div key={r.slug} className="doc-row">
                <div className="doc-row-left">
                  <div className="doc-row-title">{r.slug}</div>
                  <div className="doc-row-sub">
                    params={r.param_count} · examples={r.example_count} · required={r.required_count}
                  </div>
                </div>
                <div className={`score ${r.health_score < 70 ? 'bad' : 'ok'}`}>{r.health_score}%</div>
              </div>
            ))
          )}
        </div>
      </DocCard>

      <DocCard
        title="Changelog"
        subtitle="Auto-generated per-module diffs from the latest re-scrape."
        actions={
          <button type="button" className="ui-btn ui-btn-ghost btn-sm" onClick={loadSessions}>
            Refresh
          </button>
        }
      >
        <div className="doc-list">
          {!changelog.length ? (
            <div className="ui-empty">No sessions yet.</div>
          ) : (
            changelog.map((d, i) => (
              <div key={i} className="doc-row">
                <div className="doc-row-left">
                  <div className="doc-row-title">{d.module_slug || d.slug}</div>
                  <div className="doc-row-sub">{d.diff_summary || 'changed'}</div>
                </div>
                {d.health_score != null && (
                  <div className={`score ${d.health_score < 70 ? 'bad' : 'ok'}`}>{d.health_score}%</div>
                )}
              </div>
            ))
          )}
        </div>
      </DocCard>

      <DocCard
        wide
        title="Scrape log"
        subtitle="Real-time events via SSE."
        actions={
          <div className="doc-actions">
            <span className={pillClass(liveStatus)}>{liveStatus}</span>
            <button type="button" className="ui-btn ui-btn-ghost btn-sm" onClick={() => setTerminal('')}>
              Clear
            </button>
          </div>
        }
      >
        <div className="terminal" ref={terminalRef}>
          {terminal}
        </div>
      </DocCard>
    </>
  );
}
