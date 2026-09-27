'use strict';

// WellPeps Pulse dashboard. Rules this file follows:
// - The CSP forbids inline script and style: no inline event-handler or style
//   attributes. Clicks go through one delegated listener (data-action), and
//   widths are set through the CSSOM (el.style.width) after rendering.
// - Every value from the server passes through escHtml (text) or safeHref
//   (links) before it reaches innerHTML.
// - State-changing requests carry the session's CSRF token (X-CSRF-Token).

let currentTab = 'urgent';
let ME = null;
let CLAIMS = null;            // [{id, text, tier, publishable}]
let reviewItems = [];         // mentions shown in the review desk list
let reviewIndex = 0;
let reviewDetail = null;      // detail of the selected review item
let editClaims = [];          // claim ids in the editor
let feedOffset = 0;
const FEED_PAGE = 50;

// ── Appearance ──

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

applyTheme(THEMES.includes(readTheme()) ? readTheme() : 'auto');

// ── Utilities ──

function escHtml(s) {
  if (s === null || s === undefined || s === '') return '';
  return String(s).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]
  ));
}

function safeHref(url) {
  // Only link out to http(s); anything else (javascript:, data:) is dropped.
  return /^https?:\/\//i.test(String(url || '')) ? escHtml(url) : '';
}

function extLink(url, label) {
  const href = safeHref(url);
  return href ? '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + escHtml(label) + '</a>' : '';
}

// Mention lifecycle vocabulary. tone: waiting | active | good | bad | idle
const STATUS = {
  new:        ['New', 'active'],
  triaged:    ['Triaged', 'active'],
  drafted:    ['Drafted', 'active'],
  in_review:  ['Waiting on you', 'waiting'],
  approved:   ['Approved — post by hand', 'good'],
  rejected:   ['Rejected', 'idle'],
  posted:     ['Posted', 'good'],
  dropped:    ['Dropped', 'idle'],
  escalated:  ['Escalated', 'bad'],
};
const PLATFORMS = ['reddit', 'instagram', 'facebook', 'tiktok', 'x', 'youtube', 'trustpilot', 'bbb', 'google_reviews', 'web', 'other'];
const CATEGORIES = ['complaint', 'question', 'purchase_intent', 'praise', 'misinformation', 'adverse_event', 'legal_regulatory', 'privacy', 'billing_fraud', 'other'];
const URGENCIES = ['urgent', 'high', 'normal', 'low'];
const ESC_KINDS = ['adverse_event', 'legal', 'privacy', 'billing_fraud', 'viral_negative'];

function statusMeta(status) {
  const hit = STATUS[String(status || '').toLowerCase()];
  if (hit) return { label: hit[0], tone: hit[1] };
  const label = String(status || 'unknown').replace(/_/g, ' ');
  return { label: label.charAt(0).toUpperCase() + label.slice(1), tone: 'idle' };
}

function badge(status) {
  const m = statusMeta(status);
  return '<span class="badge t-' + m.tone + '">' + escHtml(m.label) + '</span>';
}

function toneBadge(text, tone) {
  return '<span class="badge t-' + tone + '">' + escHtml(text) + '</span>';
}

function tierBadge(tier) {
  const tone = {green: 'good', yellow: 'waiting', red: 'bad'}[tier] || 'idle';
  return toneBadge(tier ? 'filter ' + tier : 'not filtered', tone);
}

// The DB stores naive UTC; without a zone suffix the browser would read it as local.
const NAIVE_TS = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

function parseTs(d) {
  if (!d) return null;
  const s = String(d);
  const dt = new Date(NAIVE_TS.test(s) ? s.replace(' ', 'T') + 'Z' : s);
  return isNaN(dt) ? null : dt;
}

function formatDate(d) {
  const dt = parseTs(d);
  if (!dt) return escHtml(d || '');
  return escHtml(dt.toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}));
}

function truncate(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

function tag(k, v) {
  return v ? '<span class="tag"><span class="k">' + escHtml(k) + '</span>' + escHtml(v) + '</span>' : '';
}

function emptyState(glyph, title, copy) {
  return '<div class="empty"><div class="glyph">' + glyph + '</div>' +
    '<div class="title">' + escHtml(title) + '</div><div class="copy">' + escHtml(copy) + '</div></div>';
}

function offlineState() {
  return emptyState('&#9888;', 'Dashboard can\'t reach the server',
    'The dashboard process may have stopped. Restart it with pulse dashboard and refresh this page.');
}

function applyWidths(root) {
  (root || document).querySelectorAll('[data-width]').forEach(el => {
    el.style.width = Math.max(0, Math.min(100, Number(el.dataset.width) || 0)) + '%';
  });
}

// ── API ──

async function request(path, opts) {
  opts = opts || {};
  const headers = {};
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if ((opts.method || 'GET') !== 'GET' && ME) headers['X-CSRF-Token'] = ME.csrf;
  try {
    const r = await fetch(path, {method: opts.method || 'GET', headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body), credentials: 'same-origin'});
    if (r.status === 401) { window.location.assign('/login'); return {ok: false, status: 401, data: null}; }
    let data = null;
    try { data = await r.json(); } catch { data = null; }
    return {ok: r.ok, status: r.status, data};
  } catch {
    return {ok: false, status: 0, data: null};
  }
}

