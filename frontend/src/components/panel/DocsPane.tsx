import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '@/app/providers/AuthProvider';
import { usePanel } from '@/app/providers/PanelProvider';
import { api } from '@/lib/api';
import type { DocsModuleHealth, RollbackVersion } from '@/lib/types';

function DocCard({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="doc-card">
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
  const { tab, connectDocsStream, closeDocsStream } = usePanel();
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
      const diffs = det.session?.summary?.diffs || det.session?.summary?.changed || [];
      setChangelog(
        diffs.slice(0, 10) as {
          module_slug?: string;
          slug?: string;
          diff_summary?: string;
          health_score?: number;
        }[],
      );
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    if (tab === 'docs') {
      loadStatus();
      loadRollback();
      loadSessions();
    }
  }, [tab, loadStatus, loadRollback, loadSessions]);

  useEffect(() => {
    const el = terminalRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [terminal]);

  const handleCheck = async () => {
    setTerminal('');
    setLiveStatus('running');
    setChecking(true);
    setChanged([]);
    try {
      const data = await api.docs.check();
      setLiveStatus('streaming');
      connectDocsStream(data.session_id, appendLog);
      const out = await waitSession(data.session_id);
      const list = out.session?.summary?.changed || [];
      setChanged(list);
      setChangedSlugs(list.map((c) => c.slug));
      setLiveStatus('done');
      closeDocsStream();
      await loadSessions();
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
    if (!confirm(`Re-scrape ${changedSlugs.length} changed module(s)?`)) return;
    setTerminal('');
    setLiveStatus('running');
    try {
      const data = await api.docs.rescrape(changedSlugs);
      setLiveStatus('streaming');
      connectDocsStream(data.session_id, appendLog);
      await waitSession(data.session_id);
      await loadStatus();
      await loadRollback();
      await loadSessions();
      setLiveStatus('done');
      closeDocsStream();
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
      <div className="slabel">Auto-update scheduler</div>
      <DocCard
        title="Documentation index"
        subtitle="Compare remote HTML SHA with local scrape. Re-scrape only modified modules."
      >
        {/* Re-scraping rewrites the knowledge base every user generates
            against, so the server restricts it to administrators. */}
        {isAdmin ? (
          <div className="doc-actions" style={{ marginBottom: '.8rem' }}>
            <button type="button" className="btn-gen-sm" disabled={checking} onClick={handleCheck}>
              Check for updates
            </button>
            <button type="button" className="btn-ghost" disabled={!changedSlugs.length} onClick={handleRescrape}>
              Re-scrape changed
            </button>
          </div>
        ) : (
          <div className="no-data" style={{ marginBottom: '.8rem' }}>
            Only administrators can refresh the knowledge base.
          </div>
        )}
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
            <div className="no-data">No update check run yet.</div>
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

      <div className="slabel">Rollback</div>
      <DocCard
        title="Backups"
        subtitle="Restore a previous KB version."
        actions={
          <button type="button" className="btn-ghost" onClick={loadRollback}>
            Refresh
          </button>
        }
      >
        <div className="doc-list">
          {!rollback.length ? (
            <div className="no-data">No backups yet.</div>
          ) : (
            rollback.slice(0, 10).map((v) => (
              <div key={v.filename} className="doc-row">
                <div className="doc-row-left">
                  <div className="doc-row-title">{v.filename}</div>
                  <div className="doc-row-sub">
                    {new Date(v.modified_at).toLocaleString()} · {(v.size / 1024).toFixed(1)} KB
                  </div>
                </div>
                {isAdmin ? (
                  <button type="button" className="btn-ghost" onClick={() => handleRestore(v.filename)}>
                    Restore
                  </button>
                ) : null}
              </div>
            ))
          )}
        </div>
      </DocCard>

      <div className="slabel">Scrape log (live)</div>
      <DocCard
        title="Terminal feed"
        subtitle="Real-time events via SSE."
        actions={
          <div className="doc-actions">
            <span className={pillClass(liveStatus)}>{liveStatus}</span>
            <button type="button" className="btn-ghost" onClick={() => setTerminal('')}>
              Clear
            </button>
          </div>
        }
      >
        <div className="terminal" ref={terminalRef}>
          {terminal}
        </div>
      </DocCard>

      <div className="slabel">Module health</div>
      <DocCard
        title="Scoring"
        subtitle="Based on params, examples, required detection. <70% is flagged."
        actions={
          <button type="button" className="btn-ghost" onClick={loadStatus}>
            Refresh
          </button>
        }
      >
        <div className="doc-list">
          {!health.length ? (
            <div className="no-data">No KB loaded.</div>
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

      <div className="slabel">Changelog (latest re-scrape)</div>
      <DocCard
        title="Diffs"
        subtitle="Auto-generated per-module diffs."
        actions={
          <button type="button" className="btn-ghost" onClick={loadSessions}>
            Refresh
          </button>
        }
      >
        <div className="doc-list">
          {!changelog.length ? (
            <div className="no-data">No sessions yet.</div>
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
    </>
  );
}
