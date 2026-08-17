import { AppProvider } from './providers/AppProvider';
import { AuthProvider, useAuth } from './providers/AuthProvider';
import { OnboardingProvider } from './providers/OnboardingProvider';
import { usePanel } from './providers/PanelProvider';
import { AppHeader } from '@/components/layout/AppHeader';
import { AppFooter } from '@/components/layout/AppFooter';
import { ThreadSidebar } from '@/components/threads/ThreadSidebar';
import { ChatMain } from '@/components/chat/ChatMain';
import { SidePanel } from '@/components/panel/SidePanel';
import { LoginPage } from '@/components/auth/LoginPage';
import { ForcePasswordChange } from '@/components/auth/AccountPanel';
import { OnboardingPage } from '@/components/onboarding/OnboardingPage';

/**
 * Persistent shell: the header and footer never change — only the main
 * body (threads / chat / side panel) is dynamic.
 */
function AppShell() {
  const { collapsed } = usePanel();

  return (
    <div className={`app${collapsed ? '' : ' panel-open'}`}>
      <AppHeader />
      <div className="app-body">
        <ThreadSidebar />
        <ChatMain />
        <SidePanel />
      </div>
      <AppFooter />
    </div>
  );
}

/**
 * Renders the workspace only once a session is confirmed.
 *
 * `AppProvider` is mounted inside the authenticated branch on purpose: it
 * fetches threads and stats on mount, which would 401 for an anonymous
 * visitor. Keeping it out of the tree until login also means it remounts
 * on user change, so no previous user's state can linger.
 */
function AuthGate() {
  const { initializing, user } = useAuth();

  if (initializing) {
    return (
      <div className="auth-screen">
        <div className="auth-boot">
          <span className="auth-spinner" aria-hidden="true" />
          <span>Loading workspace...</span>
        </div>
      </div>
    );
  }

  if (!user) return <LoginPage />;

  if (user.must_change_password) return <ForcePasswordChange />;

  return (
    <AppProvider key={user.id}>
      <OnboardingProvider>
        <AppShell />
        <OnboardingPage />
      </OnboardingProvider>
    </AppProvider>
  );
}

export function App() {
  return (
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  );
}