async function api(path) {
  const r = await request(path);
  return r.ok ? r.data : null;
}

async function send(path, body) {
  const r = await request(path, {method: 'POST', body: body || {}});
  if (!r.ok) showToast((r.data && r.data.detail && String(r.data.detail)) || 'Request failed (' + r.status + ').', 'error');
  return r;
}

function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), type === 'error' ? 6000 : 2600);
}

function navCount(id, n, alert) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = n ? String(n) : '';
  el.classList.toggle('on', !!n);
  el.classList.toggle('alert', !!alert && !!n);
}

function can(permission) {
  const perms = {
    viewer: ['view'], reviewer: ['view', 'review', 'ack'], clinical: ['view', 'ack_adverse'],
    admin: ['view', 'review', 'ack', 'ack_adverse', 'admin'],
  };
  return !!ME && (perms[ME.role] || []).includes(permission);
}

// ── Tabs ──

function showTab(id) {
  currentTab = id;
  document.querySelectorAll('.section').forEach(s => s.classList.toggle('active', s.id === id));
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.tab === id));
  loadCurrentTab();
}

function loadCurrentTab() {
  switch (currentTab) {
    case 'urgent': loadUrgent(); break;
    case 'review': loadReview(); break;
    case 'feed': loadFeed(); break;
    case 'usage': loadUsage(); break;
    case 'users': loadUsers(); break;
    case 'controls': loadHarveyStatus(); loadLogs(); break;
  }
}

// ── Urgent ──

function countdownText(seconds) {
  if (seconds === null || seconds === undefined) return 'no SLA';
  const late = seconds < 0;
  const s = Math.abs(Math.round(seconds));
  const m = Math.floor(s / 60), sec = s % 60;
  const text = (m >= 60 ? Math.floor(m / 60) + 'h ' + (m % 60) + 'm' : m + 'm ' + String(sec).padStart(2, '0') + 's');
  return late ? 'overdue ' + text : 'due in ' + text;
}

function tickCountdowns() {
  const now = Date.now();
  document.querySelectorAll('.countdown[data-due]').forEach(el => {
    const due = parseTs(el.dataset.due);
    if (!due) return;
    const seconds = (due.getTime() - now) / 1000;
    el.textContent = countdownText(seconds);
    el.classList.toggle('late', seconds < 0);
  });
}

async function pollUrgent() {
  const data = await api('/api/urgent');
  if (!data) return null;
  const breached = data.items.filter(i => i.breached).length;
  navCount('nav-urgent', data.items.length, breached > 0);
  return data;
}

async function loadUrgent() {
  const el = document.getElementById('urgent-list');
  const data = await pollUrgent();
  if (!data) { el.innerHTML = offlineState(); return; }
  if (!data.items.length) {
    el.innerHTML = emptyState('&#10003;', 'Nothing urgent', 'No open escalations. Adverse events, legal threats, privacy and billing-fraud posts appear here the moment triage flags them.');
    return;
  }
  let html = '<div class="queue">';
  for (const e of data.items) {
    const ackAllowed = e.kind === 'adverse_event' ? can('ack_adverse') : can('ack');
    html += '<div class="queue-item ' + (e.breached ? 'bad' : 'warn') + '" id="escalation-' + escHtml(String(e.id)) + '">' +
      '<div class="qtext">' +
        '<div class="qtitle">#' + escHtml(String(e.id)) + ' ' + escHtml(String(e.kind).replace(/_/g, ' ')) +
          ' on ' + escHtml(e.platform) + '</div>' +
        '<div class="urgent-meta">' +
          '<span class="countdown' + (e.breached ? ' late' : '') + '" data-due="' + escHtml(e.sla_due_at || '') + '">' +
            escHtml(countdownText(e.seconds_left)) + '</span>' +
          (e.breached ? toneBadge('SLA breached', 'bad') : '') +
          (e.notified ? '' : toneBadge('not paged', 'waiting')) +
          '<span>Owner: <b>' + escHtml(e.owner || 'UNASSIGNED') + '</b></span>' +
          (e.category ? tag('category', e.category) : '') +
          extLink(e.permalink, 'Open post ↗') +
        '</div>' +
      '</div>' +
      '<div class="urgent-actions">' +
        '<button class="btn btn-primary btn-sm" data-action="ack" data-id="' + escHtml(String(e.id)) + '"' +
          (ackAllowed ? '' : ' disabled title="' + (e.kind === 'adverse_event' ? 'Only clinical or admin can acknowledge adverse events' : 'Your role cannot acknowledge escalations') + '"') +
          '>Ack</button>' +
      '</div></div>';
  }
  el.innerHTML = html + '</div>';
  tickCountdowns();
}

