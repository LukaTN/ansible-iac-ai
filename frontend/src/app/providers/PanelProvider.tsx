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
import type { PanelTab, RagStatus, StatsPayload } from '@/lib/types';
import { api } from '@/lib/api';

interface PanelContextValue {
  tab: PanelTab;
  collapsed: boolean;
  stats: StatsPayload | null;
  ragStatus: RagStatus | null;
  setTab: (tab: PanelTab) => void;
  toggleCollapsed: () => void;
  openPanel: (tab: PanelTab) => void;
  loadOverview: () => Promise<void>;
  checkRagStatus: () => Promise<void>;
  connectDocsStream: (sessionId: number, onLine: (line: string) => void) => void;
  closeDocsStream: () => void;
}

const PanelContext = createContext<PanelContextValue | null>(null);

export function PanelProvider({ children }: { children: ReactNode }) {
  const [tab, setTabState] = useState<PanelTab>('stats');
  const [collapsed, setCollapsed] = useState(true);
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const [ragStatus, setRagStatus] = useState<RagStatus | null>(null);
  const evtSourceRef = useRef<EventSource | null>(null);

  const closeDocsStream = useCallback(() => {
    evtSourceRef.current?.close();
    evtSourceRef.current = null;
  }, []);

  const setTab = useCallback(
    (next: PanelTab) => {
      setTabState(next);
      if (next !== 'docs') closeDocsStream();
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

  const openPanel = useCallback(
    (which: PanelTab) => {
      setCollapsed(false);
      setTab(which);
    },
    [setTab],
  );

  const loadOverview = useCallback(async () => {
    try {
      const data = await api.stats.get();
      setStats(data);
    } catch (e) {
      console.error('stats', e);
    }
  }, []);

  const checkRagStatus = useCallback(async () => {
    try {
      const data = await api.rag.status();
      setRagStatus(data);
    } catch (e) {
      console.log('RAG status failed', e);
    }
  }, []);

  const connectDocsStream = useCallback(
    (sessionId: number, onLine: (line: string) => void) => {
      closeDocsStream();
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
      collapsed,
      stats,
      ragStatus,
      setTab,
      toggleCollapsed,
      openPanel,
      loadOverview,
      checkRagStatus,
      connectDocsStream,
      closeDocsStream,
    }),
    [
      tab,
      collapsed,
      stats,
      ragStatus,
      setTab,
      toggleCollapsed,
      openPanel,
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
