import { ApiError } from '@/lib/apiError';
import type {
  AuthProfile,
  AuthState,
  ChatAcceptedResponse,
  ChatJobStatus,
  DocsStatus,
  RagStatus,
  RollbackVersion,
  ScrapeSession,
  StatsPayload,
  Thread,
} from '@/lib/types';
import {
  cannedAssistantReply,
  mockChangedModules,
  mockDocsEmpty,
  mockDocsHealthy,
  mockFailedScrapeLines,
  mockProfileFor,
  mockRagOffline,
  mockRagReady,
  mockRollback,
  mockScrapeLogLines,
  mockScrapeSessions,
  mockStats,
  MOCK_THREAD,
} from './data';
import { emitMockSocket } from './mockSocket';
import {
  allocMessageId,
  allocThreadId,
  appendMockMessage,
  clearMockThreads,
  currentMockUser,
  deleteMockThread,
  getAuthConfigForState,
  getDesignModeState,
  getMockThread,
  isMockRunning,
  listMockThreads,
  setDesignModeState,
  setMockRunning,
  upsertMockThread,
  type DesignPersona,
} from './store';

function delay<T>(value: T, ms = 40): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(value), ms);
  });
}

function fail(message: string, status: number, code: string): never {
  throw new ApiError(message, status, { error: message, code });
}

function userFromEmail(email: string): DesignPersona | null {
  const e = email.trim().toLowerCase();
  if (e === 'designer.admin@example.com' || e === 'admin@example.com') return 'admin';
  if (e === 'temp.designer@example.com' || e === 'temp@example.com') return 'mustChangePassword';
  if (e === 'designer@example.com' || e === 'user@example.com') return 'member';
  if (e.includes('admin')) return 'admin';
  if (e.includes('@')) return 'member';
  return null;
}

let scrapeSessionId = 80;
let scrapeStatus: ScrapeSession = mockScrapeSessions[0];

export function getMockScrapeLines(): string[] {
  const scene = getDesignModeState().docsScene;
  if (scene === 'failed') return [...mockFailedScrapeLines];
  return [...mockScrapeLogLines];
}

