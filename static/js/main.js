/* =============================================================
   AnsibleAI frontend — module entry point.

   Pulls in every feature module, wires their init() hooks, and
   kicks off initial data loads. No globals, no legacy bridge —
   every interaction is driven by delegated `data-action`
   listeners registered by each module's init().
   ============================================================= */

import { init as initChat, updateThreadHint } from './chat/actions.js';
import { init as initThreads, loadThreads } from './threads/actions.js';
import { init as initPanelUI } from './panel/ui.js';
import { loadOverview, checkRagStatus } from './panel/overview.js';

function boot() {
  initPanelUI();
  initThreads({ onOverviewRefresh: loadOverview });
  initChat({
    onSent: async () => {
      await loadThreads();
      await loadOverview();
    },
  });

  loadThreads();
  loadOverview();
  checkRagStatus();
  updateThreadHint();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
