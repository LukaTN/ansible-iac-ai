import type { AuthUser, ChatMessage, Thread } from '@/lib/types';
import {
  cloneMessages,
  mockAdmin,
  mockAuthConfig,
  mockMember,
  mockTempUser,
  mockThreadSeeds,
} from './data';

export type DesignPersona = 'anonymous' | 'member' | 'admin' | 'mustChangePassword';
export type DesignScreen = 'login' | 'register' | 'forcePassword' | 'workspace';
export type DesignChatScene =
  | 'empty'
  | 'active'
  | 'generating'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'awaiting';
export type DesignDocsScene = 'healthy' | 'needsUpdate' | 'scraping' | 'failed' | 'empty';
export type DesignPanel = 'collapsed' | 'stats' | 'docs';
export type DesignOverlay = 'none' | 'account' | 'onboarding' | 'confirmDelete' | 'confirmClear';

export interface DesignModeState {
  persona: DesignPersona;
  screen: DesignScreen;
  chatScene: DesignChatScene;
  docsScene: DesignDocsScene;
  panel: DesignPanel;
  overlay: DesignOverlay;
  loginBusy: boolean;
  loginError: boolean;
  loginNotice: boolean;
  sessionExpired: boolean;
  inviteOnly: boolean;
  ragReady: boolean;
}

const DEFAULT_STATE: DesignModeState = {
  persona: 'admin',
  screen: 'workspace',
  chatScene: 'completed',
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

type Listener = () => void;

let state: DesignModeState = { ...DEFAULT_STATE };
const listeners = new Set<Listener>();

export function getDesignModeState(): DesignModeState {
  return state;
}

export function setDesignModeState(patch: Partial<DesignModeState>): void {
  state = { ...state, ...patch };
  listeners.forEach((fn) => fn());
}

export function subscribeDesignMode(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function personaToUser(persona: DesignPersona): AuthUser | null {
  if (persona === 'anonymous') return null;
  if (persona === 'admin') return { ...mockAdmin };
  if (persona === 'mustChangePassword') return { ...mockTempUser };
  return { ...mockMember };
}

export function currentMockUser(): AuthUser | null {
  return personaToUser(state.persona);
}


let nextThreadId = 200;
let nextMessageId = 50_000;
let threads: Thread[] = mockThreadSeeds.map((t) => ({
  ...t,
  messages: t.messages ? cloneMessages(t.messages) : [],
}));
const running = new Set<number>();

export function resetMockThreads(): void {
  threads = mockThreadSeeds.map((t) => ({
    ...t,
    messages: t.messages ? cloneMessages(t.messages) : [],
  }));
  running.clear();
  nextThreadId = 200;
}

export function listMockThreads(): Thread[] {
  return threads
    .map((t) => ({
      ...t,
      message_count: t.messages?.length ?? t.message_count,
      messages: undefined,
    }))
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
}

export function getMockThread(id: number): Thread | undefined {
  const t = threads.find((x) => x.id === id);
  if (!t) return undefined;
  return { ...t, messages: t.messages ? cloneMessages(t.messages) : [] };
}

export function upsertMockThread(thread: Thread): void {
  const idx = threads.findIndex((t) => t.id === thread.id);
  if (idx >= 0) threads[idx] = { ...threads[idx], ...thread };
  else threads.unshift(thread);
}

export function deleteMockThread(id: number): void {
  threads = threads.filter((t) => t.id !== id);
  running.delete(id);
}

export function clearMockThreads(): void {
  threads = [];
  running.clear();
}

export function appendMockMessage(threadId: number, message: ChatMessage): void {
  const t = threads.find((x) => x.id === threadId);
  if (!t) return;
  t.messages = [...(t.messages ?? []), message];
  t.message_count = t.messages.length;
  t.updated_at = message.ts;
}

export function allocThreadId(): number {
  nextThreadId += 1;
  return nextThreadId;
}

export function allocMessageId(): number {
  nextMessageId += 1;
  return nextMessageId;
}

export function setMockRunning(threadId: number, value: boolean): void {
  if (value) running.add(threadId);
  else running.delete(threadId);
}

export function isMockRunning(threadId: number): boolean {
  return running.has(threadId);
}

export function getAuthConfigForState() {
  return {
    ...mockAuthConfig,
    registration_enabled: !state.inviteOnly,
    app_admin_ui: true,
  };
}

/** Mark mock accounts as having seen onboarding so the tour does not auto-block the workspace. */
export function seedDesignModeStorage(): void {
  try {
    localStorage.setItem(`ansibleai.onboarded.v1.u${mockAdmin.id}`, '1');
    localStorage.setItem(`ansibleai.onboarded.v1.u${mockMember.id}`, '1');
    localStorage.setItem(`ansibleai.onboarded.v1.u${mockTempUser.id}`, '1');
  } catch {
    /* ignore */
  }
}
