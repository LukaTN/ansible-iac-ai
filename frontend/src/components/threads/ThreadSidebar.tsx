import { useState } from 'react';
import { AWAITING_REPLY_FALLBACK, useChat } from '@/app/providers/ChatProvider';
import { usePanel } from '@/app/providers/PanelProvider';
import { useSocket } from '@/app/providers/SocketProvider';
import { useThreads } from '@/app/providers/ThreadsProvider';
import { stepLabel } from '@/lib/generationSteps';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { BookIcon, PlusIcon, SearchIcon, StatsIcon, TrashIcon } from '@/components/ui/Icons';
import { relTime } from '@/lib/time';
import { isDesignMode } from '@/lib/designMode';
import { useDesignModeState } from '@/design-mode/useDesignModeState';
import { setDesignModeState } from '@/mocks/store';

type DeletePrompt =
  | { kind: 'one'; id: number; title: string }
  | { kind: 'all'; count: number };

export function ThreadSidebar() {
  const { currentId, awaitingReplyIds, newThread, openThread } = useChat();
  const { filter, setFilter, filteredItems, items, deleteThread, clearAllThreads } = useThreads();
  const { openPanel, loadOverview } = usePanel();
  const { generationProgress } = useSocket();
  const dm = useDesignModeState();
  const [deletePrompt, setDeletePrompt] = useState<DeletePrompt | null>(null);
  const [deleting, setDeleting] = useState(false);

  const inspectorPrompt: DeletePrompt | null =
    isDesignMode() && dm.overlay === 'confirmDelete' && items[0]
      ? { kind: 'one', id: items[0].id, title: items[0].title || 'New chat' }
      : isDesignMode() && dm.overlay === 'confirmClear' && items.length
        ? { kind: 'all', count: items.length }
        : null;
  const activePrompt = deletePrompt ?? inspectorPrompt;

  const requestDelete = (e: React.MouseEvent, id: number, title: string) => {
    e.stopPropagation();
    setDeletePrompt({ kind: 'one', id, title: title || 'New chat' });
  };

  const requestClearAll = () => {
    if (!items.length) return;
    setDeletePrompt({ kind: 'all', count: items.length });
  };

  const closeDialog = () => {
    if (deleting) return;
    setDeletePrompt(null);
    if (isDesignMode()) setDesignModeState({ overlay: 'none' });
  };

  const confirmDelete = async () => {
    if (!activePrompt || deleting) return;
    setDeleting(true);
    try {
      if (activePrompt.kind === 'one') {
        await deleteThread(activePrompt.id);
        if (currentId === activePrompt.id) newThread();
      } else {
        await clearAllThreads();
        newThread();
        await loadOverview();
      }
      setDeletePrompt(null);
      if (isDesignMode()) setDesignModeState({ overlay: 'none' });
    } finally {
      setDeleting(false);
    }
  };

  const dialogProps =
    activePrompt?.kind === 'one'
      ? {
          title: 'Delete this chat?',
          description: 'This conversation and its playbooks will be permanently removed.',
          detail: activePrompt.title,
          confirmLabel: 'Delete chat',
        }
      : activePrompt?.kind === 'all'
        ? {
            title: 'Delete all chats?',
            description: `You're about to remove ${activePrompt.count} conversation${activePrompt.count === 1 ? '' : 's'}. This cannot be undone.`,
            confirmLabel: 'Delete all',
          }
        : null;

  return (
    <>
      <aside className="threads">
        <div className="threads-hdr">
          <button type="button" className="btn-new" onClick={newThread} title="New chat">
            <PlusIcon />
            New chat
          </button>
        </div>

        <div className="threads-search">
          <SearchIcon />
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search chats..."
            aria-label="Search chats"
          />
        </div>

        <div className="threads-list">
          {!filteredItems.length ? (
            <div className="threads-empty">{filter.trim() ? 'No matches' : 'No chats yet'}</div>
          ) : (
            filteredItems.map((t) => {
              const progress = generationProgress.get(t.id);
              const awaiting = awaitingReplyIds.has(t.id);
              const generating = Boolean(progress || awaiting);
              const progressLabel = progress
                ? progress.detail
                  ? `${stepLabel(progress.step)} · ${progress.detail}`
                  : progress.message
                : awaiting
                  ? AWAITING_REPLY_FALLBACK
                  : '';
              return (
                <button
                  key={t.id}
                  type="button"
                  className={`thread-row${t.id === currentId ? ' active' : ''}${generating ? ' generating' : ''}`}
                  onClick={() => openThread(t.id)}
                >
                  <div className="thread-row-main">
                    <div className="thread-row-title" title={t.title || 'New chat'}>
                      {t.title || 'New chat'}
                    </div>
                    <div className="thread-row-meta">
                      {generating ? (
                        <span className="thread-progress">{progressLabel}</span>
                      ) : (
                        <>
                          {t.message_count} {t.message_count === 1 ? 'message' : 'messages'} · {relTime(t.updated_at)}
                        </>
                      )}
                    </div>
                  </div>
                  <span
                    className="thread-del"
                    title="Delete"
                    role="button"
                    tabIndex={0}
                    onClick={(e) => requestDelete(e, t.id, t.title || 'New chat')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        requestDelete(e as unknown as React.MouseEvent, t.id, t.title || 'New chat');
                      }
                    }}
                  >
                    <TrashIcon size={12} />
                  </span>
                </button>
              );
            })
          )}
        </div>

        <div className="threads-footer">
          <button type="button" className="tfoot-btn" onClick={() => openPanel('stats')} title="Analytics">
            <StatsIcon />
            Analytics
          </button>
          <button type="button" className="tfoot-btn" onClick={() => openPanel('docs')} title="Docs">
            <BookIcon />
            Docs
          </button>
          <button
            type="button"
            className="tfoot-btn danger"
            onClick={requestClearAll}
            title="Delete all my chats"
            disabled={!items.length}
          >
            <TrashIcon />
          </button>
        </div>
      </aside>

      {dialogProps ? (
        <ConfirmDialog
          open={Boolean(activePrompt)}
          tone="danger"
          loading={deleting}
          onCancel={closeDialog}
          onConfirm={confirmDelete}
          {...dialogProps}
        />
      ) : null}
    </>
  );
}
