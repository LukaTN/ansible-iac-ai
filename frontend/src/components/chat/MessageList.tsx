import { useEffect, useRef } from 'react';
import { AWAITING_REPLY_FALLBACK, useChat } from '@/app/providers/ChatProvider';
import { useSocket } from '@/app/providers/SocketProvider';
import { MessageBubble } from './MessageBubble';
import { AgentThinking } from './AgentThinking';
import { WelcomeScreen } from './WelcomeScreen';
import type { ThreadGenerationState } from '@/lib/socket';

export function MessageList() {
  const { currentId, messages, isPending, awaitingReplyIds } = useChat();
  const { generationProgress } = useSocket();
  const feedRef = useRef<HTMLDivElement>(null);

  const progressState: ThreadGenerationState | undefined =
    currentId != null ? generationProgress.get(currentId) : undefined;

  const fallbackState: ThreadGenerationState | undefined =
    !progressState && currentId != null && awaitingReplyIds.has(currentId)
      ? {
          step: 'planning',
          message: AWAITING_REPLY_FALLBACK,
          thoughts: [
            {
              id: 0,
              step: 'planning',
              text: AWAITING_REPLY_FALLBACK,
              at: Date.now(),
            },
          ],
          updatedAt: Date.now(),
        }
      : undefined;

  const thinkingState = progressState ?? fallbackState;

  useEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [messages, isPending, thinkingState?.updatedAt, thinkingState?.thoughts.length]);

  const showWelcome = !messages.length && !isPending;

  return (
    <div ref={feedRef} className="chat-feed">
      {showWelcome && <WelcomeScreen />}
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      {isPending && <AgentThinking state={thinkingState} />}
    </div>
  );
}