export const mockApi = {
  auth: {
    config: () => delay(getAuthConfigForState()),
    me: async (): Promise<AuthState> => {
      const user = currentMockUser();
      if (!user) return delay({ authenticated: false, user: null });
      return delay({ authenticated: true, user });
    },
    login: async (email: string, password: string): Promise<AuthState> => {
      if (!email || !password) fail('Email and password are required.', 400, 'missing_fields');
      if (password === 'wrong-password') fail('Invalid email or password.', 401, 'invalid_credentials');
      const persona = userFromEmail(email);
      if (!persona) fail('Invalid email or password.', 401, 'invalid_credentials');
      setDesignModeState({
        persona,
        screen: persona === 'mustChangePassword' ? 'forcePassword' : 'workspace',
        sessionExpired: false,
      });
      const user = currentMockUser();
      return { authenticated: true, user };
    },
    register: async (
      email: string,
      password: string,
      displayName?: string,
    ): Promise<AuthState> => {
      if (getDesignModeState().inviteOnly) {
        fail('Self-registration is disabled.', 403, 'registration_disabled');
      }
      if (!email || !password) fail('Email and password are required.', 400, 'missing_fields');
      if (email.toLowerCase().startsWith('pending@')) {
        return delay({
          authenticated: false,
          user: null,
          pending_approval: true,
          message:
            'Registration received. An administrator must activate the account before it can be used.',
        });
      }
      setDesignModeState({ persona: 'member', screen: 'workspace' });
      const user = currentMockUser();
      if (user && displayName) user.display_name = displayName;
      return { authenticated: true, user };
    },
    logout: async (): Promise<AuthState> => {
      setDesignModeState({ persona: 'anonymous', screen: 'login' });
      return { authenticated: false, user: null };
    },
    profile: async (): Promise<AuthProfile> => {
      const user = currentMockUser();
      if (!user) fail('Authentication required', 401, 'unauthenticated');
      return delay(mockProfileFor(user, listMockThreads().length));
    },
    changePassword: async (_current: string, newPassword: string) => {
      if (newPassword.length < 12) fail('Password is too short.', 400, 'weak_password');
      const user = currentMockUser();
      if (!user) fail('Authentication required', 401, 'unauthenticated');
      const next = { ...user, must_change_password: false };
      if (getDesignModeState().persona === 'mustChangePassword') {
        setDesignModeState({ persona: 'member', screen: 'workspace' });
      }
      return delay({
        authenticated: true,
        user: next,
        message: 'Password updated. Other sessions were signed out.',
      });
    },
  },

  threads: {
    list: async (): Promise<Thread[]> => delay(listMockThreads()),
    open: async (id: number): Promise<Thread> => {
      const t = getMockThread(id);
      if (!t) fail('Thread not found', 404, 'not_found');
      return delay(t);
    },
    delete: async (id: number): Promise<{ deleted: number }> => {
      deleteMockThread(id);
      emitMockSocket('thread_deleted', { id });
      return delay({ deleted: id });
    },
    clear: async (): Promise<{ cleared: boolean }> => {
      clearMockThreads();
      emitMockSocket('threads_cleared', null);
      return delay({ cleared: true });
    },
    rename: async (id: number, title: string): Promise<Thread> => {
      const t = getMockThread(id);
      if (!t) fail('Thread not found', 404, 'not_found');
      const next = { ...t, title, updated_at: new Date().toISOString(), messages: undefined };
      upsertMockThread({ ...t, title, updated_at: next.updated_at });
      emitMockSocket('thread_updated', next);
      return delay(next);
    },
  },

  chat: {
    send: async (
      threadId: number | null,
      message: string,
    ): Promise<ChatAcceptedResponse> => {
      const now = new Date().toISOString();
      const userMsg = {
        id: allocMessageId(),
        thread_id: threadId ?? 0,
        role: 'user' as const,
        content: message,
        ts: now,
        playbook: null,
        filename: null,
        module: null,
        validation: null,
        module_ref: null,
        rag_meta: null,
        tool_trace: null,
      };
      let thread: Thread;
      if (threadId) {
        const existing = getMockThread(threadId);
        if (!existing) fail('Thread not found', 404, 'not_found');
        appendMockMessage(threadId, { ...userMsg, thread_id: threadId });
        thread = getMockThread(threadId)!;
        thread = { ...thread, messages: undefined };
      } else {
        const id = allocThreadId();
        userMsg.thread_id = id;
        thread = {
          id,
          title: message.slice(0, 50) + (message.length > 50 ? '…' : ''),
          created_at: now,
          updated_at: now,
          message_count: 1,
          messages: [{ ...userMsg }],
        };
        upsertMockThread(thread);
        thread = { ...thread, messages: undefined };
        emitMockSocket('thread_upserted', thread);
      }
      setMockRunning(thread.id, true);
      window.setTimeout(() => {
        const reply = {
          ...cannedAssistantReply,
          id: allocMessageId(),
          thread_id: thread.id,
          ts: new Date().toISOString(),
        };
        appendMockMessage(thread.id, reply);
        setMockRunning(thread.id, false);
        const updated = getMockThread(thread.id);
        emitMockSocket('generation_complete', {
          thread_id: thread.id,
          thread: updated ? { ...updated, messages: undefined } : thread,
        });
      }, 1200);
      return delay(
        {
          job_id: `mock-job-${thread.id}`,
          thread,
          user_message: { ...userMsg, thread_id: thread.id },
        },
        80,
      );
    },
    cancel: async (threadId: number): Promise<{ thread_id: number; cancelling: boolean }> => {
      const was = isMockRunning(threadId);
      setMockRunning(threadId, false);
      emitMockSocket('generation_cancelled', {
        thread_id: threadId,
        error: 'Generation stopped by user.',
      });
      emitMockSocket('generation_failed', {
        thread_id: threadId,
        error: 'Generation stopped by user.',
      });
      return delay({ thread_id: threadId, cancelling: was });
    },
    status: async (threadId: number): Promise<ChatJobStatus> =>
      delay({
        thread_id: threadId,
        running: isMockRunning(threadId),
        cancelling: false,
      }),
  },

  stats: {
    get: async (): Promise<StatsPayload> => delay(mockStats),
  },

  rag: {
    status: async (): Promise<RagStatus> =>
      delay(getDesignModeState().ragReady ? mockRagReady : mockRagOffline),
  },

  docs: {
    status: async (): Promise<DocsStatus> => {
      const scene = getDesignModeState().docsScene;
      if (scene === 'empty') return delay(mockDocsEmpty);
      return delay(mockDocsHealthy);
    },
    check: async (): Promise<{ session_id: number }> => {
      scrapeSessionId += 1;
      scrapeStatus = {
        id: scrapeSessionId,
        triggered_at: new Date().toISOString(),
        status: getDesignModeState().docsScene === 'failed' ? 'failed' : 'success',
        summary: {
          changed: mockChangedModules,
        },
      };
      return delay({ session_id: scrapeSessionId }, 60);
    },
    rescrape: async (_modules: string[]): Promise<{ session_id: number }> => {
      scrapeSessionId += 1;
      scrapeStatus = {
        id: scrapeSessionId,
        triggered_at: new Date().toISOString(),
        status: 'success',
        summary: {
          diffs: mockScrapeSessions[0].summary?.diffs,
        },
      };
      return delay({ session_id: scrapeSessionId }, 60);
    },
    rollbackList: async (): Promise<{ versions: RollbackVersion[] }> => {
      if (getDesignModeState().docsScene === 'empty') return delay({ versions: [] });
      return delay({ versions: mockRollback });
    },
    restore: async (filename: string): Promise<{ restored: string }> => delay({ restored: filename }),
    sessions: async (_limit = 10): Promise<ScrapeSession[]> => {
      if (getDesignModeState().docsScene === 'empty') return delay([]);
      return delay(mockScrapeSessions);
    },
    session: async (id: number): Promise<{ session: ScrapeSession }> =>
      delay({ session: { ...scrapeStatus, id } }),
    streamUrl: (id: number) => `/docs/stream/${id}`,
  },
};

/** Thread id the inspector should open for a given chat scene. */
export function threadIdForScene(
  scene: import('./store').DesignChatScene,
): number | null {
  switch (scene) {
    case 'empty':
      return null;
    case 'active':
      return MOCK_THREAD.k8s;
    case 'generating':
      return MOCK_THREAD.nginx;
    case 'completed':
      return MOCK_THREAD.s3;
    case 'failed':
      return MOCK_THREAD.postgres;
    case 'cancelled':
      return MOCK_THREAD.deploy;
    case 'awaiting':
      return MOCK_THREAD.azure;
    default:
      return null;
  }
}
