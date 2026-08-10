import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '@/app/providers/AuthProvider';
import { useOnboarding } from '@/app/providers/OnboardingProvider';
import { api } from '@/lib/api';
import { formatAuthError } from '@/lib/authErrors';
import { BookIcon, ChevronIcon, KeyIcon, LogoutIcon } from '@/components/ui/Icons';

function initials(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export function AccountMenu({ placement = 'up' }: { placement?: 'up' | 'down' }) {
  const { user, isAdmin, logout } = useAuth();
  const { openGuide } = useOnboarding();
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!user) return null;

  return (
    <>
      <div className={`account${placement === 'down' ? ' below' : ''}`} ref={wrapRef}>
        <button
          type="button"
          className="account-btn"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="menu"
        >
          <span className="account-avatar">{initials(user.display_name || user.email)}</span>
          <span className="account-id">
            <span className="account-name" title={user.email}>
              {user.display_name || user.email}
            </span>
            <span className="account-role">{isAdmin ? 'Administrator' : 'Member'}</span>
          </span>
          <ChevronIcon open={open} />
        </button>

        {open ? (
          <div className="account-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                openGuide();
              }}
            >
              <BookIcon />
              App guide &amp; tour
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                setChanging(true);
              }}
            >
              <KeyIcon />
              Change password
            </button>
            <button
              type="button"
              role="menuitem"
              className="danger"
              onClick={() => {
                setOpen(false);
                void logout();
              }}
            >
              <LogoutIcon />
              Sign out
            </button>
          </div>
        ) : null}
      </div>

      {changing ? <ChangePasswordDialog onClose={() => setChanging(false)} /> : null}
    </>
  );
}

function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (newPassword !== confirm) {
      setError('The new passwords do not match.');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const res = await api.auth.changePassword(currentPassword, newPassword);
      setDone(res.message ?? 'Password updated.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirm('');
    } catch (err) {
      setError(formatAuthError(err, 'Could not update the password. Please try again.'));
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="confirm-overlay" role="presentation" onClick={busy ? undefined : onClose}>
      <div
        className="confirm-dialog pw-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Change password"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="confirm-dialog-title">Change password</h2>
        <p className="confirm-dialog-desc">
          Other devices will be signed out. You will stay signed in here.
        </p>

        {done ? (
          <div className="auth-alert ok" role="status">
            {done}
          </div>
        ) : null}
        {error ? (
          <div className="auth-alert err" role="alert">
            {error}
          </div>
        ) : null}

        {done ? (
          <div className="confirm-dialog-actions">
            <button type="button" className="confirm-dialog-cancel" onClick={onClose}>
              Close
            </button>
          </div>
        ) : (
          <form className="auth-form" onSubmit={submit} noValidate>
            <label className="auth-field">
              <span>Current password</span>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                autoComplete="current-password"
                required
                disabled={busy}
              />
            </label>
            <label className="auth-field">
              <span>New password</span>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                placeholder="At least 12 characters"
                required
                disabled={busy}
              />
            </label>
            <label className="auth-field">
              <span>Confirm new password</span>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                required
                disabled={busy}
              />
            </label>
            <div className="confirm-dialog-actions">
              <button
                type="button"
                className="confirm-dialog-cancel"
                onClick={onClose}
                disabled={busy}
              >
                Cancel
              </button>
              <button type="submit" className="auth-submit compact" disabled={busy}>
                {busy ? 'Updating...' : 'Update password'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>,
    document.body,
  );
}
