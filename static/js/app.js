// ── PANELS ──
function showPanel(name, btn) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sb-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  if (btn) btn.classList.add('active');
  if (name === 'history') loadHistory();
  if (name === 'stats')   loadStats();
}

// ── INPUT ──
function setChip(b) {
  document.getElementById('user-input').value = b.textContent.trim();
  document.getElementById('user-input').focus();
}

function clearInput() {
  document.getElementById('user-input').value = '';
  document.getElementById('result').style.display = 'none';
  document.getElementById('err-box').style.display = 'none';
}

// ── LOADING ──
const lmsgs = [
  'Matching intent...',
  'Selecting module...',
  'Building context...',
  'Calling AI model...',
  'Extracting YAML...',
  'Validating output...',
  'Saving to database...'
];
let lIdx = 0, lTimer;

function startLoad() {
  lIdx = 0;
  document.getElementById('load-txt').textContent = lmsgs[0];
  lTimer = setInterval(() => {
    lIdx = (lIdx + 1) % lmsgs.length;
    document.getElementById('load-txt').textContent = lmsgs[lIdx];
  }, 2100);
}

// ── GENERATE ──
async function generate() {
  const input = document.getElementById('user-input').value.trim();
  if (!input) return;

  document.getElementById('gen-btn').disabled = true;
  document.getElementById('result').style.display = 'none';
  document.getElementById('err-box').style.display = 'none';
  document.getElementById('loading').style.display = 'block';
  startLoad();

  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request: input })
    });
    const data = await res.json();
    if (!res.ok || data.error) { showErr(data.error || 'Unknown error'); return; }
    showResult(data, input);
    await loadOverview(); // refresh stats from DB
  } catch (e) {
    showErr('Cannot reach server. Is the backend running?');
  } finally {
    clearInterval(lTimer);
    document.getElementById('loading').style.display = 'none';
    document.getElementById('gen-btn').disabled = false;
  }
}

function showErr(msg) {
  const b = document.getElementById('err-box');
  b.textContent = '⚠  ' + msg;
  b.style.display = 'block';
}

