import { useEffect, useState } from 'react';
import { isDesignMode } from '@/lib/designMode';
import {
  seedDesignModeStorage,
  setDesignModeState,
  type DesignChatScene,
  type DesignDocsScene,
  type DesignOverlay,
  type DesignPanel,
  type DesignPersona,
  type DesignScreen,
} from '@/mocks/store';
import './designMode.css';
import { useDesignModeState } from './useDesignModeState';

const PERSONAS: { id: DesignPersona; label: string }[] = [
  { id: 'anonymous', label: 'Anonymous' },
  { id: 'member', label: 'Member' },
  { id: 'admin', label: 'Admin' },
  { id: 'mustChangePassword', label: 'Must change password' },
];

const SCREENS: { id: DesignScreen; label: string }[] = [
  { id: 'login', label: 'Login' },
  { id: 'register', label: 'Register' },
  { id: 'forcePassword', label: 'Force password' },
  { id: 'workspace', label: 'Workspace' },
];

const CHATS: { id: DesignChatScene; label: string }[] = [
  { id: 'empty', label: 'Empty' },
  { id: 'active', label: 'Active' },
  { id: 'generating', label: 'Generating' },
  { id: 'completed', label: 'Completed' },
  { id: 'failed', label: 'Failed' },
  { id: 'cancelled', label: 'Cancelled' },
  { id: 'awaiting', label: 'Awaiting user' },
];

const DOCS: { id: DesignDocsScene; label: string }[] = [
  { id: 'healthy', label: 'Healthy' },
  { id: 'needsUpdate', label: 'Needs update' },
  { id: 'scraping', label: 'Scraping' },
  { id: 'failed', label: 'Failed' },
  { id: 'empty', label: 'Empty KB' },
];

const PANELS: { id: DesignPanel; label: string }[] = [
  { id: 'collapsed', label: 'Collapsed' },
  { id: 'stats', label: 'Analytics' },
  { id: 'docs', label: 'Docs' },
];

const OVERLAYS: { id: DesignOverlay; label: string }[] = [
  { id: 'none', label: 'None' },
  { id: 'account', label: 'Account' },
  { id: 'onboarding', label: 'Onboarding' },
  { id: 'confirmDelete', label: 'Delete chat' },
  { id: 'confirmClear', label: 'Delete all' },
];

