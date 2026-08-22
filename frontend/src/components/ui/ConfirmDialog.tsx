import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { TrashIcon } from '@/components/ui/Icons';

export type ConfirmDialogTone = 'danger' | 'default';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmDialogTone;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  detail,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'default',
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    cancelRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !loading) onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener('keydown', onKey);
    };
  }, [open, loading, onCancel]);

  if (!open) return null;

  return createPortal(
    <div className="confirm-overlay" role="presentation" onClick={loading ? undefined : onCancel}>
      <div
        className={`confirm-dialog${tone === 'danger' ? ' confirm-dialog-danger' : ''}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description || detail ? descId : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-dialog-icon" aria-hidden>
          {tone === 'danger' ? <TrashIcon size={18} /> : null}
        </div>

        <div className="confirm-dialog-body" id={description || detail ? descId : undefined}>
          <h2 id={titleId} className="confirm-dialog-title">
            {title}
          </h2>
          {description ? (
            <p className="confirm-dialog-desc">
              {description}
            </p>
          ) : null}
          {detail ? <div className="confirm-dialog-detail">{detail}</div> : null}
        </div>

        <div className="confirm-dialog-actions">
          <button
            ref={cancelRef}
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={onCancel}
            disabled={loading}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`ui-btn ${tone === 'danger' ? 'ui-btn-danger' : 'ui-btn-primary'}`}
            onClick={onConfirm}
            disabled={loading}
          >
            {loading ? 'Deleting…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
