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
import type { ChatMessage, Thread } from '@/lib/types';
import { ApiError, api } from '@/lib/api';
import { useSocket } from '@/app/providers/SocketProvider';
import { getSocket, type GenerationFailed, type GenerationProgress } from '@/lib/socket';

const AWAITING_REPLY_FALLBACK = 'Planning and consulting docs...';

// Polling fallback for a turn whose completion event never arrives.
const WATCHDOG_INTERVAL_MS = 5_000;
// Above the worker's hard time limit, so the poll always outlives the job
// it is watching rather than giving up on one that is still running.
const WATCHDOG_MAX_MS = 30 * 60_000;

export { AWAITING_REPLY_FALLBACK };

function threadAwaitingReply(messages: ChatMessage[]): boolean {
  return messages.length > 0 && messages[messages.length - 1].role === 'user';
}

interface ChatContextValue {
  currentId: number | null;
  title: string;
  messages: ChatMessage[];
  isPending: boolean;
  awaitingReplyIds: ReadonlySet<number>;
  openThread: (id: number) => Promise<void>;
  newThread: () => void;
  sendMessage: (text: string) => Promise<void>;
  stopGeneration: () => Promise<void>;
  suggestText: string | null;
  setSuggestText: (text: string | null) => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function ChatProvider({
  children,
  onSent,
}: {
  children: ReactNode;
  onSent?: (info: { assignedId: number; visible: boolean }) => Promise<void>;
}) {
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [title, setTitle] = useState('New chat');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggestText, setSuggestText] = useState<string | null>(null);
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(() => new Set());
  const [awaitingReplyIds, setAwaitingReplyIds] = useState<Set<number>>(() => new Set());
  const [hidePendingOnNewView, setHidePendingOnNewView] = useState(false);
  const { generationProgress } = useSocket();
  const currentIdRef = useRef(currentId);
  const inFlightRef = useRef<{
    originThreadId: number | null;
    knownThreadId: number | null;
  } | null>(null);
  const stopRequestedRef = useRef(false);
  // threadId -> polling timer, one per turn we are still waiting on.
  const watchdogsRef = useRef(new Map<number, ReturnType<typeof setInterval>>());
  currentIdRef.current = currentId;

  const pendingKey = (threadId: number | null) => String(threadId ?? 'new');

  const addAwaitingReply = useCallback((threadId: number) => {
    setAwaitingReplyIds((prev) => new Set(prev).add(threadId));
  }, []);

  const removeAwaitingReply = useCallback((threadId: number) => {
    setAwaitingReplyIds((prev) => {
      if (!prev.has(threadId)) return prev;
      const next = new Set(prev);
      next.delete(threadId);
      return next;
    });
  }, []);

  const isThreadGenerating = useCallback(
    (threadId: number) =>
      pendingKeys.has(String(threadId)) ||
      generationProgress.has(threadId) ||
      awaitingReplyIds.has(threadId),
    [pendingKeys, generationProgress, awaitingReplyIds],
  );

  const isPending = useMemo(() => {
    if (currentId != null) {
      return isThreadGenerating(currentId);
    }
    // New-chat view: only show loading for an in-flight send started here
    // before the user navigated away (New chat / another thread).
    if (hidePendingOnNewView) return false;
    const inflight = inFlightRef.current;
    if (!inflight || inflight.originThreadId !== null) return false;
    if (pendingKeys.has('new')) return true;
    const kid = inflight.knownThreadId;
    return kid != null && isThreadGenerating(kid);
  }, [currentId, hidePendingOnNewView, isThreadGenerating, pendingKeys]);

  const refreshThread = useCallback(async (id: number) => {
    const data = await api.threads.open(id);
    if (currentIdRef.current !== id) return;
    setTitle(data.title || 'Chat');
    const msgs = data.messages || [];
    setMessages(msgs);
    if (threadAwaitingReply(msgs)) addAwaitingReply(id);
    else removeAwaitingReply(id);
  }, [addAwaitingReply, removeAwaitingReply]);

  const stopWatchdog = useCallback((threadId: number) => {
    const timer = watchdogsRef.current.get(threadId);
    if (timer === undefined) return;
    clearInterval(timer);
    watchdogsRef.current.delete(threadId);
  }, []);

  /**
   * A turn is over, however it ended: stop watching, stop showing it as
   * pending, and release the in-flight slot so Stop no longer targets it.
   *
   * `sendMessage` cannot do this in a `finally` any more — it returns
   * while the worker is still running — so every terminal path funnels
   * through here instead.
   */
  const settleThread = useCallback(
    (threadId: number) => {
      stopWatchdog(threadId);
      removeAwaitingReply(threadId);
      setPendingKeys((prev) => {
        if (!prev.has(String(threadId))) return prev;
        const next = new Set(prev);
        next.delete(String(threadId));
        return next;
      });
      if (inFlightRef.current?.knownThreadId === threadId) {
        inFlightRef.current = null;
      }
    },
    [removeAwaitingReply, stopWatchdog],
  );

