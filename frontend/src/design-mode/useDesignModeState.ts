import { useSyncExternalStore } from 'react';
import { isDesignMode } from '@/lib/designMode';
import {
  getDesignModeState,
  subscribeDesignMode,
  type DesignModeState,
} from '@/mocks/store';

const IDLE: DesignModeState = {
  persona: 'anonymous',
  screen: 'login',
  chatScene: 'empty',
  docsScene: 'healthy',
  panel: 'collapsed',
  overlay: 'none',
  loginBusy: false,
  loginError: false,
  loginNotice: false,
  sessionExpired: false,
  inviteOnly: false,
  ragReady: true,
};

function subscribe(onStoreChange: () => void): () => void {
  if (!isDesignMode()) return () => {};
  return subscribeDesignMode(onStoreChange);
}

function getSnapshot(): DesignModeState {
  return isDesignMode() ? getDesignModeState() : IDLE;
}

/** Safe to call outside Design Mode — returns a frozen idle snapshot. */
export function useDesignModeState(): DesignModeState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
