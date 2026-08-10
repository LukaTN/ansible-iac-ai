import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api, primeCsrf, setUnauthorizedHandler } from '@/lib/api';
import { disconnectSocket } from '@/lib/socket';
import type { AuthUser } from '@/lib/types';

interface AuthContextValue {
  /** True until the initial session probe resolves; render nothing user-visible before then. */
  initializing: boolean;
  user: AuthUser | null;
  isAdmin: boolean;
  /** Set when a previously valid session was rejected mid-session. */
  sessionExpired: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<string | null>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [initializing, setInitializing] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);

  /* The session can die server-side at any moment (logout in another tab,
     password change, expiry). Rather than let calls fail one by one, drop
     to the login screen as soon as any request reports 401. */
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser((current) => {
        if (current) setSessionExpired(true);
        return null;
      });
      disconnectSocket();
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const state = await api.auth.me();
        if (!cancelled) setUser(state.user);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setInitializing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    // Force a fresh CSRF token — a stale cookie from a previous session
    // is the usual cause of "CSRF validation failed" on the login screen.
    await primeCsrf(true);
    const state = await api.auth.login(email, password);
    setSessionExpired(false);
    setUser(state.user);
  }, []);

  /** Resolves with a message when the account needs admin approval, else null. */
  const register = useCallback(
    async (email: string, password: string, displayName?: string) => {
      await primeCsrf(true);
      const state = await api.auth.register(email, password, displayName);
      if (state.authenticated && state.user) {
        setSessionExpired(false);
        setUser(state.user);
        return null;
      }
      return state.message ?? 'Registration received. An administrator must activate the account.';
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      // Clear locally even if the request failed, so the browser is not
      // left showing another user's data.
      setUser(null);
      setSessionExpired(false);
      disconnectSocket();
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      initializing,
      user,
      isAdmin: user?.role === 'admin',
      sessionExpired,
      login,
      register,
      logout,
    }),
    [initializing, user, sessionExpired, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