  /**
   * Poll /api/chat/status until the turn is no longer running.
   *
   * Socket.IO normally reports completion, but the answer now arrives
   * entirely out-of-band: the POST returns before the agent has started.
   * A dropped socket, a proxy that blocks WebSocket upgrades, or a lost
   * event would otherwise leave the composer disabled with no way out.
   */
  const startWatchdog = useCallback(
    (threadId: number) => {
      if (watchdogsRef.current.has(threadId)) return;
      const startedAt = Date.now();

      const timer = setInterval(() => {
        if (Date.now() - startedAt > WATCHDOG_MAX_MS) {
          settleThread(threadId);
          void refreshThread(threadId).catch(() => {});
          return;
        }
        void api.chat
          .status(threadId)
          .then((status) => {
            if (status.running) return;
            settleThread(threadId);
            return refreshThread(threadId);
          })
          .catch(() => {
            // Server unreachable. Keep polling: it may come back, and the
            // deadline above guarantees this stops eventually.
          });
      }, WATCHDOG_INTERVAL_MS);

      watchdogsRef.current.set(threadId, timer);
    },
    [refreshThread, settleThread],
  );

  // Intervals outlive the render that created them; unmounting mid-generation
  // (logout, navigation) must not leave them polling forever.
  useEffect(() => {
    const watchdogs = watchdogsRef.current;
    return () => {
      watchdogs.forEach((timer) => clearInterval(timer));
      watchdogs.clear();
    };
  }, []);

  useEffect(() => {
    const socket = getSocket();
    const onThreadUpserted = (thread: Thread) => {
      if (inFlightRef.current?.originThreadId !== null) return;
      inFlightRef.current = {
        originThreadId: null,
        knownThreadId: thread.id,
      };
      setPendingKeys((prev) => new Set(prev).add(String(thread.id)));
      addAwaitingReply(thread.id);
      // User may have hit Stop before the thread id was known.
      if (stopRequestedRef.current) {
        void api.chat.cancel(thread.id).catch(() => {});
      }
    };
    const onGenerationProgress = (progress: GenerationProgress) => {
      addAwaitingReply(progress.thread_id);
    };
    // The worker emits this last on every path — answered, cancelled,
    // timed out, or failed — so it is the one signal that always clears
    // the pending state and pulls the persisted reply.
    const onGenerationComplete = (payload: { thread_id: number }) => {
      settleThread(payload.thread_id);
      void refreshThread(payload.thread_id);
    };
    const onGenerationFailed = (payload: GenerationFailed) => {
      settleThread(payload.thread_id);
    };
    const onGenerationCancelled = (payload: { thread_id: number }) => {
      settleThread(payload.thread_id);
      void refreshThread(payload.thread_id);
    };

    socket.on('thread_upserted', onThreadUpserted);
    socket.on('generation_progress', onGenerationProgress);
    socket.on('generation_complete', onGenerationComplete);
    socket.on('generation_failed', onGenerationFailed);
    socket.on('generation_cancelled', onGenerationCancelled);
    return () => {
      socket.off('thread_upserted', onThreadUpserted);
      socket.off('generation_progress', onGenerationProgress);
      socket.off('generation_complete', onGenerationComplete);
      socket.off('generation_failed', onGenerationFailed);
      socket.off('generation_cancelled', onGenerationCancelled);
    };
  }, [addAwaitingReply, refreshThread, settleThread]);

  const markPending = useCallback((threadId: number | null) => {
    setPendingKeys((prev) => new Set(prev).add(pendingKey(threadId)));
  }, []);

  const clearPending = useCallback(
    (originThreadId: number | null, assignedId: number | null) => {
      setPendingKeys((prev) => {
        const next = new Set(prev);
        next.delete(pendingKey(originThreadId));
        if (assignedId != null) next.delete(pendingKey(assignedId));
        return next;
      });
    },
    [],
  );

  const openThread = useCallback(async (id: number) => {
    setHidePendingOnNewView(false);
    try {
      const data = await api.threads.open(id);
      const msgs = data.messages || [];
      setCurrentId(id);
      setTitle(data.title || 'Chat');
      setMessages(msgs);
      if (threadAwaitingReply(msgs)) addAwaitingReply(id);
      else removeAwaitingReply(id);
    } catch (e) {
      console.error(e);
    }
  }, [addAwaitingReply, removeAwaitingReply]);

  const newThread = useCallback(() => {
    setHidePendingOnNewView(true);
    setCurrentId(null);
    setTitle('New chat');
    setMessages([]);
  }, []);