async function ackEscalation(id) {
  const r = await send('/api/escalations/' + encodeURIComponent(id) + '/ack');
  if (r.ok) { showToast('Escalation #' + id + ' acknowledged.', 'success'); loadUrgent(); }
}

// ── Review desk ──

async function loadClaims() {
  if (!CLAIMS) CLAIMS = await api('/api/claims') || [];
  return CLAIMS;
}

async function loadReview(keepSelection) {
  const listEl = document.getElementById('review-list');
  const [waiting, approved] = await Promise.all([
    api('/api/mentions?status=in_review&limit=200'),
    api('/api/mentions?status=approved&limit=200'),
  ]);
  if (!waiting || !approved) { listEl.innerHTML = offlineState(); return; }
  const selectedId = keepSelection && reviewItems[reviewIndex] ? reviewItems[reviewIndex].id : null;
  reviewItems = waiting.items.slice().reverse().concat(approved.items.slice().reverse());  // oldest first
  navCount('nav-review', waiting.total, false);
  if (!reviewItems.length) {
    listEl.innerHTML = '';
    document.getElementById('review-pane').innerHTML = emptyState('&#10003;', 'Nothing to review',
      'Drafts land here after the compliance filter and the adversarial reviewer have looked at them.');
    return;
  }
  const idx = selectedId ? reviewItems.findIndex(m => m.id === selectedId) : -1;
  reviewIndex = idx >= 0 ? idx : Math.min(reviewIndex, reviewItems.length - 1);
  let html = '';
  let group = '';
  reviewItems.forEach((m, i) => {
    const g = m.status === 'approved' ? 'Approved — post by hand' : 'Waiting on you';
    if (g !== group) { html += '<div class="desk-group">' + escHtml(g) + '</div>'; group = g; }
    html += '<div class="desk-item' + (i === reviewIndex ? ' active' : '') + '" data-action="review-select" data-index="' + i + '">' +
      '<div class="to">' + escHtml(m.platform) + ' · ' + escHtml(m.category || '') + '</div>' +
      '<div class="sub">' + escHtml(truncate(m.title || m.text, 80)) + '</div></div>';
  });
  listEl.innerHTML = html;
  await selectReview(reviewIndex);
}

async function selectReview(index) {
  if (!reviewItems.length) return;
  reviewIndex = Math.max(0, Math.min(index, reviewItems.length - 1));
  document.querySelectorAll('#review-list .desk-item').forEach(el => {
    el.classList.toggle('active', Number(el.dataset.index) === reviewIndex);
  });
  const active = document.querySelector('#review-list .desk-item.active');
  if (active) active.scrollIntoView({block: 'nearest'});
  const [detail] = await Promise.all([api('/api/mentions/' + reviewItems[reviewIndex].id), loadClaims()]);
  const pane = document.getElementById('review-pane');
  if (!detail) { pane.innerHTML = offlineState(); return; }
  reviewDetail = detail;
  editClaims = detail.latest_draft ? detail.latest_draft.claim_ids.slice() : [];
  renderReviewPane();
}

function claimChip(cid, removable) {
  const info = (reviewDetail && reviewDetail.claims[cid]) || (CLAIMS || []).find(c => c.id === cid) || {};
  const cls = info.publishable ? 'ok' : 'pending';
  const hover = (info.text || 'unknown claim') + (info.publishable ? '' : ' — PENDING sign-off, not publishable');
  return '<span class="claim-chip ' + cls + '" title="' + escHtml(hover) + '">' + escHtml(cid) +
    (removable ? '<button type="button" data-action="claim-remove" data-claim="' + escHtml(cid) + '" aria-label="Remove claim">×</button>' : '') +
    '</span>';
}

function renderClaims() {
  const el = document.getElementById('claim-chips');
  if (!el) return;
  const editable = can('review') && reviewDetail.mention.status === 'in_review';
  el.innerHTML = editClaims.length ? editClaims.map(c => claimChip(c, editable)).join(' ')
    : '<span class="muted">No claim IDs — approval needs at least one.</span>';
}

