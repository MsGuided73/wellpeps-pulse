let currentTab = 'feed';

// ── Appearance ──
//
// Three states, not two: "auto" follows the OS and is the default. Stored
// per-browser; nothing is sent anywhere.

const THEMES = ['auto', 'light', 'dark'];
const THEME_KEY = 'pulse-theme';

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = mode;
}

function readTheme() {
  try { return localStorage.getItem(THEME_KEY) || 'auto'; } catch { return 'auto'; }
}

function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(readTheme()) + 1) % THEMES.length];
  try { localStorage.setItem(THEME_KEY, next); } catch { /* private mode */ }
  applyTheme(next);
}

(function initTheme() {
  const saved = readTheme();
  applyTheme(THEMES.includes(saved) ? saved : 'auto');
})();

// ── Utilities ──

function escHtml(s) {
  if (s === null || s === undefined || s === '') return '';
  return String(s).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]
  ));
}

// Mention lifecycle vocabulary.
// tone: waiting (needs a human) | active (in flight) | good | bad | idle
const STATUS = {
  new:        ['New', 'active'],
  triaged:    ['Triaged', 'active'],
  drafted:    ['Drafted', 'active'],
  in_review:  ['Waiting on you', 'waiting'],
  approved:   ['Approved', 'good'],
  rejected:   ['Rejected', 'idle'],
  posted:     ['Posted', 'good'],
  dropped:    ['Dropped', 'idle'],
  escalated:  ['Escalated', 'bad'],
};

function statusMeta(status) {
  const key = String(status || '').toLowerCase();
  const hit = STATUS[key];
  if (hit) return { label: hit[0], tone: hit[1] };
  const label = String(status || 'unknown').replace(/_/g, ' ');
  return { label: label.charAt(0).toUpperCase() + label.slice(1), tone: 'idle' };
}

function badge(status) {
  const m = statusMeta(status);
  return '<span class="badge t-' + m.tone + '">' + escHtml(m.label) + '</span>';
}

// The DB stores naive UTC ("2026-09-27 19:59:00" / "2026-09-27T19:59:00.123");
// without a zone suffix the browser would read it as local time.
const NAIVE_TS = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

