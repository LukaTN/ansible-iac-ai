import {
  createContext,
  useEffect,
  useRef,
  useState,
  use,
  type ReactNode,
} from 'react';
import {
  getSocket,
  type GenerationFailed,
  type GenerationProgress,
  type ThreadGenerationState,
} from '@/lib/socket';
import type { Thread } from '@/lib/types';

interface SocketContextValue {
  connected: boolean;
  generationProgress: Map<number, ThreadGenerationState>;
}

const SocketContext = createContext<SocketContextValue | null>(null);

export function SocketProvider({
  children,
  onThreadUpserted,
  onThreadUpdated,
  onThreadDeleted,
  onThreadsCleared,
  onGenerationComplete,
}: {
  children: ReactNode;
  onThreadUpserted?: (thread: Thread) => void;
  onThreadUpdated?: (thread: Thread) => void;
  onThreadDeleted?: (payload: { id: number }) => void;
  onThreadsCleared?: () => void;
  onGenerationComplete?: (payload: { thread_id: number; thread: Thread }) => void;
}) {
  const [connected, setConnected] = useState(false);
  const [generationProgress, setGenerationProgress] = useState<
    Map<number, ThreadGenerationState>
  >(() => new Map());

  const callbacksRef = useRef({
    onThreadUpserted,
    onThreadUpdated,
    onThreadDeleted,
    onThreadsCleared,
    onGenerationComplete,
  });
  callbacksRef.current = {
    onThreadUpserted,
    onThreadUpdated,
    onThreadDeleted,
    onThreadsCleared,
    onGenerationComplete,
  };

  useEffect(() => {
    const socket = getSocket();

    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => setConnected(false));

    socket.on('thread_upserted', (thread: Thread) => {
      callbacksRef.current.onThreadUpserted?.(thread);
    });

    socket.on('thread_updated', (thread: Thread) => {
      callbacksRef.current.onThreadUpdated?.(thread);
    });

    socket.on('thread_deleted', (payload: { id: number }) => {
      callbacksRef.current.onThreadDeleted?.(payload);
    });

    socket.on('threads_cleared', () => {
      callbacksRef.current.onThreadsCleared?.();
    });

    socket.on('generation_progress', (progress: GenerationProgress) => {
      setGenerationProgress((prev) => {
        const next = new Map(prev);
        const existing = next.get(progress.thread_id);
        const thoughts = existing?.thoughts ?? [];
        const last = thoughts[thoughts.length - 1];
        const sameAsLast =
          last?.text === progress.message && last?.detail === progress.detail;
        const mergedThoughts = sameAsLast
          ? thoughts
          : [
              ...thoughts,
              {
                id: thoughts.length,
                step: progress.step,
                text: progress.message,
                detail: progress.detail,
                at: Date.now(),
              },
            ];
        next.set(progress.thread_id, {
          step: progress.step,
          message: progress.message,
          detail: progress.detail,
          thoughts: mergedThoughts,
          updatedAt: Date.now(),
        });
        return next;
      });
    });

    socket.on('generation_complete', (payload: { thread_id: number; thread: Thread }) => {
      setGenerationProgress((prev) => {
        const next = new Map(prev);
        next.delete(payload.thread_id);
        return next;
      });
      callbacksRef.current.onGenerationComplete?.(payload);
    });

    socket.on('generation_failed', (payload: GenerationFailed) => {
      setGenerationProgress((prev) => {
        const next = new Map(prev);
        next.delete(payload.thread_id);
        return next;
      });
    });

    socket.on('generation_cancelled', (payload: { thread_id: number }) => {
      setGenerationProgress((prev) => {
        const next = new Map(prev);
        next.delete(payload.thread_id);
        return next;
      });
    });

    return () => {
      socket.off('connect');
      socket.off('disconnect');
      socket.off('thread_upserted');
      socket.off('thread_updated');
      socket.off('thread_deleted');
      socket.off('threads_cleared');
      socket.off('generation_progress');
      socket.off('generation_complete');
      socket.off('generation_failed');
      socket.off('generation_cancelled');
    };
  }, []);

  return (
    <SocketContext value={{ connected, generationProgress }}>
      {children}
    </SocketContext>
  );
}

export function useSocket(): SocketContextValue {
  const ctx = use(SocketContext);
  if (!ctx) throw new Error('useSocket must be used within SocketProvider');
  return ctx;
}
