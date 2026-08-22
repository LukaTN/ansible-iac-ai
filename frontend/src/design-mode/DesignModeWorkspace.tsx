import { useEffect } from 'react';
import { useChat } from '@/app/providers/ChatProvider';
import { useOnboarding } from '@/app/providers/OnboardingProvider';
import { usePanel } from '@/app/providers/PanelProvider';
import { MOCK_THREAD, mockGeneratingThoughts } from '@/mocks/data';
import { threadIdForScene } from '@/mocks/mockApi';
import { emitMockSocket } from '@/mocks/mockSocket';
import { setMockRunning } from '@/mocks/store';
import { useDesignModeState } from './useDesignModeState';

/**
 * Applies inspector scenes to the live workspace providers.
 * Mounted only inside the authenticated AppProvider tree.
 */
export function DesignModeWorkspace() {
  const dm = useDesignModeState();
  const { openThread, newThread } = useChat();
  const { collapsePanel, openPanel, loadOverview, checkRagStatus } = usePanel();
  const { openGuide, closeGuide } = useOnboarding();

  useEffect(() => {
    let cancelled = false;
    const scene = dm.chatScene;
    const id = threadIdForScene(scene);

    (async () => {
      Object.values(MOCK_THREAD).forEach((tid) => {
        setMockRunning(tid, false);
        emitMockSocket('generation_failed', { thread_id: tid, error: '' });
      });

      if (scene === 'empty' || id == null) {
        newThread();
        return;
      }

      await openThread(id);
      if (cancelled) return;

      if (scene === 'generating') {
        setMockRunning(id, true);
        for (const thought of mockGeneratingThoughts) {
          emitMockSocket('generation_progress', {
            thread_id: id,
            step: thought.step,
            message: thought.text,
            detail: thought.detail,
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [dm.chatScene, openThread, newThread]);

  useEffect(() => {
    if (dm.panel === 'collapsed') {
      collapsePanel();
      return;
    }
    openPanel(dm.panel);
    if (dm.panel === 'stats') void loadOverview();
    if (dm.panel === 'docs') void checkRagStatus();
  }, [dm.panel, collapsePanel, openPanel, loadOverview, checkRagStatus]);

  useEffect(() => {
    if (dm.overlay === 'onboarding') openGuide();
    else closeGuide();
  }, [dm.overlay, openGuide, closeGuide]);

  return null;
}
