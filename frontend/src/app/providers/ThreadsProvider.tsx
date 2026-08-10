import {
  createContext,
  useCallback,
  use,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { Thread } from '@/lib/types';
import { api } from '@/lib/api';

interface ThreadsContextValue {
  items: Thread[];
  filter: string;
  setFilter: (filter: string) => void;
  filteredItems: Thread[];
  loadThreads: () => Promise<void>;
  deleteThread: (id: number) => Promise<void>;
  clearAllThreads: () => Promise<void>;
  upsertThread: (thread: Thread) => void;
  removeThread: (id: number) => void;
  clearItems: () => void;
}

const ThreadsContext = createContext<ThreadsContextValue | null>(null);

export function ThreadsProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Thread[]>([]);
  const [filter, setFilter] = useState('');

  const loadThreads = useCallback(async () => {
    try {
      const data = await api.threads.list();
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('threads', e);
    }
  }, []);

  const upsertThread = useCallback((thread: Thread) => {
    setItems((prev) => {
      const idx = prev.findIndex((t) => t.id === thread.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], ...thread };
        next.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
        return next;
      }
      return [thread, ...prev];
    });
  }, []);

  const removeThread = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const clearItems = useCallback(() => {
    setItems([]);
  }, []);

  const deleteThread = useCallback(
    async (id: number) => {
      try {
        await api.threads.delete(id);
      } catch (e) {
        console.error(e);
      }
    },
    [],
  );

  const clearAllThreads = useCallback(async () => {
    try {
      await api.threads.clear();
    } catch (e) {
      console.error(e);
    }
  }, []);

  const filteredItems = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((t) => (t.title || '').toLowerCase().includes(q));
  }, [items, filter]);

  const value = useMemo(
    () => ({
      items,
      filter,
      setFilter,
      filteredItems,
      loadThreads,
      deleteThread,
      clearAllThreads,
      upsertThread,
      removeThread,
      clearItems,
    }),
    [items, filter, filteredItems, loadThreads, deleteThread, clearAllThreads, upsertThread, removeThread, clearItems],
  );

  return <ThreadsContext value={value}>{children}</ThreadsContext>;
}

export function useThreads(): ThreadsContextValue {
  const ctx = use(ThreadsContext);
  if (!ctx) throw new Error('useThreads must be used within ThreadsProvider');
  return ctx;
}
