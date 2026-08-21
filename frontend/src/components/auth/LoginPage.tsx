import { useEffect, useRef, useState } from 'react';
import { useAuth } from '@/app/providers/AuthProvider';
import { formatAuthError } from '@/lib/authErrors';
import { isDesignMode } from '@/lib/designMode';
import { CodeBracketsIcon } from '@/components/ui/Icons';
import { useDesignModeState } from '@/design-mode/useDesignModeState';
import { setDesignModeState } from '@/mocks/store';

type Mode = 'login' | 'register';

export function LoginPage() {
  const { login, register, sessionExpired, authConfig } = useAuth();
  const dm = useDesignModeState();
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  const showPassword = authConfig.local_login_enabled;
  const showRegister = authConfig.registration_enabled;
  const inviteOnly = !showRegister;

  useEffect(() => {
    emailRef.current?.focus();
  }, [mode, dm.screen]);

  const switchMode = (next: Mode) => {
    if (isDesignMode()) setDesignModeState({ screen: next, persona: 'anonymous' });
    else setMode(next);
    setError(null);
    setNotice(null);
    setPassword('');
  };

  const isRegister = isDesignMode() ? dm.screen === 'register' : mode === 'register';

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      if (!isRegister) {
        await login(email.trim(), password);
      } else {
        const pending = await register(email.trim(), password, displayName.trim() || undefined);
        if (pending) {
          setNotice(pending);
          setMode('login');
          if (isDesignMode()) setDesignModeState({ screen: 'login', persona: 'anonymous' });
          setPassword('');
        }
      }
    } catch (err) {
      setError(formatAuthError(err));
      setPassword('');
    } finally {
      setBusy(false);
    }
  };

  const showBusy = busy || (isDesignMode() && dm.loginBusy);
  const showError =
    error ||
    (isDesignMode() && dm.loginError ? 'Email or password is incorrect. Check both and try again.' : null);
  const showNotice =
    notice ||
    (isDesignMode() && dm.loginNotice
      ? 'Registration received. An administrator must activate the account before it can be used.'
      : null);

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

        <h1 className="auth-heading">{isRegister ? 'Create your account' : 'Sign in'}</h1>
        <p className="auth-sub">
          {isRegister
            ? 'Your conversations and generated playbooks stay private to your account.'
            : 'Generate production-ready Ansible playbooks from plain English.'}
        </p>

        {sessionExpired && !error ? (
          <div className="auth-alert warn" role="status">
            Your session ended. Please sign in again.
          </div>
        ) : null}

        {showNotice ? (
          <div className="auth-alert ok" role="status">
            {showNotice}
          </div>
        ) : null}

        {showError ? (
          <div className="auth-alert err" role="alert" id="auth-form-error">
            {showError}
          </div>
        ) : null}

        {showPassword || isRegister ? (
          <form className="auth-form" onSubmit={submit} noValidate>
            {isRegister ? (
              <label className="auth-field">
                <span>Display name</span>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Optional"
                  autoComplete="name"
                  maxLength={120}
                  disabled={showBusy}
                />
              </label>
            ) : null}

            <label className="auth-field">
              <span>Email</span>
              <input
                ref={emailRef}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="username"
                aria-invalid={Boolean(showError)}
                aria-describedby={showError ? 'auth-form-error' : undefined}
                required
                maxLength={255}
                disabled={showBusy}
              />
            </label>

            <label className="auth-field">
              <span>Password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isRegister ? 'At least 12 characters' : '••••••••••••'}
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                aria-invalid={Boolean(showError)}
                aria-describedby={showError ? 'auth-form-error' : undefined}
                required
                disabled={showBusy}
              />
            </label>

            {isRegister ? (
              <p className="auth-hint">
                Use a long passphrase. Common passwords and anything based on your email
                are rejected.
              </p>
            ) : null}

            <button type="submit" className="ui-btn ui-btn-primary auth-submit" disabled={showBusy}>
              {showBusy ? (
                <>
                  <span className="auth-spinner" aria-hidden="true" />
                  {isRegister ? 'Creating account...' : 'Signing in...'}
                </>
              ) : isRegister ? (
                'Create account'
              ) : (
                'Sign in'
              )}
            </button>
          </form>
        ) : (
          <div className="auth-alert warn" role="status">
            Password sign-in is not enabled on this server.
          </div>
        )}

        {showRegister ? (
          <div className="auth-switch">
            {isRegister ? (
              <>
                Already have an account?{' '}
                <button type="button" onClick={() => switchMode('login')} disabled={showBusy}>
                  Sign in
                </button>
              </>
            ) : (
              <>
                No account yet?{' '}
                <button type="button" onClick={() => switchMode('register')} disabled={showBusy}>
                  Create one
                </button>
              </>
            )}
          </div>
        ) : null}

        {inviteOnly && !isRegister ? (
          <p className="auth-switch">
            Access is by invitation. An administrator creates your account and sends you a
            temporary password.
          </p>
        ) : null}
      </div>
    </div>
  );
}
