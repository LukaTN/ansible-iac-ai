/* =============================================================
   panel/ui.js — right-panel tab switching + collapse.

   `init()` wires the header tabs, the collapse button, and the
   two footer shortcut buttons in the threads sidebar. Flipping
   tabs also closes any active docs SSE connection.
   ============================================================= */

import { $, $$, delegate } from '../util/dom.js';
import { panelStore, closeDocsStream } from './state.js';
import { loadOverview } from './overview.js';
import {
  docsLoadStatus, docsLoadRollback, docsLoadSessions,
  docsCheckUpdates, docsRescrapeChanged,
  docsRestore, docsClearLog,
} from './docs.js';

function setTab(which) {
  panelStore.set({ tab: which });
  $$('.side-tab').forEach((b) => b.classList.remove('active'));
  const tabBtn = document.querySelector(`.side-tab[data-tab="${which}"]`);
  if (tabBtn) tabBtn.classList.add('active');
  $$('.side-pane').forEach((p) => p.classList.remove('active'));
  $('side-' + which)?.classList.add('active');

  if (which === 'docs') {
    docsLoadStatus();
    docsLoadRollback();
    docsLoadSessions();
  } else {
    // Leaving the Docs tab → drop any active SSE stream.
    closeDocsStream();
  }
  if (which === 'stats') loadOverview();
}

export function toggleRightPanel() {
  const panel = $('side-panel');
  if (!panel) return;
  panel.classList.toggle('collapsed');
  panelStore.set({ collapsed: panel.classList.contains('collapsed') });
  if (panelStore.get().collapsed) closeDocsStream();
}

export function togglePanel(which) {
  $('side-panel')?.classList.remove('collapsed');
  panelStore.set({ collapsed: false });
  setTab(which);
}

export function init() {
  // Side tab header (Stats / Docs / collapse chevron).
  delegate(document.querySelector('.side-tabs'), {
    'side-tab-stats'    : () => setTab('stats'),
    'side-tab-docs'     : () => setTab('docs'),
    'toggle-right-panel': toggleRightPanel,
  });

  // Docs panel buttons (live in #side-docs).
  delegate($('side-docs'), {
    'docs-check'    : docsCheckUpdates,
    'docs-rescrape' : docsRescrapeChanged,
    'docs-rollback-refresh': docsLoadRollback,
    'docs-status-refresh'  : docsLoadStatus,
    'docs-sessions-refresh': docsLoadSessions,
    'docs-clear-log': docsClearLog,
    'docs-restore'  : (el) => docsRestore(el.dataset.filename),
  });

  // Top-bar chat panel toggle.
  delegate(document.querySelector('.chat-topbar'), {
    'toggle-right-panel': toggleRightPanel,
  });
}
