import { io, Socket } from 'socket.io-client';
import { isDesignMode } from './designMode';
import { getMockSocket, resetMockSocket } from '@/mocks/mockSocket';

export type GenerationStep =
  | 'planning'
  | 'retrieving'
  | 'generating'
  | 'validating'
  | 'synthesizing';

export interface ThoughtEntry {
  id: number;
  step: GenerationStep;
  text: string;
  detail?: string;
  at: number;
}

export interface GenerationProgress {
  thread_id: number;
  step: GenerationStep;
  message: string;
  detail?: string;
}

export interface ThreadGenerationState {
  step: GenerationStep;
  message: string;
  detail?: string;
  thoughts: ThoughtEntry[];
  updatedAt: number;
}

export interface GenerationFailed {
  thread_id: number;
  error: string;
}

let socket: Socket | null = null;

export function getSocket(): Socket {
  if (isDesignMode()) {
    return getMockSocket() as unknown as Socket;
  }
  if (!socket) {
    // Same-origin on purpose, in dev via the Vite proxy: the server only
    // accepts authenticated sockets, and a cross-origin connection would
    // not carry the SameSite session cookie.
    socket = io({
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      withCredentials: true,
      reconnectionAttempts: 10,
      reconnectionDelay: 1000,
    });
  }
  return socket;
}

/**
 * Tear the connection down on logout.
 *
 * The server binds each socket to a per-user room at connect time, so a
 * socket left open after logout would keep receiving the previous user's
 * events.
 */
export function disconnectSocket(): void {
  if (isDesignMode()) {
    resetMockSocket();
    return;
  }
  if (socket) {
    socket.removeAllListeners();
    socket.disconnect();
    socket = null;
  }
}
