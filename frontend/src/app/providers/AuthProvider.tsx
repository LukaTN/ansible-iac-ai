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
import type { AuthConfig, AuthUser } from '@/lib/types';
import { isDesignMode } from '@/lib/designMode';
import { getDesignModeState, subscribeDesignMode } from '@/mocks/store';

interface AuthContextValue {
  /** True until the initial session probe resolves; render nothing user-visible before then. */
  initializing: boolean;
  user: AuthUser | null;
  isAdmin: boolean;
  /** Public auth capabilities (SSO vs password). Safe defaults if the probe fails. */
  authConfig: AuthConfig;
  /** Set when a previously valid session was rejected mid-session. */
  sessionExpired: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<string | null>;
  logout: () => Promise<void>;
  applyUser: (user: AuthUser | null) => void;
}

const DEFAULT_AUTH_CONFIG: AuthConfig = {
  auth_mode: 'local',
  oidc_enabled: false,
  local_login_enabled: true,
  registration_enabled: true,
  app_admin_ui: true,
  oidc_login_url: null,
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [initializing, setInitializing] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authConfig, setAuthConfig] = useState<AuthConfig>(DEFAULT_AUTH_CONFIG);
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
        const [state, config] = await Promise.all([
          api.auth.me(),
          api.auth.config().catch(() => DEFAULT_AUTH_CONFIG),
        ]);
        if (!cancelled) {
          setUser(state.user);
          setAuthConfig(config);
        }
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

  useEffect(() => {
    if (!isDesignMode()) return;
    return subscribeDesignMode(() => {
      void api.auth.config().then(setAuthConfig).catch(() => {});
      void api.auth
        .me()
        .then((next) => {
          setUser(next.user);
          setSessionExpired(getDesignModeState().sessionExpired);
        })
        .catch(() => setUser(null));
    });
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

  const applyUser = useCallback((next: AuthUser | null) => {
    setUser(next);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      initializing,
      user,
      isAdmin: Boolean(user?.role === 'admin' && (authConfig.app_admin_ui ?? authConfig.auth_mode === 'local')),
      authConfig,
      sessionExpired,
      login,
      register,
      logout,
      applyUser,
    }),
    [initializing, user, authConfig, sessionExpired, login, register, logout, applyUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
