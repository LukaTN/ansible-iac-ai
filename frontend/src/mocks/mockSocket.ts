type Handler = (...args: unknown[]) => void;

class MockSocket {
  connected = false;
  private handlers = new Map<string, Set<Handler>>();

  on(event: string, fn: Handler): this {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    set.add(fn);
    return this;
  }

  off(event: string, fn?: Handler): this {
    if (!fn) {
      this.handlers.delete(event);
      return this;
    }
    this.handlers.get(event)?.delete(fn);
    return this;
  }

  /** Fan-out to listeners (used by Design Mode and the mock API). */
  emit(event: string, ...args: unknown[]): this {
    this.handlers.get(event)?.forEach((fn) => {
      fn(...args);
    });
    return this;
  }

  disconnect(): void {
    this.connected = false;
    this.emit('disconnect');
  }

  removeAllListeners(): void {
    this.handlers.clear();
  }

  markConnected(): void {
    this.connected = true;
    this.emit('connect');
  }
}

let instance: MockSocket | null = null;

export function getMockSocket(): MockSocket {
  if (!instance) {
    instance = new MockSocket();
    queueMicrotask(() => instance?.markConnected());
  }
  return instance;
}

export function resetMockSocket(): void {
  instance?.removeAllListeners();
  instance = null;
}

export function emitMockSocket(event: string, payload?: unknown): void {
  getMockSocket().emit(event, payload);
}