function timeline(audit) {
  if (!audit || !audit.length) return '<p class="muted">No audit events yet.</p>';
  return '<div class="timeline">' + audit.map(e => {
    const v = e.verdict || {};
    const note = v.reason || (e.event === 'edited' || e.event === 'approved' || e.event === 'copied' ? truncate(e.final_text, 160) : '') ||
      (v.kind ? 'kind: ' + v.kind : '') || (v.posted_url ? 'posted at ' + v.posted_url : '') ||
      (e.filter_result && e.filter_result.tier ? 'filter ' + e.filter_result.tier : '') || (v.verdict ? 'verdict ' + v.verdict : '');
    return '<div class="ev"><span class="when">' + formatDate(e.at) + '</span>' +
      '<span class="what">' + escHtml(e.event) + '</span><span class="who">' + escHtml(e.actor) + '</span>' +
      (note ? '<div class="note">' + escHtml(note) + '</div>' : '') + '</div>';
  }).join('') + '</div>';
}

function triageTags(m, t) {
  t = t || {};
  return '<div class="tag-row">' + tag('platform', m.platform) + tag('category', t.category) + tag('urgency', t.urgency) +
    tag('competitor', t.competitor) + tag('drug', t.drug) + tag('product', t.product) + '</div>';
}

function renderReviewPane() {
  const d = reviewDetail;
  const m = d.mention, draft = d.latest_draft, status = m.status;
  const inReview = status === 'in_review', approved = status === 'approved';
  const reviewer = can('review');
  const blockers = (d.approval && d.approval.blockers) || [];
  const claimOptions = (CLAIMS || []).map(c => '<option value="' + escHtml(c.id) + '">' + escHtml(c.id) +
    (c.publishable ? '' : ' (pending)') + ' — ' + escHtml(truncate(c.text, 70)) + '</option>').join('');
  let html = '<div class="to-line">' + escHtml(m.platform) + ' · ' + escHtml(m.author_handle || 'unknown author') +
      ' · collected ' + formatDate(m.collected_at) + ' · ' + badge(status) + '</div>' +
    '<div class="subject">' + escHtml(m.title || truncate(m.text, 90)) + '</div>' +
    '<div class="body">' + escHtml(m.text) + '</div>' +
    '<p>' + extLink(m.url, 'Open original ↗') + '</p>' + triageTags(m, d.triage);

  html += '<div class="desk-block"><div class="subhead">Draft reply' + (draft ? ' · v' + escHtml(String(draft.version)) +
      ' by ' + escHtml(draft.model) : '') + '</div>' +
    '<textarea class="form-input editor" id="editor"' + (reviewer && (inReview || approved) ? '' : ' readonly') + '>' +
      escHtml(draft ? draft.text : '') + '</textarea>' +
    '<div class="desk-block"><div class="subhead">Claims</div><div id="claim-chips"></div>' +
    (reviewer && inReview ? '<div class="claim-add"><select class="form-input" id="claim-select">' + claimOptions +
      '</select><button class="btn btn-secondary btn-sm" data-action="claim-add">Add claim</button></div>' : '') + '</div>';

  if (draft) {
    html += '<div class="desk-block"><div class="subhead">Checks</div>' + tierBadge(draft.tier) + ' ' +
      toneBadge('reviewer: ' + (draft.review_verdict || 'none'), draft.review_verdict === 'pass' ? 'good' : draft.review_verdict === 'reject' ? 'bad' : 'waiting') +
      (draft.filter_hits.length ? '<ul class="hit-list">' + draft.filter_hits.map(h => '<li>' + escHtml(h) + '</li>').join('') + '</ul>' : '') +
      (draft.review_reasons.length ? '<ul class="hit-list">' + draft.review_reasons.map(r => '<li>' + escHtml(r) + '</li>').join('') + '</ul>' : '') +
      '</div>';
  }

  if (reviewer) {
    html += '<div class="desk-actions">';
    if (inReview) {
      html += '<button class="btn btn-secondary" data-action="save-edit">Save edit (re-check)</button>' +
        '<button class="btn btn-primary" id="approve-btn" data-action="approve"' + (blockers.length ? ' disabled' : '') + '>Approve</button>' +
        '<button class="btn btn-danger" data-action="reject">Reject…</button>';
    }
    if (approved) {
      html += '<button class="btn btn-primary" data-action="copy-open">Copy reply &amp; open post</button>' +
        '<button class="btn btn-secondary" data-action="mark-posted">Mark posted…</button>' +
        '<button class="btn btn-secondary" data-action="save-edit">Save edit (voids approval)</button>';
    }
    html += '<select class="form-input" id="escalate-kind">' + ESC_KINDS.map(k => '<option value="' + k + '">' + escHtml(k.replace(/_/g, ' ')) + '</option>').join('') +
      '</select><button class="btn btn-danger btn-sm" data-action="escalate">Escalate</button></div>';
    if (inReview && blockers.length) {
      html += '<ul class="blockers">' + blockers.map(b => '<li>Approve is blocked: ' + escHtml(b) + '</li>').join('') + '</ul>';
    }
  } else {
    html += '<p class="muted">Your role is read-only here.</p>';
  }
  html += '<div class="desk-block"><div class="subhead">Audit trail</div>' + timeline(d.audit) + '</div>';
  document.getElementById('review-pane').innerHTML = html;
  renderClaims();
}

