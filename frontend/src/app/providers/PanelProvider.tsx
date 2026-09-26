import {
  createContext,
  useCallback,
  use,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useAuth } from '@/app/providers/AuthProvider';
import type { PanelTab, RagStatus, StatsPayload, WorkspaceView } from '@/lib/types';
import { api } from '@/lib/api';
import { isDesignMode } from '@/lib/designMode';
import { startMockDocsStream } from '@/mocks/docsStream';

interface PanelContextValue {
  tab: PanelTab;
  workspaceView: WorkspaceView;
  collapsed: boolean;
  stats: StatsPayload | null;
  ragStatus: RagStatus | null;
  setTab: (tab: PanelTab) => void;
  toggleCollapsed: () => void;
  collapsePanel: () => void;
  openPanel: (tab: PanelTab) => void;
  openDocs: () => void;
  closeDocs: () => void;
  loadOverview: () => Promise<void>;
  checkRagStatus: () => Promise<void>;
  connectDocsStream: (sessionId: number, onLine: (line: string) => void) => void;
  closeDocsStream: () => void;
}

const PanelContext = createContext<PanelContextValue | null>(null);

export function PanelProvider({ children }: { children: ReactNode }) {
  const { user, isAdmin } = useAuth();
  const [tab, setTabState] = useState<PanelTab>('stats');
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>('chat');
  const [collapsed, setCollapsed] = useState(true);
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const [ragStatus, setRagStatus] = useState<RagStatus | null>(null);
  const evtSourceRef = useRef<{ close: () => void } | null>(null);

  const closeDocsStream = useCallback(() => {
    evtSourceRef.current?.close();
    evtSourceRef.current = null;
  }, []);

  const setTab = useCallback(
    (next: PanelTab) => {
      if (next === 'docs') return;
      setTabState(next);
      closeDocsStream();
    },
    [closeDocsStream],
  );

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      if (next) closeDocsStream();
      return next;
    });
  }, [closeDocsStream]);

  const collapsePanel = useCallback(() => {
    setCollapsed(true);
    closeDocsStream();
  }, [closeDocsStream]);

  const closeDocs = useCallback(() => {
    setWorkspaceView('chat');
    setTabState('stats');
    closeDocsStream();
  }, [closeDocsStream]);

  const openDocs = useCallback(() => {
    if (!isAdmin && !isDesignMode()) return;
    setCollapsed(true);
    setWorkspaceView('docs');
    setTabState('docs');
    void api.rag.status().then(setRagStatus).catch(() => {});
  }, [isAdmin]);

  const openPanel = useCallback(
    (which: PanelTab) => {
      if (which === 'docs') {
        openDocs();
        return;
      }
      setWorkspaceView('chat');
      setCollapsed(false);
      setTab('stats');
    },
    [openDocs, setTab],
  );

  const loadOverview = useCallback(async () => {
    try {
      const data = await api.stats.get();
      setStats(data);
    } catch (e) {
      console.error('stats', e);
    }
  }, []);

  const userId = user?.id ?? null;
  const [statsUserId, setStatsUserId] = useState(userId);
  if (userId !== statsUserId) {
    setStatsUserId(userId);
    setStats(null);
  }

  if (!isAdmin && workspaceView === 'docs' && !isDesignMode()) {
    setWorkspaceView('chat');
    setTabState('stats');
  }

  useEffect(() => {
    if (!user) return;
    if (!(workspaceView === 'chat' && tab === 'stats' && !collapsed)) return;
    let cancelled = false;
    api.stats
      .get()
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((e) => console.error('stats', e));
    return () => {
      cancelled = true;
    };
  }, [user, tab, collapsed, workspaceView]);

  useEffect(() => {
    if (!isAdmin && !isDesignMode()) closeDocsStream();
  }, [isAdmin, closeDocsStream]);

  const checkRagStatus = useCallback(async () => {
    try {
      const data = await api.rag.status();
      setRagStatus(data);
    } catch (e) {
      console.warn('RAG status failed', e);
    }
  }, []);

  const connectDocsStream = useCallback(
    (sessionId: number, onLine: (line: string) => void) => {
      closeDocsStream();
      if (isDesignMode()) {
        evtSourceRef.current = startMockDocsStream(onLine);
        return;
      }
      const es = new EventSource(api.docs.streamUrl(sessionId));
      evtSourceRef.current = es;
      es.onmessage = (ev) => {
        const line = (ev.data || '').replaceAll('\\n', '\n');
        if (line.includes('STREAM_END')) {
          closeDocsStream();
          return;
        }
        onLine(line);
      };
      es.addEventListener('ping', () => {});
      es.onerror = () => closeDocsStream();
    },
    [closeDocsStream],
  );

  useEffect(() => () => closeDocsStream(), [closeDocsStream]);

  const value = useMemo(
    () => ({
      tab,
      workspaceView,
      collapsed,
      stats,
      ragStatus,
      setTab,
      toggleCollapsed,
      collapsePanel,
      openPanel,
      openDocs,
      closeDocs,
      loadOverview,
      checkRagStatus,
      connectDocsStream,
      closeDocsStream,
    }),
    [
      tab,
      workspaceView,
      collapsed,
      stats,
      ragStatus,
      setTab,
      toggleCollapsed,
      collapsePanel,
      openPanel,
      openDocs,
      closeDocs,
      loadOverview,
      checkRagStatus,
      connectDocsStream,
      closeDocsStream,
    ],
  );

  return <PanelContext value={value}>{children}</PanelContext>;
}

export function usePanel(): PanelContextValue {
  const ctx = use(PanelContext);
  if (!ctx) throw new Error('usePanel must be used within PanelProvider');
  return ctx;
}
