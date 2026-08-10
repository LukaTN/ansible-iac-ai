import { useEffect, useRef, useState } from 'react';
import { useAuth } from '@/app/providers/AuthProvider';
import { formatAuthError } from '@/lib/authErrors';
import { CodeBracketsIcon } from '@/components/ui/Icons';

type Mode = 'login' | 'register';

export function LoginPage() {
  const { login, register, sessionExpired } = useAuth();
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, [mode]);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setNotice(null);
    setPassword('');
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      if (mode === 'login') {
        await login(email.trim(), password);
      } else {
        const pending = await register(email.trim(), password, displayName.trim() || undefined);
        if (pending) {
          setNotice(pending);
          setMode('login');
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

  const isRegister = mode === 'register';

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="threads-logo">
            <CodeBracketsIcon />
          </div>
          <div className="threads-title">
            <span>Ansible</span>AI
          </div>
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

        {notice ? (
          <div className="auth-alert ok" role="status">
            {notice}
          </div>
        ) : null}

        {error ? (
          <div className="auth-alert err" role="alert">
            {error}
          </div>
        ) : null}

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
                disabled={busy}
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
              required
              maxLength={255}
              disabled={busy}
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
              required
              disabled={busy}
            />
          </label>

          {isRegister ? (
            <p className="auth-hint">
              Use a long passphrase. Common passwords and anything based on your email
              are rejected.
            </p>
          ) : null}

          <button type="submit" className="auth-submit" disabled={busy}>
            {busy ? (
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

        <div className="auth-switch">
          {isRegister ? (
            <>
              Already have an account?{' '}
              <button type="button" onClick={() => switchMode('login')} disabled={busy}>
                Sign in
              </button>
            </>
          ) : (
            <>
              No account yet?{' '}
              <button type="button" onClick={() => switchMode('register')} disabled={busy}>
                Create one
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