function currentMentionId() {
  return reviewDetail ? reviewDetail.mention.id : null;
}

async function saveEdit() {
  const text = document.getElementById('editor').value;
  const r = await send('/api/mentions/' + currentMentionId() + '/edit', {text, claim_ids: editClaims});
  if (r.ok) {
    showToast('Saved as v' + r.data.version + ' — filter ' + r.data.tier + ', reviewer ' + r.data.verdict + '.', 'success');
    loadReview(true);
  }
}

async function approveCurrent() {
  const btn = document.getElementById('approve-btn');
  if (!btn || btn.disabled || !reviewDetail.latest_draft) return;
  const r = await send('/api/mentions/' + currentMentionId() + '/approve', {draft_id: reviewDetail.latest_draft.id});
  if (r.ok) { showToast('Approved. Copy it and post it by hand.', 'success'); loadReview(true); }
}

async function rejectCurrent() {
  const reason = window.prompt('Why reject this draft? (recorded in the audit log)');
  if (!reason || !reason.trim()) return;
  const r = await send('/api/mentions/' + currentMentionId() + '/reject', {reason: reason.trim()});
  if (r.ok) { showToast('Rejected.', 'success'); loadReview(); }
}

async function copyAndOpen() {
  const draft = reviewDetail.latest_draft;
  const url = reviewDetail.mention.url;
  if (!draft || !safeHref(url)) return;
  const copying = navigator.clipboard ? navigator.clipboard.writeText(draft.text) : Promise.reject(new Error('no clipboard'));
  window.open(url, '_blank', 'noopener,noreferrer');
  try { await copying; } catch { showToast('Could not copy automatically; select the text and copy it.', 'error'); return; }
  const r = await send('/api/mentions/' + currentMentionId() + '/copied');
  if (r.ok) { showToast('Reply copied. Paste it on the post, then mark it posted.', 'success'); loadReview(true); }
}

async function markPosted() {
  const url = window.prompt('Link to the reply you posted (optional):', '');
  if (url === null) return;
  const r = await send('/api/mentions/' + currentMentionId() + '/mark-posted', {posted_url: url.trim()});
  if (r.ok) { showToast('Marked posted.', 'success'); loadReview(); }
}

async function escalateCurrent() {
  const kind = document.getElementById('escalate-kind').value;
  if (!window.confirm('Escalate this mention as ' + kind.replace(/_/g, ' ') + '? The owner is paged.')) return;
  const r = await send('/api/mentions/' + currentMentionId() + '/escalate', {kind});
  if (r.ok) { showToast('Escalated' + (r.data.paged ? ' and paged.' : '; page not sent (check Slack).'), 'success'); loadReview(); pollUrgent(); }
}

// ── Feed ──

function fillSelect(id, values, labeler) {
  const sel = document.getElementById(id);
  if (!sel) return;
  for (const v of values) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = labeler ? labeler(v) : v.replace(/_/g, ' ');
    sel.appendChild(opt);
  }
}

fillSelect('f-status', Object.keys(STATUS), k => STATUS[k][0]);
fillSelect('f-platform', PLATFORMS);
fillSelect('f-category', CATEGORIES);
fillSelect('f-urgency', URGENCIES);

async function loadSummary() {
  const el = document.getElementById('feed-summary');
  const data = await api('/api/summary');
  if (!data) { el.innerHTML = ''; return; }
  const chips = Object.entries(data.mentions || {}).filter(([, n]) => n)
    .map(([k, n]) => '<span class="chip">' + escHtml(statusMeta(k).label) + ' <b>' + escHtml(String(n)) + '</b></span>');
  chips.unshift('<span class="chip">Total <b>' + escHtml(String(data.total || 0)) + '</b></span>');
  el.innerHTML = chips.join('');
}

function feedQuery() {
  const params = new URLSearchParams();
  new FormData(document.getElementById('feed-filters')).forEach((v, k) => { if (String(v).trim()) params.set(k, String(v).trim()); });
  params.set('limit', String(FEED_PAGE));
  params.set('offset', String(feedOffset));
  return params.toString();
}

