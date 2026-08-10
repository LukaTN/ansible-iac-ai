import { useCallback, useEffect, type ReactNode } from 'react';
import { ChatProvider } from './ChatProvider';
import { PanelProvider, usePanel } from './PanelProvider';
import { ThreadsProvider, useThreads } from './ThreadsProvider';
import { SocketProvider } from './SocketProvider';
import type { Thread } from '@/lib/types';

function AppBootstrap({ children }: { children: ReactNode }) {
  const { loadThreads, upsertThread, removeThread, clearItems } = useThreads();
  const { loadOverview, checkRagStatus } = usePanel();

  const onSent = useCallback(async () => {
    await loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    loadThreads();
    loadOverview();
    checkRagStatus();
  }, [loadThreads, loadOverview, checkRagStatus]);

  const handleThreadUpserted = useCallback(
    (thread: Thread) => upsertThread(thread),
    [upsertThread],
  );

  const handleThreadUpdated = useCallback(
    (thread: Thread) => upsertThread(thread),
    [upsertThread],
  );

  const handleThreadDeleted = useCallback(
    (payload: { id: number }) => removeThread(payload.id),
    [removeThread],
  );

  const handleThreadsCleared = useCallback(() => clearItems(), [clearItems]);

  const handleGenerationComplete = useCallback(
    (payload: { thread_id: number; thread: Thread }) => {
      upsertThread(payload.thread);
      loadOverview();
    },
    [upsertThread, loadOverview],
  );

  return (
    <SocketProvider
      onThreadUpserted={handleThreadUpserted}
      onThreadUpdated={handleThreadUpdated}
      onThreadDeleted={handleThreadDeleted}
      onThreadsCleared={handleThreadsCleared}
      onGenerationComplete={handleGenerationComplete}
    >
      <ChatProvider onSent={onSent}>{children}</ChatProvider>
    </SocketProvider>
  );
}

export function AppProvider({ children }: { children: ReactNode }) {
  return (
    <PanelProvider>
      <ThreadsProvider>
        <AppBootstrap>{children}</AppBootstrap>
      </ThreadsProvider>
    </PanelProvider>
  );
}