function formatDate(d) {
  if (!d) return '';
  try {
    const s = String(d);
    const dt = new Date(NAIVE_TS.test(s) ? s.replace(' ', 'T') + 'Z' : s);
    if (isNaN(dt)) return escHtml(d);
    return dt.toLocaleString('en-US', {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  } catch { return escHtml(d); }
}

function safeHref(url) {
  // Only link out to http(s); anything else (javascript:, data:) is dropped.
  return /^https?:\/\//i.test(String(url || '')) ? escHtml(url) : '';
}

function truncate(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

function emptyState(glyph, title, copy) {
  return '<div class="empty"><div class="glyph">' + glyph + '</div>' +
    '<div class="title">' + title + '</div>' +
    '<div class="copy">' + copy + '</div></div>';
}

function offlineState() {
  return emptyState('&#9888;', 'Dashboard can\'t reach the server',
    'The dashboard process may have stopped. Restart it with <b>pulse dashboard</b> and refresh this page.');
}

async function api(path, opts) {
  try {
    const r = await fetch(path, opts);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

function navCount(id, n) {
  const el = document.getElementById(id);
  if (el) el.textContent = n ? String(n) : '';
}

// ── Tabs ──

function showTab(id, btn) {
  currentTab = id;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if (btn) btn.classList.add('active');
  loadCurrentTab();
}

function loadCurrentTab() {
  switch (currentTab) {
    case 'feed': loadFeed(); break;
    case 'usage': loadUsage(); break;
    case 'controls': loadHarveyStatus(); loadLogs(); break;
  }
}

// ── Feed ──

(function initStatusFilter() {
  const sel = document.getElementById('feed-status');
  if (!sel) return;
  for (const [key, meta] of Object.entries(STATUS)) {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = meta[0];
    sel.appendChild(opt);
  }
})();

async function loadSummary() {
  const el = document.getElementById('feed-summary');
  const data = await api('/api/summary');
  if (!data) { el.innerHTML = ''; return; }
  const chips = Object.entries(data.mentions || {})
    .filter(([, n]) => n)
    .map(([k, n]) => '<span class="chip">' + escHtml(statusMeta(k).label) + ' <b>' + escHtml(String(n)) + '</b></span>');
  chips.unshift('<span class="chip">Total <b>' + escHtml(String(data.total || 0)) + '</b></span>');
  if (data.open_escalations) {
    chips.push('<span class="chip">Open escalations <b>' + escHtml(String(data.open_escalations)) + '</b></span>');
  }
  el.innerHTML = chips.join('');
  navCount('nav-feed', (data.mentions || {}).new || 0);
}

async function loadFeed() {
  const el = document.getElementById('feed-list');
  const status = document.getElementById('feed-status').value;
  const qs = status ? '?status=' + encodeURIComponent(status) : '';
  loadSummary();
  const rows = await api('/api/mentions' + qs);
  if (!rows) { el.innerHTML = offlineState(); return; }
  if (!rows.length) {
    el.innerHTML = emptyState('&#9678;', 'No mentions yet',
      'Collectors arrive in a later phase. Once they run, every public mention appears here with a link to its source.');
    return;
  }
  let html = '<div class="table-card"><table><thead><tr>' +
    '<th>Collected</th><th>Platform</th><th>Author</th><th>Mention</th><th>Status</th><th></th>' +
    '</tr></thead><tbody>';
  for (const m of rows) {
    const href = safeHref(m.url);
    const body = m.title ? m.title + ' — ' + (m.text || '') : (m.text || '');
    html += '<tr>' +
      '<td class="muted">' + formatDate(m.collected_at) + '</td>' +
      '<td>' + escHtml(m.platform) + '</td>' +
      '<td class="muted">' + escHtml(m.author_handle) + '</td>' +
      '<td>' + escHtml(truncate(body, 220)) + '</td>' +
      '<td>' + badge(m.status) + '</td>' +
      '<td>' + (href ? '<a href="' + href + '" target="_blank" rel="noopener noreferrer">Open</a>' : '') + '</td>' +
      '</tr>';
  }
  el.innerHTML = html + '</tbody></table></div>';
}

// ── Controls ──

async function loadHarveyStatus() {
  const data = await api('/api/harvey/status');
  const headerDot = document.getElementById('header-dot');
  const headerText = document.getElementById('header-status-text');

  if (!data) {
    headerDot.className = 'status-dot offline';
    headerText.textContent = 'Offline';
    return;
  }
  const running = !!data.running;

  headerDot.className = 'status-dot ' + (running ? 'running' : 'stopped');
  headerText.textContent = running ? 'Heartbeat running' : 'Heartbeat stopped';
  document.getElementById('control-dot').className = 'dot ' + (running ? 'running' : 'stopped');
  const label = document.getElementById('control-label');
  label.className = 'label ' + (running ? 'running' : 'stopped');
  label.textContent = running ? 'Running' : 'Stopped';

  const meta = document.getElementById('control-meta');
  if (running && data.pid) {
    let info = 'PID ' + escHtml(String(data.pid));
    if (data.started_at) info += ' &middot; started ' + formatDate(data.started_at);
    meta.innerHTML = info;
  } else {
    meta.innerHTML = 'The heartbeat wakes every few minutes, does what needs doing, and sleeps.';
  }

  document.getElementById('btn-start').style.display = running ? 'none' : '';
  document.getElementById('btn-stop').style.display = running ? '' : 'none';
}

async function startHarvey() {
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  const data = await api('/api/harvey/start', {method: 'POST'});
  if (data && data.success) showToast('Heartbeat started.', 'success');
  else showToast((data && data.message) || 'Failed to start.', 'error');
  btn.disabled = false;
  loadHarveyStatus();
}

async function stopHarvey() {
  const btn = document.getElementById('btn-stop');
  btn.disabled = true;
  const data = await api('/api/harvey/stop', {method: 'POST'});
  if (data && data.success) showToast('Heartbeat stopped.', 'success');
  else showToast((data && data.message) || 'Failed to stop.', 'error');
  btn.disabled = false;
  loadHarveyStatus();
}

async function loadLogs() {
  const data = await api('/api/harvey/logs');
  const el = document.getElementById('log-viewer');
  if (data && data.lines && data.lines.length) {
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.textContent = data.lines.join('\n');
    if (stick) el.scrollTop = el.scrollHeight;
  } else {
    el.textContent = 'No logs yet. Start the heartbeat to see activity.';
  }
}

// ── Usage ──

function fmtTokens(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

async function loadUsage() {
  const data = await api('/api/usage');
  const statsEl = document.getElementById('usage-stats');
  if (!data) { statsEl.innerHTML = offlineState(); return; }

  // Quota gauges — the same numbers `/usage` shows in Claude Code.
  const quotaEl = document.getElementById('usage-quota');
  if (data.quota && Object.keys(data.quota).length) {
    const labels = {five_hour: '5-hour window', seven_day: 'Weekly'};
    let qHtml = '<h2>Claude Subscription Quota</h2>';
    for (const [key, w] of Object.entries(data.quota)) {
      const pct = Math.min(100, Math.max(0, w.utilization || 0));
      const color = pct >= 80 ? 'yellow' : 'green';
      const resets = w.resets_at ? 'resets ' + formatDate(w.resets_at) : '';
      qHtml += '<div class="progress-wrap">' +
        '<div class="progress-label">' +
          '<span class="text">' + escHtml(labels[key] || key) + (resets ? ' &middot; ' + escHtml(resets) : '') + '</span>' +
          '<span class="pct">' + pct.toFixed(0) + '%</span>' +
        '</div>' +
        '<div class="progress-bar"><div class="progress-fill ' + color + '" style="width:' + pct + '%"></div></div>' +
      '</div>';
    }
    quotaEl.innerHTML = qHtml;
    quotaEl.style.display = 'block';
  } else {
    quotaEl.style.display = 'none';
  }

  // Totals cards — calls are the headline; tokens as chips. No dollars:
  // on a subscription plan usage isn't billed per token.
  const t = data.totals || {};
  const card = (label, p) => {
    p = p || {};
    return '<div class="stat-card"><div class="label">' + label + '</div>' +
      '<div class="value">' + (p.calls || 0) + '</div>' +
      '<div class="breakdown">' +
        '<span class="chip">calls</span>' +
        '<span class="chip">out <b>' + fmtTokens(p.output_tokens) + '</b></span>' +
        '<span class="chip">in <b>' + fmtTokens(p.input_tokens) + '</b></span>' +
        '<span class="chip">cached <b>' + fmtTokens(p.cache_read_tokens) + '</b></span>' +
      '</div></div>';
  };
  statsEl.innerHTML = card('Today', t.today) + card('Last 7 Days', t.week) + card('Last 30 Days', t.month);

  // Daily bars — output tokens per day
  const dailyEl = document.getElementById('usage-daily');
  const days = data.by_day || [];
  if (days.length) {
    const maxOut = Math.max(...days.map(d => d.output_tokens || 0), 1);
    let dHtml = '<h2>Daily Output Tokens (30 days)</h2>';
    for (const d of days.slice(-30)) {
      const pct = Math.max(2, (d.output_tokens || 0) / maxOut * 100);
      dHtml += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;font-size:12px">' +
        '<span class="muted" style="width:78px;flex-shrink:0;font-family:var(--mono)">' + escHtml(d.day || '') + '</span>' +
        '<div style="flex:1;background:rgba(255,255,255,0.05);border-radius:99px;height:10px;overflow:hidden">' +
          '<div style="width:' + pct + '%;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent-deep),var(--accent))"></div>' +
        '</div>' +
        '<span style="width:130px;text-align:right;font-variant-numeric:tabular-nums">' + fmtTokens(d.output_tokens) + ' out' +
          ' <span class="muted">&middot; ' + (d.calls || 0) + ' calls</span></span>' +
      '</div>';
    }
    dailyEl.innerHTML = dHtml;
    dailyEl.style.display = 'block';
  } else {
    dailyEl.style.display = 'none';
  }

  // Breakdown tables — calls + tokens, no cost column
  const tablesEl = document.getElementById('usage-tables');
  const table = (title, rows, keyName) => {
    if (!rows || !rows.length) return '';
    let h = '<div class="card"><h2>' + title + '</h2><div class="table-card"><table><thead><tr>' +
      '<th>' + keyName + '</th><th>Calls</th><th>Input</th><th>Output</th><th>Cache read</th>' +
      '</tr></thead><tbody>';
    for (const r of rows) {
      h += '<tr><td>' + escHtml(String(r[keyName.toLowerCase()] || '')) + '</td>' +
        '<td>' + (r.calls || 0) + '</td>' +
        '<td class="muted">' + fmtTokens(r.input_tokens) + '</td>' +
        '<td>' + fmtTokens(r.output_tokens) + '</td>' +
        '<td class="muted">' + fmtTokens(r.cache_read_tokens) + '</td></tr>';
    }
    return h + '</tbody></table></div></div>';
  };

  const anyRows = (data.by_agent || []).length || (data.by_task || []).length;
  if (!anyRows) {
    tablesEl.innerHTML = emptyState('&#9680;', 'No usage recorded yet',
      'Once Pulse starts making Claude calls, every one is logged here with calls and tokens by agent and task.');
  } else {
    tablesEl.innerHTML =
      table('By Agent (30 days)', data.by_agent, 'Agent') +
      table('By Task (30 days)', data.by_task, 'Task') +
      table('By Model (30 days)', data.by_model, 'Model');
  }
}

// ── Init & live refresh ──

loadFeed();
loadHarveyStatus();

setInterval(loadHarveyStatus, 8000);

// Auto-refresh live views without clobbering anything in progress.
setInterval(() => {
  if (document.hidden) return;
  switch (currentTab) {
    case 'feed': loadFeed(); break;
    case 'usage': loadUsage(); break;
    case 'controls': loadLogs(); break;
  }
}, 15000);