async function loadFeed() {
  const el = document.getElementById('feed-list');
  loadSummary();
  const data = await api('/api/mentions?' + feedQuery());
  const pager = document.getElementById('feed-pager');
  if (!data) { el.innerHTML = offlineState(); pager.innerHTML = ''; return; }
  if (!data.items.length) {
    el.innerHTML = emptyState('&#9678;', 'No mentions match', 'Change the filters, or wait for collectors to bring in new posts.');
    pager.innerHTML = '';
    return;
  }
  let html = '<div class="table-card"><table><thead><tr>' +
    '<th>Collected</th><th>Platform</th><th>Mention</th><th>Tags</th><th>Status</th><th></th></tr></thead><tbody>';
  for (const m of data.items) {
    const body = m.title ? m.title + ' — ' + (m.text || '') : (m.text || '');
    html += '<tr class="clickable" data-action="open-drawer" data-id="' + escHtml(String(m.id)) + '">' +
      '<td class="muted">' + formatDate(m.collected_at) + '</td>' +
      '<td>' + escHtml(m.platform) + '</td>' +
      '<td>' + escHtml(truncate(body, 200)) + '</td>' +
      '<td>' + tag('', m.category) + ' ' + tag('drug', m.drug) + ' ' + tag('', m.competitor) + '</td>' +
      '<td>' + badge(m.status) + '</td>' +
      '<td>' + extLink(m.url, 'Open ↗') + '</td></tr>';
  }
  el.innerHTML = html + '</tbody></table></div>';
  const end = Math.min(data.offset + data.items.length, data.total);
  pager.innerHTML = '<span>' + escHtml(String(data.offset + 1)) + '–' + escHtml(String(end)) + ' of ' + escHtml(String(data.total)) + '</span>' +
    '<button class="btn btn-secondary btn-sm" data-action="feed-prev"' + (data.offset > 0 ? '' : ' disabled') + '>Previous</button>' +
    '<button class="btn btn-secondary btn-sm" data-action="feed-next"' + (end < data.total ? '' : ' disabled') + '>Next</button>';
}

async function openDrawer(id) {
  const drawer = document.getElementById('drawer');
  const d = await api('/api/mentions/' + encodeURIComponent(id));
  if (!d) return;
  const m = d.mention;
  let html = '<div class="drawer-head"><div class="to-line">#' + escHtml(String(m.id)) + ' · ' + badge(m.status) + '</div>' +
    '<button class="btn btn-secondary btn-sm" data-action="close-drawer">Close</button></div>' +
    '<h2 class="subject">' + escHtml(m.title || truncate(m.text, 90)) + '</h2>' +
    '<div class="body">' + escHtml(m.text) + '</div><p>' + extLink(m.url, 'Open original ↗') + '</p>' +
    triageTags(m, d.triage);
  if (d.latest_draft) {
    html += '<div class="desk-block"><div class="subhead">Latest draft · v' + escHtml(String(d.latest_draft.version)) + '</div>' +
      '<div class="body">' + escHtml(d.latest_draft.text) + '</div>' + tierBadge(d.latest_draft.tier) + '</div>';
  }
  if (d.escalations.length) {
    html += '<div class="desk-block"><div class="subhead">Escalations</div>' + d.escalations.map(e =>
      '<div>#' + escHtml(String(e.id)) + ' ' + escHtml(e.kind) + ' · owner ' + escHtml(e.owner || 'UNASSIGNED') +
      (e.acked_at ? ' · acked by ' + escHtml(e.acked_by) : ' · open') + '</div>').join('') + '</div>';
  }
  html += '<div class="desk-block"><div class="subhead">Audit trail</div>' + timeline(d.audit) + '</div>';
  drawer.innerHTML = html;
  drawer.classList.remove('hidden');
}

function closeDrawer() {
  document.getElementById('drawer').classList.add('hidden');
}

// ── Users (admin) ──

async function loadUsers() {
  const el = document.getElementById('users-list');
  const users = await api('/api/users');
  if (!users) { el.innerHTML = offlineState(); return; }
  let html = '<div class="table-card"><table><thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead><tbody>';
  for (const u of users) {
    html += '<tr><td>' + escHtml(u.email) + '</td><td>' + escHtml(u.display_name) + '</td><td>' + escHtml(u.role) + '</td>' +
      '<td>' + (u.active ? toneBadge('active', 'good') : toneBadge('disabled', 'idle')) + '</td>' +
      '<td class="muted">' + formatDate(u.last_login_at) + '</td>' +
      '<td>' + (u.active ? '<button class="btn btn-danger btn-sm" data-action="disable-user" data-email="' + escHtml(u.email) + '">Disable</button>' : '') + '</td></tr>';
  }
  el.innerHTML = html + '</tbody></table></div>';
}

async function createUser(form) {
  const body = Object.fromEntries(new FormData(form).entries());
  const r = await send('/api/users', body);
  if (r.ok) { showToast('User ' + body.email + ' created.', 'success'); form.reset(); loadUsers(); }
}

async function disableUser(email) {
  if (!window.confirm('Disable ' + email + '? Their sessions end immediately.')) return;
  const r = await send('/api/users/disable', {email});
  if (r.ok) { showToast(email + ' disabled.', 'success'); loadUsers(); }
}

// ── Controls ──

