import { useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '@/app/providers/AuthProvider';
import { api } from '@/lib/api';
import { formatAuthError } from '@/lib/authErrors';
import type { AuthProfile, AuthUser } from '@/lib/types';
import { CodeBracketsIcon } from '@/components/ui/Icons';

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return '—';
  return dt.toLocaleString();
}

function formatTokens(n: number): string {
  return new Intl.NumberFormat().format(Math.max(0, n));
}

export function AccountPanel({ onClose }: { onClose: () => void }) {
  const { user, applyUser, authConfig } = useAuth();
  const [profile, setProfile] = useState<AuthProfile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const titleId = useId();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.auth.profile();
        if (!cancelled) {
          setProfile(data);
          applyUser(data.user);
        }
      } catch (err) {
        if (!cancelled) setLoadError(formatAuthError(err, 'Could not load your account.'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applyUser]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!user) return null;

  const usage = profile?.usage;
  const activity = profile?.activity;
  const unlimited = usage?.unlimited ?? true;
  const limit = usage?.token_budget_limit ?? 0;
  const used = usage?.token_budget_used ?? 0;
  const remaining = usage?.token_budget_remaining ?? 0;
  const pct = !unlimited && limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const canChange = Boolean(user.can_change_password ?? user.has_password);
  const showAdminBadge = authConfig.app_admin_ui && user.role === 'admin';

  return createPortal(
    <div className="confirm-overlay dossier-overlay" role="presentation" onClick={onClose}>
      <div
        className="dossier"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="dossier-head">
          <h2 id={titleId}>Account</h2>
          <button type="button" className="ui-btn ui-btn-ghost dossier-close" onClick={onClose}>
            Close
          </button>
        </header>

        {loadError ? (
          <div className="auth-alert err" role="alert">
            {loadError}
          </div>
        ) : null}

        <section className="dossier-block">
          <h3>Identity</h3>
          <dl className="dossier-grid">
            <div>
              <dt>Name</dt>
              <dd>{user.display_name || user.email.split('@')[0]}</dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd className="mono">{user.email}</dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd>{showAdminBadge ? 'Administrator' : 'Member'}</dd>
            </div>
            <div>
              <dt>Signed in</dt>
              <dd>{formatWhen(user.last_login_at)}</dd>
            </div>
          </dl>
        </section>

        <section className="dossier-block">
          <h3>Tokens spent</h3>
          <p className="dossier-copy">
            {unlimited
              ? 'Counted for today. This workspace has no daily cap.'
              : 'Counted for today against your daily cap.'}
          </p>
          {!unlimited ? (
            <div className="dossier-meter" aria-label="Token budget used">
              <span style={{ width: `${pct}%` }} />
            </div>
          ) : null}
          <dl className="dossier-grid">
            <div>
              <dt>Today</dt>
              <dd className="mono">{profile ? formatTokens(used) : '…'}</dd>
            </div>
            {unlimited ? null : (
              <>
                <div>
                  <dt>Remaining</dt>
                  <dd className="mono">{formatTokens(remaining)}</dd>
                </div>
                <div>
                  <dt>Daily cap</dt>
                  <dd className="mono">{formatTokens(limit)}</dd>
                </div>
              </>
            )}
          </dl>
        </section>

        <section className="dossier-block">
          <h3>Conversations</h3>
          <dl className="dossier-grid">
            <div>
              <dt>Threads</dt>
              <dd className="mono">{activity ? formatTokens(activity.thread_count) : '…'}</dd>
            </div>
            <div>
              <dt>Last activity</dt>
              <dd>{formatWhen(activity?.last_activity_at)}</dd>
            </div>
          </dl>
        </section>

        {canChange ? (
          <section className="dossier-block">
            <h3>Password</h3>
            <p className="dossier-copy">
              Changing it signs other devices out. You stay signed in here.
            </p>
            <ChangePasswordForm
              onUpdated={(next) => {
                applyUser(next);
              }}
            />
          </section>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

export function ChangePasswordForm({
  onUpdated,
  forced = false,
}: {
  onUpdated?: (user: AuthUser) => void;
  forced?: boolean;
}) {
  const { applyUser } = useAuth();
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
      if (res.user) {
        applyUser(res.user);
        onUpdated?.(res.user);
      }
    } catch (err) {
      setError(formatAuthError(err, 'Could not update the password. Please try again.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="auth-form" onSubmit={submit} noValidate>
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

      <label className="auth-field">
        <span>{forced ? 'Temporary password' : 'Current password'}</span>
        <input
          type="password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          autoComplete="current-password"
          required
          disabled={busy || Boolean(done)}
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
          disabled={busy || Boolean(done)}
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
          disabled={busy || Boolean(done)}
        />
      </label>
      <button type="submit" className="ui-btn ui-btn-primary auth-submit" disabled={busy || Boolean(done)}>
        {busy ? (
          <>
            <span className="auth-spinner" aria-hidden="true" />
            Updating...
          </>
        ) : forced ? (
          'Set password and continue'
        ) : (
          'Update password'
        )}
      </button>
    </form>
  );
}

export function ForcePasswordChange() {
  const { logout } = useAuth();

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="app-header-logo" aria-hidden>
            <CodeBracketsIcon size={15} />
          </span>
          <span className="app-header-name">
            <span>Ansible</span>AI
          </span>
        </div>
        <p className="auth-kicker">First sign-in</p>
        <h1 className="auth-heading">Set your password</h1>
        <p className="auth-sub">
          An administrator created this account with a temporary password. Choose a new one
          before opening the workspace. You will not be sent to another site.
        </p>
        <ChangePasswordForm forced />
        <div className="auth-switch">
          <button type="button" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
