// ── PANELS ──
function showPanel(name, btn) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sb-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  if (btn) btn.classList.add('active');
  if (name === 'history') loadHistory();
  if (name === 'stats')   loadStats();
  if (name === 'docs')    { docsLoadStatus(); docsLoadRollback(); docsLoadSessions(); }
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
  'Building context from docs...',
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
  document.getElementById('sources-wrap').style.display = 'none';
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
    await loadOverview();
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

// ── RESULT ──
function showResult(data, input) {
  document.getElementById('mod-name').textContent = data.module;
  document.getElementById('code-fname').textContent = data.file || 'playbook.yml';
  document.getElementById('code-out').textContent = data.playbook;

  // Validation
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

  // Sources panel
  if (data.module_ref) renderSources(data.module_ref, input);

  const r = document.getElementById('result');
  r.style.display = 'block';
  r.classList.remove('fade-up'); void r.offsetWidth; r.classList.add('fade-up');
  setTimeout(() => r.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}

// ── SOURCES PANEL ──
function renderSources(ref, userInput) {
  const wrap = document.getElementById('sources-wrap');
  if (!ref || !ref.found) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = 'block';

  // Update header
  document.getElementById('src-module-name').textContent = ref.module;
  document.getElementById('src-module-sub').textContent =
    `${ref.category?.toUpperCase() || 'K8S'} · ${ref.total_params || '?'} parameters available`;

  // Doc link
  document.getElementById('src-doc-link').href = ref.doc_url;
  document.getElementById('src-doc-link').onclick = (e) => {
    e.preventDefault();
    window.open(ref.doc_url, '_blank');
  };
  document.getElementById('src-doc-name').textContent = ref.module;
  document.getElementById('src-doc-url').textContent  = ref.doc_url;

  // Description
  document.getElementById('src-desc').textContent = ref.description || '—';

  // Required params
  const rp = document.getElementById('src-required-params');
  const rpSection = document.getElementById('src-required-section');
  if (ref.required_params && ref.required_params.length) {
    rpSection.style.display = 'block';
    rp.innerHTML = ref.required_params.map(p => `
      <div class="param-row">
        <span class="param-name">${p.name}</span>
        <span class="param-type">${p.type}</span>
        <span class="param-required">required</span>
        <span class="param-desc">${p.description}</span>
      </div>`).join('');
  } else {
    rpSection.style.display = 'none';
  }

  // Optional params
  const op = document.getElementById('src-optional-params');
  const opSection = document.getElementById('src-optional-section');
  if (ref.optional_params && ref.optional_params.length) {
    opSection.style.display = 'block';
    op.innerHTML = ref.optional_params.map(p => `
      <div class="param-row">
        <span class="param-name">${p.name}</span>
        <span class="param-type">${p.type}</span>
      </div>`).join('');
  } else {
    opSection.style.display = 'none';
  }

  // Intent keywords
  const kw = document.getElementById('src-keywords');
  if (ref.keywords && ref.keywords.length) {
    // Highlight keywords that appear in the user input
    const inputLower = userInput.toLowerCase();
    kw.innerHTML = ref.keywords.map(k => {
      const matched = inputLower.includes(k.toLowerCase());
      return `<span class="kw-chip" style="${matched ? 'border-color:var(--a1);color:var(--a1);background:rgba(79,255,176,.08)' : ''}">${k}</span>`;
    }).join('');
  }

  // Reasoning
  const matched = (ref.keywords || []).filter(k =>
    userInput.toLowerCase().includes(k.toLowerCase())
  );
  document.getElementById('src-reasoning').innerHTML =
    `The AI matched your request to <b>${ref.module}</b> by detecting ` +
    (matched.length
      ? `the keyword${matched.length > 1 ? 's' : ''} <b>${matched.slice(0,3).join(', ')}</b> in your input.`
      : `the module category <b>${ref.category}</b>.`) +
    ` The model used the official Ansible documentation for this module ` +
    `(${ref.total_params || '?'} parameters) to generate the playbook. ` +
    (ref.required_params?.length
      ? `It injected the ${ref.required_params.length} required parameter${ref.required_params.length > 1 ? 's' : ''} into the prompt context.`
      : `This module has no mandatory parameters.`);
}

function toggleSources() {
  const body    = document.getElementById('src-body');
  const chevron = document.getElementById('src-chevron');
  const isOpen  = body.classList.contains('open');
  body.classList.toggle('open', !isOpen);
  chevron.classList.toggle('open', !isOpen);
}

function copyCode() {
  navigator.clipboard.writeText(document.getElementById('code-out').textContent).then(() => {
    const b = document.querySelector('.copy-btn');
    b.textContent = 'copied!';
    setTimeout(() => b.textContent = 'copy', 1500);
  });
}

// ── HISTORY ──
async function loadHistory() {
  const c = document.getElementById('hist-content');
  c.innerHTML = '<div class="hist-empty">Loading...</div>';
  try {
    const res  = await fetch('/history');
    const data = await res.json();
    if (!data.length) { c.innerHTML = '<div class="hist-empty">No generations yet — go generate something!</div>'; return; }
    let h = '<div class="hist-grid">';
    data.forEach(e => {
      const d  = new Date(e.ts);
      const ts = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const bc = e.errors > 0 ? 'bad' : e.warnings > 0 ? 'warn' : 'ok';
      const bt = e.errors > 0 ? 'Invalid' : e.warnings > 0 ? 'Warnings' : 'Valid';
      const shortMod = e.module.split('.').pop();
      h += `<div class="hcard" onclick="loadHistEntry(${e.id})">
        <div class="hcard-top">
          <span class="hbadge ${bc}">${bt}</span>
          <div style="display:flex;align-items:center;gap:.5rem">
            <span class="hmod">${shortMod}</span>
            <button class="hdelete" onclick="deleteEntry(event,${e.id})" title="Delete">
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
  } catch (e) { c.innerHTML = '<div class="hist-empty">Failed to load history.</div>'; }
}

async function loadHistEntry(id) {
  try {
    const res  = await fetch('/history');
    const data = await res.json();
    const e    = data.find(x => x.id === id);
    if (!e) return;
    document.querySelector('[data-panel="generate"]').click();
    document.getElementById('user-input').value = e.request;
    showResult({
      module: e.module, file: e.file || 'playbook.yml', playbook: e.playbook,
      validation: { is_valid: e.valid, errors: [], warnings: [], passed: 0, passed_msgs: [] },
      module_ref: e.module_ref || null
    }, e.request);
  } catch (err) { console.error(err); }
}

async function deleteEntry(event, id) {
  event.stopPropagation();
  if (!confirm('Delete this entry?')) return;
  await fetch(`/history/${id}`, { method: 'DELETE' });
  await loadHistory(); await loadOverview();
}

async function clearHistory() {
  if (!confirm('Clear all history?')) return;
  await fetch('/history', { method: 'DELETE' });
  await loadHistory(); await loadOverview();
}

// ── OVERVIEW ──
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
    const ml   = document.getElementById('mod-list');
    const mods = data.modules.slice(0, 6);
    const maxC = mods[0]?.count || 1;
    if (!mods.length) { ml.innerHTML = '<div class="no-data">No generations yet</div>'; return; }
    ml.innerHTML = mods.map(m => {
      const name = m.module.split('.').pop();
      return `<div class="mod-row">
        <span class="mod-row-name">${name}</span>
        <div class="mod-bar-wrap"><div class="mod-bar" style="width:${Math.round(m.count/maxC*100)}%"></div></div>
        <span class="mod-cnt">${m.count}</span>
      </div>`;
    }).join('');
  } catch(e) { console.error(e); }
}

// ── STATS ──
async function loadStats() {
  try {
    const res  = await fetch('/stats');
    const data = await res.json();
    document.getElementById('bs-total').textContent = data.total;
    document.getElementById('bs-rate').textContent  = data.total ? Math.round(data.valid/data.total*100)+'%' : '—';
    document.getElementById('bs-rate-sub').textContent = data.total ? `${data.valid}/${data.total} valid` : '';
    document.getElementById('bs-warns').textContent = data.warns;
    const mods = data.modules;
    if (mods.length) {
      document.getElementById('bs-top').textContent     = mods[0].module.split('.').pop();
      document.getElementById('bs-top-sub').textContent = mods[0].count + ' uses';
    }
    const bc   = document.getElementById('bc-mods');
    const maxC = mods[0]?.count || 1;
    const cols = ['var(--a1)','var(--a2)','var(--a3)','var(--warn)','var(--err)','#94a3b8'];
    bc.innerHTML = mods.length
      ? mods.slice(0,7).map((m,i) => {
          const name = m.module.split('.').pop();
          return `<div class="bar-item">
            <div class="bar-lbl">${name}</div>
            <div class="bar-track"><div class="bar-fill" style="width:${Math.round(m.count/maxC*100)}%;background:${cols[i%cols.length]}"></div></div>
            <span class="bar-val">${m.count}</span>
          </div>`;
        }).join('')
      : '<div class="no-data" style="padding:0">No data yet</div>';
    const onlyWarn = data.warns, clean = data.valid - onlyWarn;
    drawDonut([
      { v: clean < 0 ? 0 : clean, c: '#4fffb0', l: 'Clean valid' },
      { v: onlyWarn,               c: '#ffb547', l: 'Valid+warnings' },
      { v: data.invalid,           c: '#ff5c5c', l: 'Invalid' }
    ]);
  } catch(e) { console.error(e); }
}

function drawDonut(segs) {
  const cv = document.getElementById('donut'), ctx = cv.getContext('2d');
  const cx=55,cy=55,r=40,r2=25;
  ctx.clearRect(0,0,110,110);
  const tot = segs.reduce((s,i)=>s+i.v,0);
  if (!tot) { ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.strokeStyle='#1e2635';ctx.lineWidth=14;ctx.stroke(); }
  else {
    let a=-Math.PI/2;
    segs.forEach(s=>{if(!s.v)return;const sw=(s.v/tot)*Math.PI*2;ctx.beginPath();ctx.moveTo(cx,cy);ctx.arc(cx,cy,r,a,a+sw);ctx.closePath();ctx.fillStyle=s.c;ctx.fill();a+=sw;});
    ctx.beginPath();ctx.arc(cx,cy,r2,0,Math.PI*2);ctx.fillStyle='#0f1117';ctx.fill();
  }
  document.getElementById('donut-leg').innerHTML=segs.map(s=>`<div class="leg-item"><div class="leg-dot" style="background:${s.c}"></div>${s.l}<b style="color:var(--txt);margin-left:auto;padding-left:.6rem">${s.v}</b></div>`).join('');
}

// ── KEYBOARD ──
document.addEventListener('keydown', e => { if((e.ctrlKey||e.metaKey)&&e.key==='Enter') generate(); });

// ── INIT ──
loadOverview();


// ─────────────────────────────────────────────
//  DOCS MANAGEMENT PANEL
// ─────────────────────────────────────────────

let _docsLastCheck = null;       // { session_id, changed:[...], ... } from /docs/sessions/<id>
let _docsChangedSlugs = [];
let _docsEvtSrc = null;

function docsClearLog() {
  document.getElementById('docs-terminal').textContent = '';
}

function _docsAppendLog(line) {
  const term = document.getElementById('docs-terminal');
  term.textContent += (term.textContent ? '\n' : '') + line;
  term.scrollTop = term.scrollHeight;
}

function _docsSetLiveStatus(txt, kind) {
  const el = document.getElementById('docs-live-status');
  el.textContent = txt;
  el.className = 'pill ' + (kind || 'idle');
}

function _docsConnectStream(sessionId) {
  if (_docsEvtSrc) { try { _docsEvtSrc.close(); } catch(e){} }
  _docsSetLiveStatus('streaming', 'ok');
  const es = new EventSource(`/docs/stream/${sessionId}`);
  _docsEvtSrc = es;
  es.onmessage = (ev) => {
    const line = (ev.data || '').replaceAll('\\n', '\n');
    if (line.includes('STREAM_END')) {
      _docsSetLiveStatus('done', 'idle');
      try { es.close(); } catch(e){}
      return;
    }
    _docsAppendLog(line);
  };
  es.addEventListener('ping', () => {});
  es.onerror = () => {
    _docsSetLiveStatus('disconnected', 'warn');
  };
}

async function docsLoadStatus() {
  try {
    const res = await fetch('/docs/status');
    const data = await res.json();
    document.getElementById('kb-generated-at').textContent = data.kb_metadata?.generated_at || '—';
    document.getElementById('kb-total-mods').textContent = data.kb_metadata?.total_modules ?? '—';

    // health list (worst first)
    const list = document.getElementById('docs-health-list');
    const rows = (data.module_health || []).slice(0, 12);
    if (!rows.length) {
      list.innerHTML = '<div class="no-data">No KB loaded.</div>';
      return;
    }
    list.innerHTML = rows.map(r => {
      const bad = r.health_score < 70;
      return `<div class="doc-row">
        <div class="doc-row-left">
          <div class="doc-row-title">${r.slug}</div>
          <div class="doc-row-sub">params=${r.param_count} · examples=${r.example_count} · required=${r.required_count}</div>
        </div>
        <div class="score ${bad ? 'bad' : 'ok'}">${bad ? '⚠️' : '✅'} ${r.health_score}%</div>
      </div>`;
    }).join('');
  } catch (e) {
    console.error(e);
  }
}

async function docsLoadRollback() {
  try {
    const res = await fetch('/docs/rollback/list');
    const data = await res.json();
    const list = document.getElementById('docs-rollback-list');
    const vers = data.versions || [];
    if (!vers.length) { list.innerHTML = '<div class="no-data">No backups yet.</div>'; return; }
    list.innerHTML = vers.slice(0, 10).map(v => `
      <div class="doc-row">
        <div class="doc-row-left">
          <div class="doc-row-title">${v.filename}</div>
          <div class="doc-row-sub">${new Date(v.modified_at).toLocaleString()} · ${(v.size/1024).toFixed(1)} KB</div>
        </div>
        <button class="btn-ghost" onclick="docsRestore('${v.filename}')">Restore</button>
      </div>
    `).join('');
  } catch (e) { console.error(e); }
}

async function docsRestore(filename) {
  if (!confirm(`Restore ${filename}? This will overwrite data/knowledge_base.json`)) return;
  const res = await fetch('/docs/rollback/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename })
  });
  const data = await res.json();
  if (!res.ok || data.error) return alert(data.error || 'Restore failed');
  await docsLoadStatus();
  alert('Restored: ' + data.restored);
}

async function docsLoadSessions() {
  try {
    const res = await fetch('/docs/sessions?limit=10');
    const sessions = await res.json();
    const box = document.getElementById('docs-changelog');
    if (!sessions.length) { box.innerHTML = '<div class="no-data">No sessions yet.</div>'; return; }
    const latest = sessions[0];
    const detRes = await fetch(`/docs/sessions/${latest.id}`);
    const det = await detRes.json();
    const diffs = det.session?.summary?.diffs || det.session?.summary?.changed || [];
    if (!diffs.length) {
      box.innerHTML = `<div class="no-data">Latest session #${latest.id} has no diffs.</div>`;
      return;
    }
    box.innerHTML = diffs.slice(0, 10).map(d => `
      <div class="doc-row">
        <div class="doc-row-left">
          <div class="doc-row-title">${d.module_slug || d.slug}</div>
          <div class="doc-row-sub">${d.diff_summary || 'changed'}</div>
        </div>
        ${d.health_score != null ? `<div class="score ${d.health_score < 70 ? 'bad':'ok'}">${d.health_score}%</div>` : ''}
      </div>
    `).join('');
  } catch (e) { console.error(e); }
}

async function docsCheckUpdates() {
  docsClearLog();
  _docsSetLiveStatus('running', 'warn');
  document.getElementById('docs-check-btn').disabled = true;
  document.getElementById('docs-rescrape-btn').disabled = true;
  document.getElementById('docs-changed-list').innerHTML = '<div class="no-data">Checking...</div>';

  const res = await fetch('/docs/check-updates', { method: 'POST' });
  const data = await res.json();
  if (!res.ok || data.error) {
    _docsSetLiveStatus('failed', 'bad');
    document.getElementById('docs-check-btn').disabled = false;
    return alert(data.error || 'Check failed');
  }
  _docsConnectStream(data.session_id);

  // poll for results
  const out = await docsWaitSession(data.session_id);
  _docsLastCheck = out;
  const changed = out.session?.summary?.changed || [];
  _docsChangedSlugs = changed.map(c => c.slug);

  const list = document.getElementById('docs-changed-list');
  if (!changed.length) {
    list.innerHTML = '<div class="no-data">No changes detected.</div>';
  } else {
    list.innerHTML = changed.map(c => `
      <div class="doc-row">
        <div class="doc-row-left">
          <div class="doc-row-title">${c.slug}</div>
          <div class="doc-row-sub">remote_hash=${(c.remote_hash||'').slice(0,10)}… · local_hash=${(c.local_hash||'').slice(0,10)}…</div>
        </div>
        <span class="pill warn">changed</span>
      </div>
    `).join('');
  }
  document.getElementById('docs-rescrape-btn').disabled = !_docsChangedSlugs.length;
  document.getElementById('docs-check-btn').disabled = false;
  await docsLoadSessions();
}

async function docsRescrapeChanged() {
  if (!_docsChangedSlugs.length) return;
  if (!confirm(`Re-scrape ${_docsChangedSlugs.length} changed module(s)?`)) return;
  docsClearLog();
  _docsSetLiveStatus('running', 'warn');
  document.getElementById('docs-rescrape-btn').disabled = true;

  const res = await fetch('/docs/rescrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ modules: _docsChangedSlugs })
  });
  const data = await res.json();
  if (!res.ok || data.error) {
    _docsSetLiveStatus('failed', 'bad');
    document.getElementById('docs-rescrape-btn').disabled = false;
    return alert(data.error || 'Re-scrape failed');
  }
  _docsConnectStream(data.session_id);
  await docsWaitSession(data.session_id);
  await docsLoadStatus();
  await docsLoadRollback();
  await docsLoadSessions();
  _docsSetLiveStatus('done', 'idle');
}

async function docsWaitSession(sessionId) {
  // lightweight polling helper
  for (let i = 0; i < 240; i++) { // up to ~2 minutes
    const res = await fetch(`/docs/sessions/${sessionId}`);
    const data = await res.json();
    const st = data.session?.status;
    if (st && st !== 'running') return data;
    await new Promise(r => setTimeout(r, 500));
  }
  return await (await fetch(`/docs/sessions/${sessionId}`)).json();
}