async function loadHarveyStatus() {
  const data = await api('/api/harvey/status');
  const headerDot = document.getElementById('header-dot');
  const headerText = document.getElementById('header-status-text');
  if (!data) { headerDot.className = 'status-dot offline'; headerText.textContent = 'Offline'; return; }
  const running = !!data.running;
  headerDot.className = 'status-dot ' + (running ? 'running' : 'stopped');
  headerText.textContent = running ? 'Heartbeat running' : 'Heartbeat stopped';
  document.getElementById('control-dot').className = 'dot ' + (running ? 'running' : 'stopped');
  const label = document.getElementById('control-label');
  label.className = 'label ' + (running ? 'running' : 'stopped');
  label.textContent = running ? 'Running' : 'Stopped';
  const meta = document.getElementById('control-meta');
  meta.textContent = running && data.pid
    ? 'PID ' + data.pid + (data.started_at ? ' · started ' + (parseTs(data.started_at) || '').toLocaleString() : '')
    : 'The heartbeat wakes every few minutes, does what needs doing, and sleeps.';
  const start = document.getElementById('btn-start'), stop = document.getElementById('btn-stop');
  start.classList.toggle('hidden', running);
  stop.classList.toggle('hidden', !running);
  start.disabled = stop.disabled = !can('admin');
  if (!can('admin')) start.title = stop.title = 'Only admins can start or stop the heartbeat';
}

async function controlHarvey(action) {
  const r = await send('/api/harvey/' + action);
  if (r.ok && r.data && r.data.success) showToast('Heartbeat ' + (action === 'start' ? 'started.' : 'stopped.'), 'success');
  else if (r.ok) showToast((r.data && r.data.message) || 'Failed.', 'error');
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

  const quotaEl = document.getElementById('usage-quota');
  if (data.quota && Object.keys(data.quota).length) {
    const labels = {five_hour: '5-hour window', seven_day: 'Weekly'};
    let qHtml = '<h2>Claude Subscription Quota</h2>';
    for (const [key, w] of Object.entries(data.quota)) {
      const pct = Math.min(100, Math.max(0, Number(w.utilization) || 0));
      qHtml += '<div class="progress-wrap"><div class="progress-label">' +
        '<span class="text">' + escHtml(labels[key] || key) + (w.resets_at ? ' · resets ' + formatDate(w.resets_at) : '') + '</span>' +
        '<span class="pct">' + pct.toFixed(0) + '%</span></div>' +
        '<div class="progress-bar"><div class="progress-fill ' + (pct >= 80 ? 'yellow' : 'green') + '" data-width="' + pct + '"></div></div></div>';
    }
    quotaEl.innerHTML = qHtml;
    quotaEl.classList.remove('hidden');
  } else {
    quotaEl.classList.add('hidden');
  }

  const t = data.totals || {};
  const card = (label, p) => {
    p = p || {};
    return '<div class="stat-card"><div class="label">' + escHtml(label) + '</div>' +
      '<div class="value">' + escHtml(String(p.calls || 0)) + '</div><div class="breakdown">' +
      '<span class="chip">calls</span><span class="chip">out <b>' + fmtTokens(p.output_tokens) + '</b></span>' +
      '<span class="chip">in <b>' + fmtTokens(p.input_tokens) + '</b></span>' +
      '<span class="chip">cached <b>' + fmtTokens(p.cache_read_tokens) + '</b></span></div></div>';
  };
  statsEl.innerHTML = card('Today', t.today) + card('Last 7 Days', t.week) + card('Last 30 Days', t.month);

  const dailyEl = document.getElementById('usage-daily');
  const days = data.by_day || [];
  if (days.length) {
    const maxOut = Math.max(...days.map(d => d.output_tokens || 0), 1);
    let dHtml = '<h2>Daily Output Tokens (30 days)</h2>';
    for (const d of days.slice(-30)) {
      const pct = Math.max(2, (d.output_tokens || 0) / maxOut * 100);
      dHtml += '<div class="bar-row"><span class="bar-day">' + escHtml(d.day || '') + '</span>' +
        '<div class="bar-track"><div class="bar-fill" data-width="' + pct + '"></div></div>' +
        '<span class="bar-val">' + fmtTokens(d.output_tokens) + ' out <span class="muted">· ' + escHtml(String(d.calls || 0)) + ' calls</span></span></div>';
    }
    dailyEl.innerHTML = dHtml;
    dailyEl.classList.remove('hidden');
  } else {
    dailyEl.classList.add('hidden');
  }

  const tablesEl = document.getElementById('usage-tables');
  const table = (title, rows, keyName) => {
    if (!rows || !rows.length) return '';
    let h = '<div class="card"><h2>' + escHtml(title) + '</h2><div class="table-card"><table><thead><tr>' +
      '<th>' + escHtml(keyName) + '</th><th>Calls</th><th>Input</th><th>Output</th><th>Cache read</th></tr></thead><tbody>';
    for (const r of rows) {
      h += '<tr><td>' + escHtml(String(r[keyName.toLowerCase()] || '')) + '</td><td>' + escHtml(String(r.calls || 0)) + '</td>' +
        '<td class="muted">' + fmtTokens(r.input_tokens) + '</td><td>' + fmtTokens(r.output_tokens) + '</td>' +
        '<td class="muted">' + fmtTokens(r.cache_read_tokens) + '</td></tr>';
    }
    return h + '</tbody></table></div></div>';
  };
  if (!(data.by_agent || []).length && !(data.by_task || []).length) {
    tablesEl.innerHTML = emptyState('&#9680;', 'No usage recorded yet',
      'Once Pulse starts making Claude calls, every one is logged here with calls and tokens by agent and task.');
  } else {
    tablesEl.innerHTML = table('By Agent (30 days)', data.by_agent, 'Agent') +
      table('By Task (30 days)', data.by_task, 'Task') + table('By Model (30 days)', data.by_model, 'Model');
  }
  applyWidths(document.getElementById('usage'));
}

