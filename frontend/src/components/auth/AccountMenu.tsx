import { useEffect, useRef, useState } from 'react';
import { useAuth } from '@/app/providers/AuthProvider';
import { useOnboarding } from '@/app/providers/OnboardingProvider';
import { AccountPanel } from '@/components/auth/AccountPanel';
import { BookIcon, ChevronIcon, LogoutIcon, UserIcon } from '@/components/ui/Icons';
import { isDesignMode } from '@/lib/designMode';
import { useDesignModeState } from '@/design-mode/useDesignModeState';
import { setDesignModeState } from '@/mocks/store';

function initials(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export function AccountMenu({ placement = 'up' }: { placement?: 'up' | 'down' }) {
  const { user, isAdmin, logout } = useAuth();
  const { openGuide } = useOnboarding();
  const dm = useDesignModeState();
  const [open, setOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
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

  const showAccount = accountOpen || (isDesignMode() && dm.overlay === 'account');
  const closeAccount = () => {
    setAccountOpen(false);
    if (isDesignMode()) setDesignModeState({ overlay: 'none' });
  };

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
                setAccountOpen(true);
              }}
            >
              <UserIcon />
              Account
            </button>
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

      {showAccount ? <AccountPanel onClose={closeAccount} /> : null}
    </>
  );
}