function showResult(data, input) {
  document.getElementById('mod-name').textContent = data.module;
  document.getElementById('code-fname').textContent = data.file || 'playbook.yml';
  document.getElementById('code-out').textContent = data.playbook;

  const v = data.validation;
  const sb = document.getElementById('val-status');
  if (v.errors.length > 0) {
    sb.className = 'val-status bad';
    sb.innerHTML = '❌ &nbsp;INVALID · ' + v.errors.length + ' error(s)';
  } else if (v.warnings.length > 0) {
    sb.className = 'val-status warn';
    sb.innerHTML = '⚠ &nbsp;VALID with warnings';
  } else {
    sb.className = 'val-status ok';
    sb.innerHTML = '✅ &nbsp;All checks passed';
  }

  const vc = document.getElementById('val-checks');
  vc.innerHTML = '';
  v.passed_msgs.forEach(m => vc.innerHTML += `<div class="chk ok"><span>✅</span><span>${m}</span></div>`);
  v.warnings.forEach(m    => vc.innerHTML += `<div class="chk warn"><span>⚠️</span><span>${m}</span></div>`);
  v.errors.forEach(m      => vc.innerHTML += `<div class="chk bad"><span>❌</span><span>${m}</span></div>`);

  const r = document.getElementById('result');
  r.style.display = 'block';
  r.classList.remove('fade-up');
  void r.offsetWidth;
  r.classList.add('fade-up');
  setTimeout(() => r.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}

function copyCode() {
  navigator.clipboard.writeText(document.getElementById('code-out').textContent).then(() => {
    const b = document.querySelector('.copy-btn');
    b.textContent = 'copied!';
    setTimeout(() => b.textContent = 'copy', 1500);
  });
}

// ── HISTORY (from DB) ──
async function loadHistory() {
  const c = document.getElementById('hist-content');
  c.innerHTML = '<div class="hist-empty">Loading...</div>';

  try {
    const res  = await fetch('/history');
    const data = await res.json();

    if (!data.length) {
      c.innerHTML = '<div class="hist-empty">No generations yet — go generate something!</div>';
      return;
    }

    let h = '<div class="hist-grid">';
    data.forEach(e => {
      const d  = new Date(e.ts);
      const ts = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const bc = e.errors > 0 ? 'bad' : e.warnings > 0 ? 'warn' : 'ok';
      const bt = e.errors > 0 ? 'Invalid' : e.warnings > 0 ? 'Warnings' : 'Valid';
      const shortModule = e.module.split('.').pop();
      h += `<div class="hcard" onclick="loadHistEntry(${e.id}, this)" data-id="${e.id}">
        <div class="hcard-top">
          <span class="hbadge ${bc}">${bt}</span>
          <div style="display:flex;align-items:center;gap:.5rem">
            <span class="hmod">${shortModule}</span>
            <button class="hdelete" onclick="deleteEntry(event, ${e.id})" title="Delete">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>
            </button>
          </div>
        </div>
        <div class="hcard-req">${e.request}</div>
        <div class="hcard-meta">
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          ${ts}
        </div>
      </div>`;
    });
    c.innerHTML = h + '</div>';
  } catch (e) {
    c.innerHTML = '<div class="hist-empty">Failed to load history.</div>';
  }
}

async function loadHistEntry(id, card) {
  try {
    const res  = await fetch('/history');
    const data = await res.json();
    const e    = data.find(x => x.id === id);
    if (!e) return;

    document.querySelector('[data-panel="generate"]').click();
    document.getElementById('user-input').value = e.request;
    showResult({
      module: e.module, file: e.file || 'playbook.yml', playbook: e.playbook,
      validation: { is_valid: e.valid, errors: [], warnings: [], passed: 0, passed_msgs: [] }
    }, e.request);
  } catch (err) {
    console.error('Failed to load entry', err);
  }
}

async function deleteEntry(event, id) {
  event.stopPropagation();
  if (!confirm('Delete this entry?')) return;

  await fetch(`/history/${id}`, { method: 'DELETE' });
  await loadHistory();
  await loadOverview();
}

async function clearHistory() {
  if (!confirm('Clear all history?')) return;

  await fetch('/history', { method: 'DELETE' });
  await loadHistory();
  await loadOverview();
}

// ── OVERVIEW (from DB /stats) ──
async function loadOverview() {
  try {
    const res  = await fetch('/stats');
    const data = await res.json();

    document.getElementById('sc-total').textContent = data.total;
    document.getElementById('sc-valid').textContent = data.valid;
    document.getElementById('sc-warn').textContent  = data.warns;
    document.getElementById('sc-err').textContent   = data.invalid;
    document.getElementById('tb-total').textContent = data.total;
    document.getElementById('tb-valid').textContent = data.valid;

    // Module usage bar
    const ml   = document.getElementById('mod-list');
    const mods = data.modules.slice(0, 6);
    const maxC = mods[0]?.count || 1;

    if (!mods.length) {
      ml.innerHTML = '<div class="no-data">No generations yet</div>';
      return;
    }
    ml.innerHTML = mods.map(m => {
      const name = m.module.split('.').pop();
      return `<div class="mod-row">
        <span class="mod-row-name">${name}</span>
        <div class="mod-bar-wrap"><div class="mod-bar" style="width:${Math.round(m.count / maxC * 100)}%"></div></div>
        <span class="mod-cnt">${m.count}</span>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('Stats load failed', e);
  }
}

// ── STATS PANEL (from DB) ──
async function loadStats() {
  try {
    const res  = await fetch('/stats');
    const data = await res.json();

    document.getElementById('bs-total').textContent = data.total;
    document.getElementById('bs-rate').textContent  = data.total ? Math.round(data.valid / data.total * 100) + '%' : '—';
    document.getElementById('bs-rate-sub').textContent = data.total ? `${data.valid}/${data.total} valid` : '';
    document.getElementById('bs-warns').textContent = data.warns;

    const mods = data.modules;
    if (mods.length) {
      document.getElementById('bs-top').textContent     = mods[0].module.split('.').pop();
      document.getElementById('bs-top-sub').textContent = mods[0].count + ' uses';
    }

    // Bar chart
    const bc   = document.getElementById('bc-mods');
    const maxC = mods[0]?.count || 1;
    const cols = ['var(--a1)', 'var(--a2)', 'var(--a3)', 'var(--warn)', 'var(--err)', '#94a3b8'];
    bc.innerHTML = mods.length
      ? mods.slice(0, 7).map((m, i) => {
          const name = m.module.split('.').pop();
          return `<div class="bar-item">
            <div class="bar-lbl">${name}</div>
            <div class="bar-track"><div class="bar-fill" style="width:${Math.round(m.count / maxC * 100)}%;background:${cols[i % cols.length]}"></div></div>
            <span class="bar-val">${m.count}</span>
          </div>`;
        }).join('')
      : '<div class="no-data" style="padding:0">No data yet</div>';

    // Donut
    const onlyWarn = data.warns;
    const clean    = data.valid - onlyWarn;
    drawDonut([
      { v: clean < 0 ? 0 : clean, c: '#4fffb0', l: 'Clean valid' },
      { v: onlyWarn,               c: '#ffb547', l: 'Valid+warnings' },
      { v: data.invalid,           c: '#ff5c5c', l: 'Invalid' }
    ]);
  } catch (e) {
    console.error('Stats failed', e);
  }
}

function drawDonut(segs) {
  const cv  = document.getElementById('donut');
  const ctx = cv.getContext('2d');
  const cx = 55, cy = 55, r = 40, r2 = 25;
  ctx.clearRect(0, 0, 110, 110);
  const tot = segs.reduce((s, i) => s + i.v, 0);

  if (!tot) {
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = '#1e2635'; ctx.lineWidth = 14; ctx.stroke();
  } else {
    let a = -Math.PI / 2;
    segs.forEach(s => {
      if (!s.v) return;
      const sw = (s.v / tot) * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, a, a + sw);
      ctx.closePath(); ctx.fillStyle = s.c; ctx.fill();
      a += sw;
    });
    ctx.beginPath(); ctx.arc(cx, cy, r2, 0, Math.PI * 2);
    ctx.fillStyle = '#0f1117'; ctx.fill();
  }

  document.getElementById('donut-leg').innerHTML = segs.map(s =>
    `<div class="leg-item">
      <div class="leg-dot" style="background:${s.c}"></div>
      ${s.l}
      <b style="color:var(--txt);margin-left:auto;padding-left:.6rem">${s.v}</b>
    </div>`
  ).join('');
}

// ── KEYBOARD ──
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') generate();
});

// ── INIT ──
loadOverview();