  /**
   * Ask the worker to stop.
   *
   * There is nothing local to abort any more: the HTTP request that
   * started the turn returned within milliseconds, and the work is
   * happening in another process. All we can do is set the flag and let
   * the agent notice it at its next check point, which is why the UI is
   * cleared here rather than waiting for a confirmation.
   */
  const stopGeneration = useCallback(async () => {
    const inflight = inFlightRef.current;
    const threadId =
      inflight?.knownThreadId ??
      inflight?.originThreadId ??
      currentIdRef.current;
    // If the thread id is not known yet, the thread_upserted handler
    // issues the cancel as soon as the server assigns one.
    stopRequestedRef.current = true;

    if (threadId == null) {
      clearPending(inflight?.originThreadId ?? null, null);
      return;
    }

    try {
      await api.chat.cancel(threadId);
    } catch (e) {
      console.error('cancel failed', e);
    }
    settleThread(threadId);
    clearPending(inflight?.originThreadId ?? null, threadId);
  }, [clearPending, settleThread]);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isPending) return;

      const originThreadId = currentId;
      const tmpId = `tmp-${Date.now()}`;
      const now = new Date().toISOString();
      stopRequestedRef.current = false;

      setHidePendingOnNewView(false);
      setMessages((prev) => [
        ...prev,
        { id: tmpId, role: 'user', content: trimmed, ts: now },
      ]);
      inFlightRef.current = { originThreadId, knownThreadId: originThreadId };
      markPending(originThreadId);
      if (originThreadId != null) addAwaitingReply(originThreadId);

      const originIsVisible = (assignedId: number | null) => {
        const cur = currentIdRef.current;
        if (originThreadId !== null) return cur === originThreadId;
        return cur === null || cur === assignedId;
      };

      try {
        // 202: the turn is queued, not answered. Everything below is about
        // the *user's* message and the thread; the reply arrives later over
        // the socket, or via the watchdog poll started at the end.
        const data = await api.chat.send(originThreadId, trimmed);
        const assignedId = data.thread.id;
        inFlightRef.current = { originThreadId, knownThreadId: assignedId };

        if (originIsVisible(assignedId)) {
          setCurrentId(assignedId);
          setTitle(data.thread.title || 'Chat');
          setMessages((prev) => {
            const idx = prev.findIndex((m) => m.id === tmpId);
            const next = [...prev];
            if (idx >= 0) next[idx] = data.user_message;
            else next.push(data.user_message);
            return next;
          });
        }

        // Keep the thread marked pending under its real id: the optimistic
        // key was 'new' for a first message, and nothing would clear it.
        markPending(assignedId);
        addAwaitingReply(assignedId);
        if (originThreadId === null) clearPending(null, null);

        // The user pressed Stop between the send and the response.
        if (stopRequestedRef.current) {
          void api.chat.cancel(assignedId).catch(() => {});
        }

        startWatchdog(assignedId);

        if (onSent) {
          await onSent({ assignedId, visible: originIsVisible(assignedId) });
        }
      } catch (e) {
        const assignedId =
          inFlightRef.current?.knownThreadId ??
          inFlightRef.current?.originThreadId ??
          originThreadId;

        clearPending(originThreadId, assignedId);
        if (assignedId != null) settleThread(assignedId);

        console.error(e);
        const err = e as ApiError;
        const body = (err.body as { error?: string })?.error || err.message;
        const networkLike = !(e instanceof ApiError) || err.status == null;
        const msg = networkLike
          ? '⚠ Request failed before the server responded. Check that `py app.py` is running at http://127.0.0.1:5000.'
          : `⚠ ${body || 'Request failed.'}`;
        if (originIsVisible(assignedId)) {
          setMessages((prev) => [
            ...prev,
            {
              id: `err-${Date.now()}`,
              role: 'assistant',
              content: msg,
              ts: new Date().toISOString(),
            },
          ]);
        }
      } finally {
        stopRequestedRef.current = false;
      }
    },
    [
      currentId,
      isPending,
      onSent,
      addAwaitingReply,
      clearPending,
      markPending,
      settleThread,
      startWatchdog,
    ],
  );

  const value = useMemo(
    () => ({
      currentId,
      title,
      messages,
      isPending,
      awaitingReplyIds,
      openThread,
      newThread,
      sendMessage,
      stopGeneration,
      suggestText,
      setSuggestText,
    }),
    [
      currentId,
      title,
      messages,
      isPending,
      awaitingReplyIds,
      openThread,
      newThread,
      sendMessage,
      stopGeneration,
      suggestText,
    ],
  );

  return <ChatContext value={value}>{children}</ChatContext>;
}

export function useChat(): ChatContextValue {
  const ctx = use(ChatContext);
  if (!ctx) throw new Error('useChat must be used within ChatProvider');
  return ctx;
}
