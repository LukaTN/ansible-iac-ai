import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useAuth } from './AuthProvider';

interface OnboardingContextValue {
  open: boolean;
  /** Reopen the guide on demand (account menu, topbar help button). */
  openGuide: () => void;
  /** Close the guide and remember that this user has seen it. */
  closeGuide: () => void;
}

const OnboardingContext = createContext<OnboardingContextValue | null>(null);

/** Per-user flag so the tour auto-opens once per account, not once per browser. */
const seenKey = (userId: number) => `ansibleai.onboarded.v1.u${userId}`;

function hasSeen(userId: number): boolean {
  try {
    return localStorage.getItem(seenKey(userId)) === '1';
  } catch {
    return false;
  }
}

/**
 * Mounted inside `AppProvider` (which is keyed by user id), so the lazy
 * initial state is evaluated once per signed-in user: the tour auto-opens
 * the first time an account enters the workspace.
 */
export function OnboardingProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(() => (user ? !hasSeen(user.id) : false));

  const openGuide = useCallback(() => setOpen(true), []);

  const closeGuide = useCallback(() => {
    setOpen(false);
    if (!user) return;
    try {
      localStorage.setItem(seenKey(user.id), '1');
    } catch {
      // Private mode etc. — the tour simply reopens next session.
    }
  }, [user]);

  const value = useMemo<OnboardingContextValue>(
    () => ({ open, openGuide, closeGuide }),
    [open, openGuide, closeGuide],
  );

  return <OnboardingContext.Provider value={value}>{children}</OnboardingContext.Provider>;
}

export function useOnboarding(): OnboardingContextValue {
  const ctx = useContext(OnboardingContext);
  if (!ctx) throw new Error('useOnboarding must be used inside <OnboardingProvider>');
  return ctx;
}