function ChipGroup<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (id: T) => void;
}) {
  return (
    <div className="dm-chips">
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          className={value === opt.id ? 'active' : ''}
          onClick={() => onChange(opt.id)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function DesignModeToolbar() {
  const dm = useDesignModeState();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    seedDesignModeStorage();
    document.documentElement.classList.add('design-mode');
    return () => {
      document.documentElement.classList.remove('design-mode');
      document.documentElement.classList.remove('dm-inspector-collapsed');
    };
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle('dm-inspector-collapsed', collapsed);
  }, [collapsed]);

  if (!isDesignMode()) return null;

  const setScreen = (screen: DesignScreen) => {
    if (screen === 'login' || screen === 'register') {
      setDesignModeState({
        screen,
        persona: 'anonymous',
        overlay: 'none',
        loginBusy: false,
        loginError: false,
        loginNotice: false,
      });
      return;
    }
    if (screen === 'forcePassword') {
      setDesignModeState({ screen, persona: 'mustChangePassword', overlay: 'none' });
      return;
    }
    setDesignModeState({
      screen,
      persona: dm.persona === 'anonymous' || dm.persona === 'mustChangePassword' ? 'admin' : dm.persona,
      overlay: 'none',
    });
  };

  const workspacePersona = (): DesignPersona =>
    dm.persona === 'anonymous' || dm.persona === 'mustChangePassword' ? 'admin' : dm.persona;

  const setPersona = (persona: DesignPersona) => {
    if (persona === 'anonymous') {
      setDesignModeState({ persona, screen: 'login', overlay: 'none' });
      return;
    }
    if (persona === 'mustChangePassword') {
      setDesignModeState({ persona, screen: 'forcePassword', overlay: 'none' });
      return;
    }
    setDesignModeState({ persona, screen: 'workspace', overlay: 'none' });
  };

  if (collapsed) {
    return (
      <button
        type="button"
        className="dm-root collapsed"
        onClick={() => setCollapsed(false)}
        title="Open Design Mode"
        aria-label="Open Design Mode"
      >
        <span className="dm-head">
          <span>Design Mode</span>
          <span className="dm-toggle" aria-hidden>
            ◂
          </span>
        </span>
      </button>
    );
  }

  return (
    <aside className="dm-root" aria-label="Design Mode inspector">
      <div className="dm-head">
        <span>Design Mode</span>
        <button type="button" className="dm-toggle" onClick={() => setCollapsed(true)} title="Hide Design Mode">
          ▸
        </button>
      </div>
      <div className="dm-body">
        <section className="dm-section">
          <h3>Pages</h3>
          <ChipGroup value={dm.screen} options={SCREENS} onChange={setScreen} />
        </section>

        <section className="dm-section">
          <h3>User</h3>
          <ChipGroup value={dm.persona} options={PERSONAS} onChange={setPersona} />
        </section>

        <section className="dm-section">
          <h3>Chat</h3>
          <ChipGroup
            value={dm.chatScene}
            options={CHATS}
            onChange={(chatScene) =>
              setDesignModeState({
                chatScene,
                screen: 'workspace',
                overlay: 'none',
                persona: workspacePersona(),
              })
            }
          />
        </section>

        <section className="dm-section">
          <h3>Side panel</h3>
          <ChipGroup
            value={dm.panel}
            options={PANELS}
            onChange={(panel) => setDesignModeState({ panel, screen: 'workspace', persona: workspacePersona() })}
          />
        </section>

        <section className="dm-section">
          <h3>Documentation</h3>
          <ChipGroup
            value={dm.docsScene}
            options={DOCS}
            onChange={(docsScene) =>
              setDesignModeState({
                docsScene,
                panel: 'docs',
                screen: 'workspace',
                overlay: 'none',
                persona: 'admin',
              })
            }
          />
        </section>

        <section className="dm-section">
          <h3>Overlays</h3>
          <ChipGroup
            value={dm.overlay}
            options={OVERLAYS}
            onChange={(overlay) => setDesignModeState({ overlay, screen: 'workspace', persona: workspacePersona() })}
          />
        </section>

        <section className="dm-section">
          <h3>Login extras</h3>
          <ChipGroup
            value={dm.loginBusy ? 'busy' : dm.loginError ? 'error' : dm.loginNotice ? 'notice' : 'idle'}
            options={[
              { id: 'idle', label: 'Idle' },
              { id: 'busy', label: 'Busy' },
              { id: 'error', label: 'Error' },
              { id: 'notice', label: 'Pending approval' },
            ]}
            onChange={(id) =>
              setDesignModeState({
                screen: 'login',
                persona: 'anonymous',
                loginBusy: id === 'busy',
                loginError: id === 'error',
                loginNotice: id === 'notice',
              })
            }
          />
          <div className="dm-chips" style={{ marginTop: 6 }}>
            <button
              type="button"
              className={dm.sessionExpired ? 'active' : ''}
              onClick={() =>
                setDesignModeState({
                  sessionExpired: !dm.sessionExpired,
                  screen: 'login',
                  persona: 'anonymous',
                })
              }
            >
              Session expired
            </button>
            <button
              type="button"
              className={dm.inviteOnly ? 'active' : ''}
              onClick={() => setDesignModeState({ inviteOnly: !dm.inviteOnly, screen: 'login', persona: 'anonymous' })}
            >
              Invite-only
            </button>
            <button
              type="button"
              className={dm.ragReady ? 'active' : ''}
              onClick={() => setDesignModeState({ ragReady: !dm.ragReady })}
            >
              RAG {dm.ragReady ? 'ready' : 'offline'}
            </button>
          </div>
        </section>

        <p className="dm-hint">
          Mock data only. Disable with <code>VITE_DESIGN_MODE=false</code> or run <code>npm run dev</code> without{' '}
          <code>--mode design</code>.
        </p>
      </div>
    </aside>
  );
}
