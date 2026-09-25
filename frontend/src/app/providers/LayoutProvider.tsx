import {
  createContext,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { usePanel } from './PanelProvider';

interface LayoutContextValue {
  threadsOpen: boolean;
  openThreads: () => void;
  closeThreads: () => void;
  toggleThreads: () => void;
  closeOverlays: () => void;
}

const LayoutContext = createContext<LayoutContextValue | null>(null);

export function LayoutProvider({ children }: { children: ReactNode }) {
  const { collapsed, collapsePanel, workspaceView, closeDocs } = usePanel();
  const [threadsRequested, setThreadsRequested] = useState(false);
  // Allow the threads drawer while Docs is open so users can pick a chat
  // without being forced out of the corpus view first.
  const threadsOpen = threadsRequested && collapsed;

  const closeThreads = useCallback(() => setThreadsRequested(false), []);

  const openThreads = useCallback(() => {
    collapsePanel();
    setThreadsRequested(true);
  }, [collapsePanel]);

  const toggleThreads = useCallback(() => {
    if (threadsOpen) {
      setThreadsRequested(false);
      return;
    }
    collapsePanel();
    setThreadsRequested(true);
  }, [threadsOpen, collapsePanel]);

  const closeOverlays = useCallback(() => {
    setThreadsRequested(false);
    collapsePanel();
  }, [collapsePanel]);

  useEffect(() => {
    const mq = window.matchMedia('(min-width: 961px)');
    const onChange = () => {
      if (mq.matches) setThreadsRequested(false);
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (threadsOpen) {
        setThreadsRequested(false);
        return;
      }
      if (!collapsed) {
        collapsePanel();
        return;
      }
      if (workspaceView === 'docs') closeDocs();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [threadsOpen, collapsed, collapsePanel, workspaceView, closeDocs]);

  const value = useMemo(
    () => ({ threadsOpen, openThreads, closeThreads, toggleThreads, closeOverlays }),
    [threadsOpen, openThreads, closeThreads, toggleThreads, closeOverlays],
  );

  return <LayoutContext value={value}>{children}</LayoutContext>;
}

export function useLayout() {
  const ctx = use(LayoutContext);
  if (!ctx) throw new Error('useLayout must be used within LayoutProvider');
  return ctx;
}
