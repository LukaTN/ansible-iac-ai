import { useCallback, useEffect, useRef, useState } from 'react';
import { useChat } from '@/app/providers/ChatProvider';
import { SendIcon, StopIcon } from '@/components/ui/Icons';

export function ChatComposer() {
  const { currentId, isPending, sendMessage, stopGeneration, suggestText, setSuggestText } =
    useChat();
  const [text, setText] = useState('');
  const [stopping, setStopping] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autoGrow = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, []);

  useEffect(() => {
    if (suggestText) {
      setText(suggestText);
      setSuggestText(null);
      requestAnimationFrame(autoGrow);
      textareaRef.current?.focus();
    }
  }, [suggestText, setSuggestText, autoGrow]);

  useEffect(() => {
    if (!isPending) setStopping(false);
  }, [isPending]);

  const handleSend = async () => {
    const trimmed = text.trim();
    if (!trimmed || isPending) return;
    setText('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    await sendMessage(trimmed);
  };

  const handleStop = async () => {
    if (!isPending || stopping) return;
    setStopping(true);
    try {
      await stopGeneration();
    } finally {
      setStopping(false);
    }
  };

  return (
    <div className={`chat-input-wrap${isPending ? ' is-pending' : ''}`}>
      {isPending ? (
        <div className="chat-pending-bar" aria-hidden>
          <span />
        </div>
      ) : null}
      <div className="chat-input-card">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            autoGrow();
          }}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              e.preventDefault();
              if (!isPending) void handleSend();
            }
          }}
          placeholder={
            isPending ? 'Agent is working — hit Stop to cancel…' : 'Describe what you want to automate…'
          }
          rows={2}
          aria-label="Message"
          disabled={isPending}
        />
        <div className="chat-input-row">
          <div className="chat-input-hint">
            {isPending ? (
              <>
                <span className="chat-pending-dot" aria-hidden />
                <span>
                  {stopping
                    ? 'Stopping generation…'
                    : 'Generation in progress — you can stop or switch threads'}
                </span>
              </>
            ) : (
              <>
                <span className="kbd">Ctrl</span>
                <span>+</span>
                <span className="kbd">Enter</span>
                <span className="hint-sep">to send</span>
                <span className="hint-sep">·</span>
                <span>{currentId ? 'Continues this thread' : 'First message opens a new thread'}</span>
              </>
            )}
          </div>
          {isPending ? (
            <button
              type="button"
              className="btn-stop"
              onClick={handleStop}
              disabled={stopping}
              aria-label="Stop generation"
            >
              <StopIcon />
              {stopping ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button
              type="button"
              className="btn-send"
              disabled={!text.trim()}
              onClick={handleSend}
            >
              <SendIcon />
              Send message
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