// ── Events ──

const ACTIONS = {
  'theme': () => cycleTheme(),
  'refresh': () => loadCurrentTab(),
  'logout': async () => { await request('/api/logout', {method: 'POST', body: {}}); window.location.assign('/login'); },
  'ack': el => ackEscalation(el.dataset.id),
  'review-select': el => selectReview(Number(el.dataset.index)),
  'claim-remove': el => { editClaims = editClaims.filter(c => c !== el.dataset.claim); renderClaims(); },
  'claim-add': () => {
    const cid = document.getElementById('claim-select').value;
    if (cid && !editClaims.includes(cid)) { editClaims.push(cid); renderClaims(); }
  },
  'save-edit': () => saveEdit(),
  'approve': () => approveCurrent(),
  'reject': () => rejectCurrent(),
  'copy-open': () => copyAndOpen(),
  'mark-posted': () => markPosted(),
  'escalate': () => escalateCurrent(),
  'open-drawer': el => openDrawer(el.dataset.id),
  'close-drawer': () => closeDrawer(),
  'feed-prev': () => { feedOffset = Math.max(0, feedOffset - FEED_PAGE); loadFeed(); },
  'feed-next': () => { feedOffset += FEED_PAGE; loadFeed(); },
  'disable-user': el => disableUser(el.dataset.email),
  'start': () => controlHarvey('start'),
  'stop': () => controlHarvey('stop'),
  'logs': () => loadLogs(),
};

document.addEventListener('click', (event) => {
  const tabBtn = event.target.closest('nav button[data-tab]');
  if (tabBtn) { showTab(tabBtn.dataset.tab); return; }
  if (event.target.closest('a[href]')) return;  // let links (Open post ↗) work inside clickable rows
  const el = event.target.closest('[data-action]');
  if (!el || el.disabled) return;
  const handler = ACTIONS[el.dataset.action];
  if (handler) { event.preventDefault(); handler(el); }
});

document.getElementById('feed-filters').addEventListener('submit', (event) => {
  event.preventDefault();
  feedOffset = 0;
  loadFeed();
});

document.getElementById('user-form').addEventListener('submit', (event) => {
  event.preventDefault();
  createUser(event.target);
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { closeDrawer(); return; }
  const typing = event.target.closest('input, textarea, select, [contenteditable]');
  if (typing || event.ctrlKey || event.metaKey || event.altKey || currentTab !== 'review') return;
  if (event.key === 'j') { event.preventDefault(); selectReview(reviewIndex + 1); }
  else if (event.key === 'k') { event.preventDefault(); selectReview(reviewIndex - 1); }
  else if (event.key === 'e') { const ed = document.getElementById('editor'); if (ed) { event.preventDefault(); ed.focus(); } }
  else if (event.key === 'a' && can('review')) { event.preventDefault(); approveCurrent(); }
});

// ── Init & live refresh ──

async function init() {
  ME = await api('/api/me');
  if (!ME) return;  // request() already redirected to /login on 401
  document.getElementById('user-chip').innerHTML = '<span>' + escHtml(ME.name || ME.email) + '</span>' +
    '<span class="role">' + escHtml(ME.role) + '</span>';
  document.getElementById('nav-users').classList.toggle('hidden', !can('admin'));
  showTab('urgent');  // Slack pages link to /#escalation-<id>: the Urgent tab
  loadHarveyStatus();
  loadReviewCount();
  setInterval(pollUrgent, 30000);
  setInterval(tickCountdowns, 1000);
  setInterval(loadHarveyStatus, 8000);
  setInterval(() => {
    if (document.hidden) return;
    if (currentTab === 'urgent') loadUrgent();
    else if (currentTab === 'usage') loadUsage();
    else if (currentTab === 'controls') loadLogs();
  }, 15000);
}

async function loadReviewCount() {
  const data = await api('/api/mentions?status=in_review&limit=1');
  if (data) navCount('nav-review', data.total, false);
}

init();
