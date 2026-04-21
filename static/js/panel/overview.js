/* =============================================================
   panel/overview.js — Stats tab + top-bar RAG indicator.
   ============================================================= */

import { $, esc } from '../util/dom.js';
import { api } from '../api.js';
import { panelStore } from './state.js';

export async function loadOverview() {
  try {
    const data = await api.stats.get();
    $('tb-total').textContent = data.total;
    $('tb-valid').textContent = data.valid;
    $('bs-total').textContent = data.total;
    $('bs-rate').textContent  = data.total ? Math.round(data.valid / data.total * 100) + '%' : '—';
    $('bs-rate-sub').textContent = data.total ? `${data.valid}/${data.total} valid` : '';
    $('bs-warns').textContent = data.warns || 0;
    $('bs-err').textContent   = data.invalid || 0;

    const bc   = $('bc-mods');
    const mods = data.modules || [];
    const maxC = mods[0]?.count || 1;
    const cols = ['var(--a1)', 'var(--a2)', 'var(--a3)', 'var(--warn)', 'var(--err)', '#94a3b8'];
    bc.innerHTML = mods.length
      ? mods.slice(0, 7).map((m, i) => {
          const name = m.module.split('.').pop();
          return `<div class="bar-item">
            <div class="bar-lbl">${esc(name)}</div>
            <div class="bar-track"><div class="bar-fill" style="width:${Math.round(m.count / maxC * 100)}%;background:${cols[i % cols.length]}"></div></div>
            <span class="bar-val">${m.count}</span>
          </div>`;
        }).join('')
      : '<div class="no-data" style="padding:0">No data yet</div>';

    const onlyWarn = data.warns || 0;
    const clean = (data.valid || 0) - onlyWarn;
    drawDonut([
      { v: clean < 0 ? 0 : clean, c: '#4fffb0', l: 'Clean valid' },
      { v: onlyWarn,              c: '#ffb547', l: 'Valid+warnings' },
      { v: data.invalid || 0,     c: '#ff5c5c', l: 'Invalid' },
    ]);
  } catch (e) { console.error(e); }
}

function drawDonut(segs) {
  const cv = $('donut'); if (!cv) return;
  const ctx = cv.getContext('2d');
  const cx = 55, cy = 55, r = 40, r2 = 25;
  ctx.clearRect(0, 0, 110, 110);
  const tot = segs.reduce((s, i) => s + i.v, 0);
  if (!tot) {
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = '#1e2635'; ctx.lineWidth = 14; ctx.stroke();
  } else {
    let a = -Math.PI / 2;
    segs.forEach((s) => {
      if (!s.v) return;
      const sw = (s.v / tot) * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, r, a, a + sw); ctx.closePath();
      ctx.fillStyle = s.c; ctx.fill(); a += sw;
    });
    ctx.beginPath(); ctx.arc(cx, cy, r2, 0, Math.PI * 2); ctx.fillStyle = '#0f1117'; ctx.fill();
  }
  $('donut-leg').innerHTML = segs.map((s) => `
    <div class="leg-item"><div class="leg-dot" style="background:${s.c}"></div>${s.l}
    <b style="color:var(--txt);margin-left:auto;padding-left:.6rem">${s.v}</b></div>`).join('');
}

export async function checkRagStatus() {
  try {
    const data = await api.rag.status();
    const lbl  = $('rag-status-label');
    if (data.available && data.chunks > 0) {
      panelStore.set({ ragAvailable: true });
      if (lbl) lbl.textContent = `Ready · ${data.chunks.toLocaleString()} chunks`;
    } else {
      panelStore.set({ ragAvailable: false });
      if (lbl) lbl.textContent = 'No index — run rag/pipeline.py --build';
    }
  } catch (e) { console.log('RAG status failed', e); }
}
