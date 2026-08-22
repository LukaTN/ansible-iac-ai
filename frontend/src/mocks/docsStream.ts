import { getMockScrapeLines } from '@/mocks/mockApi';

export type MockStreamHandle = { close: () => void };

/**
 * Replaces EventSource in Design Mode so the docs terminal can fill without Flask SSE.
 */
export function startMockDocsStream(onLine: (line: string) => void): MockStreamHandle {
  const lines = getMockScrapeLines();
  let i = 0;
  const id = window.setInterval(() => {
    if (i >= lines.length) {
      window.clearInterval(id);
      return;
    }
    const line = lines[i];
    i += 1;
    if (line === 'STREAM_END') {
      window.clearInterval(id);
      return;
    }
    onLine(line);
  }, 180);
  return {
    close: () => window.clearInterval(id),
  };
}
