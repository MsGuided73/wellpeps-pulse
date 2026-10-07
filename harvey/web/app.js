'use strict';

// WellPeps Pulse dashboard. Rules this file follows:
// - The CSP forbids inline script and style: no inline event-handler or style
//   attributes. Clicks go through one delegated listener (data-action), and
//   widths are set through the CSSOM (el.style.width) after rendering.
// - Every value from the server passes through escHtml (text) or safeHref
//   (links) before it reaches innerHTML.
// - State-changing requests carry the session's CSRF token (X-CSRF-Token).
// - Labels, times and platform icons come from labels.js (loaded first):
//   raw enum codes are shown only in title attributes.

let currentTab = 'feed';
let ME = null;
let CLAIMS = null;            // [{id, text, tier, publishable}]
let reviewItems = [];         // mentions shown in the review desk list
let reviewIndex = 0;
let reviewDetail = null;      // detail of the selected review item
let editClaims = [];          // claim ids in the editor
let DEMO = {sandbox: false, demo_config: false, brand_handle: ''};  // GET /api/demo

// ── Utilities ──

function safeHref(url) {
  // Only link out to http(s); anything else (javascript:, data:) is dropped.
  return /^https?:\/\//i.test(String(url || '')) ? escHtml(url) : '';
}

// Local DEMO sandbox permalink (http://127.0.0.1:<port>/sandbox/...).
function isSandboxUrl(url) {
  try {
    const u = new URL(String(url || ''));
    return /^https?:$/.test(u.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname) &&
      u.pathname.startsWith('/sandbox/');
  } catch { return false; }
}

// In demo mode a sandbox permalink opens on this dashboard's own host/port
// (the demo data may have been seeded for another port).
function postUrl(url) {
  if (!DEMO.sandbox || !isSandboxUrl(url)) return url;
  const u = new URL(url);
  return window.location.origin + u.pathname + u.search + u.hash;
}

function isDemoPost(url) {
  return DEMO.sandbox && isSandboxUrl(url);
}

function extLink(url, text, cls) {
  const href = safeHref(postUrl(url));
  return href ? '<a' + (cls ? ' class="' + escHtml(cls) + '"' : '') + ' href="' + href +
    '" target="_blank" rel="noopener noreferrer">' + escHtml(text) + '</a>' : '';
}

// Mention lifecycle tone: waiting | active | good | bad | idle
const STATUS_TONE = {
  new: 'active', triaged: 'active', drafted: 'active', in_review: 'waiting', approved: 'good',
  rejected: 'idle', posted: 'good', dropped: 'idle', escalated: 'bad',
};
const PLATFORMS = Object.keys(LABELS.platform);
const CATEGORIES = Object.keys(LABELS.category);
const URGENCIES = Object.keys(LABELS.urgency);
const ESC_KINDS = Object.keys(LABELS.kind);

function statusMeta(status) {
  const key = String(status || 'unknown').toLowerCase();
  return { label: label('status', key), tone: STATUS_TONE[key] || 'idle' };
}

function badge(status) {
  const m = statusMeta(status);
  return '<span class="badge t-' + m.tone + '" title="' + escHtml(status || '') + '">' + escHtml(m.label) + '</span>';
}

function toneBadge(text, tone) {
  return '<span class="badge t-' + tone + '">' + escHtml(text) + '</span>';
}

function tierBadge(tier) {
  const tone = {green: 'good', yellow: 'waiting', red: 'bad'}[tier] || 'idle';
  return '<span class="badge t-' + tone + '" title="' + escHtml(tier || '') + '">' +
    escHtml(tier ? label('tier', tier) : 'Not filtered') + '</span>';
}

function verdictBadge(verdict) {
  const tone = verdict === 'pass' ? 'good' : verdict === 'reject' ? 'bad' : 'waiting';
  return '<span class="badge t-' + tone + '" title="' + escHtml(verdict || '') + '">' +
    escHtml(verdict ? label('verdict', verdict) : 'Not reviewed') + '</span>';
}

// Relative time ("3h ago") with the absolute time in the tooltip; escaped HTML.
function formatDate(d) {
  return timeHtml(d);
}

function truncate(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

function tag(k, v, raw) {
  return v ? '<span class="tag"' + (raw ? ' title="' + escHtml(raw) + '"' : '') + '>' +
    (k ? '<span class="k">' + escHtml(k) + '</span>' : '') + escHtml(v) + '</span>' : '';
}

// A tag whose value is a raw code shown through the label map.
function labelTag(k, group, raw) {
  return raw ? tag(k, label(group, raw), raw) : '';
}

function whyHtml(why) {
  if (!why || !why.text) return '';
  return '<div class="why"><span class="why-src" title="' + escHtml(why.source) + '">' +
    escHtml(label('why', why.source)) + '</span><span class="why-text">' + escHtml(why.text) + '</span></div>';
}

function authorText(platform, handle) {
  handle = String(handle || '').trim();
  if (!handle) return '';
  if (platform === 'reddit' && !/^\/?u\//i.test(handle)) return 'u/' + handle;
  if (['x', 'instagram', 'tiktok'].includes(platform) && handle[0] !== '@') return '@' + handle;
  return handle;
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
    // An admin reset the password: only the change-password screen (password.js) works now.
    if (r.status === 403 && data && data.error === 'password_change_required') enterForcedChange();
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
    viewer: ['view'], reviewer: ['view', 'review', 'ack'], clinical: ['view', 'ack_adverse', 'approve_clinical'],
    admin: ['view', 'review', 'ack', 'ack_adverse', 'approve_clinical', 'admin'],
  };
  return !!ME && (perms[ME.role] || []).includes(permission);
}

// ── Tabs & routing ──
// The URL hash names the tab (#feed, #urgent, #analytics…); Feed and
// Analytics add their filters (#feed?q=…). Slack links use #escalation-<id>
// (Urgent) and #pulse-brief-<id> (Pulse). No hash means Feed.

const TABS = ['feed', 'urgent', 'review', 'analytics', 'pulse', 'usage', 'users', 'controls'];
const TAB_HASH = {};          // tab -> () => hash with its filters (feed.js, analytics.js)
const TAB_ROUTE = {};         // tab -> (query) => void, called before the tab loads

function tabHash(id) {
  return TAB_HASH[id] ? TAB_HASH[id]() : '#' + id;
}

function routeFromHash(hash) {
  if (/^#escalation-\d+$/.test(hash)) return {tab: 'urgent', keep: true};
  if (/^#pulse-brief-\d+$/.test(hash)) return {tab: 'pulse', keep: true};
  const m = hash.match(/^#([a-z]+)(?:\?(.*))?$/);
  if (m && TABS.includes(m[1])) return {tab: m[1], query: m[2] || '', keep: true};
  return {tab: 'feed', query: '', keep: false};
}

function route() {
  const r = routeFromHash(window.location.hash);
  const tab = r.tab === 'users' && !can('admin') ? 'feed' : r.tab;
  if (TAB_ROUTE[tab] && r.query !== undefined) TAB_ROUTE[tab](r.query);
  showTab(tab, {keepHash: r.keep && tab === r.tab, replace: true});
}

function showTab(id, opts) {
  currentTab = id;
  document.querySelectorAll('.section').forEach(s => s.classList.toggle('active', s.id === id));
  document.querySelectorAll('nav button').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === id);
    if (b.dataset.tab === id) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  if (!(opts && opts.keepHash)) {
    const hash = tabHash(id);
    if (window.location.hash !== hash) history[opts && opts.replace ? 'replaceState' : 'pushState'](null, '', hash);
  }
  if (typeof Charts !== 'undefined') Charts.hideTip();
  loadCurrentTab();
}

window.addEventListener('hashchange', () => { if (ME) route(); });

function loadCurrentTab() {
  switch (currentTab) {
    case 'urgent': loadUrgent(); break;
    case 'review': loadReview(); break;
    case 'analytics': loadAnalytics(); break;
    case 'pulse': loadPulse(); break;
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
  renderUrgentBanner(data.items.length, breached);
  return data;
}

const UNASSIGNED_HINT = 'Set escalation.owners in harvey.yaml';

function ownerHtml(owner) {
  const o = String(owner || '').trim();
  if (!o || /^UNASSIGNED/i.test(o)) {
    return '<span class="badge t-waiting owner-missing" title="' + escHtml(UNASSIGNED_HINT) + '">Unassigned</span>';
  }
  return '<span class="owner">Owner <b>' + escHtml(o) + '</b></span>';
}

function pagedHtml(e) {
  const at = parseTs(e.notified_at);
  if (!e.notified || !at) return '<span class="badge t-waiting" title="Slack page not sent yet; the sweep retries">Not paged</span>';
  const sameDay = at.toDateString() === new Date().toDateString();
  return '<span class="paged" title="' + escHtml(absTime(e.notified_at)) + '">Paged ' +
    escHtml(sameDay ? clockTime(at) : relTime(e.notified_at)) + '</span>';
}

function urgentCard(e) {
  const id = escHtml(String(e.id));
  const ackAllowed = e.kind === 'adverse_event' ? can('ack_adverse') : can('ack');
  const ackHint = e.kind === 'adverse_event' ? 'Only clinical or admin can acknowledge adverse events'
    : 'Your role cannot acknowledge escalations';
  const posted = e.posted_at || e.collected_at;
  const author = authorText(e.platform, e.author_handle);
  return '<article class="ucard ' + (e.breached ? 'bad' : 'warn') + '" id="escalation-' + id + '">' +
    '<div class="ucard-top">' + platformHtml(e.platform) +
      '<span class="ucard-id">#' + id + '</span>' +
      '<span class="ucard-sla"><span class="countdown' + (e.breached ? ' late' : '') + '" data-due="' +
        escHtml(e.sla_due_at || '') + '" title="SLA due ' + escHtml(absTime(e.sla_due_at)) + '">' +
        escHtml(countdownText(e.seconds_left)) + '</span>' +
        (e.breached ? toneBadge('SLA breached', 'bad') : '') + '</span>' +
    '</div>' +
    '<h3 class="ucard-title" title="' + escHtml(e.kind) + '">' + escHtml(label('kind', e.kind)) + '</h3>' +
    (e.title ? '<div class="ucard-subject">' + escHtml(e.title) + '</div>' : '') +
    (e.excerpt ? '<blockquote class="ucard-quote">“' + escHtml(e.excerpt) + '”</blockquote>' : '') +
    whyHtml(e.why) +
    '<div class="ucard-meta">' +
      (posted ? '<span>' + (e.posted_at ? 'posted ' : 'collected ') + timeHtml(posted) + '</span>' : '') +
      (author ? '<span class="author">' + escHtml(author) + '</span>' : '') +
      pagedHtml(e) + ownerHtml(e.owner) +
    '</div>' +
    '<div class="ucard-actions">' +
      '<button class="btn btn-secondary btn-sm" data-action="view-details" data-id="' + escHtml(String(e.mention_id)) + '">View details</button>' +
      extLink(e.permalink, 'Open post ↗', 'btn btn-ghost btn-sm') +
      '<button class="btn btn-primary btn-sm" data-action="ack" data-id="' + id + '"' +
        (ackAllowed ? '' : ' disabled title="' + escHtml(ackHint) + '"') + '>Ack</button>' +
    '</div></article>';
}

async function loadUrgent() {
  const el = document.getElementById('urgent-list');
  const data = await pollUrgent();
  if (!data) { el.innerHTML = offlineState(); return; }
  if (!data.items.length) {
    el.innerHTML = emptyState('&#10003;', 'Nothing urgent', 'No open escalations. Adverse events, legal threats, privacy and billing-fraud posts appear here the moment triage flags them.');
    return;
  }
  el.innerHTML = '<div class="ucards">' + data.items.map(urgentCard).join('') + '</div>';
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
  // Waiting items: safety and incidents first, then unresolved gates, then the
  // rest (Competitor/Switching Protocol §9), oldest first within each.
  reviewItems = waiting.items.slice().reverse().sort((a, b) => queueRank(a) - queueRank(b))
    .concat(approved.items.slice().reverse());
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
      '<div class="to">' + platformIcon(m.platform) + '<span>' + escHtml(label('platform', m.platform)) +
        (m.category ? ' · ' + escHtml(label('category', m.category)) : '') + '</span>' +
        (m.urgency === 'urgent' || m.urgency === 'high' ? toneBadge(label('urgency', m.urgency), m.urgency === 'urgent' ? 'bad' : 'waiting') : '') +
        (m.opportunity_score !== null && m.opportunity_score !== undefined
          ? toneBadge('Opportunity ' + m.opportunity_score + '/8', 'active') : '') +
      '</div>' +
      '<div class="sub">' + escHtml(truncate(m.title || m.text, 80)) + '</div></div>';
  });
  listEl.innerHTML = html;
  await selectReview(reviewIndex);
}

const SEVERE_CATEGORIES = ['adverse_event', 'legal_regulatory', 'privacy', 'billing_fraud'];

function queueRank(m) {
  if (m.protocol_route || SEVERE_CATEGORIES.includes(m.category)) return 0;
  if (m.protocol_decision === 'clinical_caution' || m.protocol_decision === 'hold') return 1;
  return 2;
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

// Urgency reason codes read as words; keyword regexes never reach the screen
// (the raw code stays in the tooltip).
const SCREEN_FLAGS = {adverse_event: 'adverse event', self_harm: 'self-harm', minor: 'minor', failed: 'could not run'};

function reasonText(reason) {
  const r = String(reason || '');
  if (r.startsWith('override:')) return 'Urgent keyword rule matched';
  const screen = r.match(/^safety_screen:(\w+)/);
  if (screen) return 'Independent safety check: ' + (SCREEN_FLAGS[screen[1]] || screen[1]);
  if (r.startsWith('severe_category')) {
    const rest = r.slice('severe_category'.length).replace(/^:\s*/, '');
    return 'Severe category' + (rest ? ': ' + rest : '');
  }
  if (r.startsWith('manual:')) return 'Escalated manually';
  if (r.startsWith('triage_failed')) return 'Triage failed';
  return r;
}

function auditNote(e) {
  const v = e.verdict || {};
  if (v.reason) return {text: reasonText(v.reason), raw: v.reason};
  if (['edited', 'approved', 'copied'].includes(e.event) && e.final_text) return {text: truncate(e.final_text, 160)};
  if (v.kind) return {text: LABELS.kind[v.kind] || label('category', v.kind), raw: v.kind};
  if (v.posted_url) return {text: 'posted at ' + v.posted_url};
  if (e.filter_result && e.filter_result.tier) return {text: label('tier', e.filter_result.tier), raw: e.filter_result.tier};
  if (v.verdict) return {text: label('verdict', v.verdict), raw: v.verdict};
  if (v.urgency_reason) return {text: reasonText(v.urgency_reason), raw: v.urgency_reason};
  return null;
}

function timeline(audit) {
  if (!audit || !audit.length) return '<p class="muted">No audit events yet.</p>';
  return '<div class="timeline">' + audit.map(e => {
    const note = auditNote(e);
    return '<div class="ev"><span class="when">' + formatDate(e.at) + '</span>' +
      '<span class="what" title="' + escHtml(e.event) + '">' + escHtml(label('event', e.event)) + '</span>' +
      '<span class="who">' + escHtml(e.actor) + '</span>' +
      (note ? '<div class="note"' + (note.raw ? ' title="' + escHtml(note.raw) + '"' : '') + '>' + escHtml(note.text) + '</div>' : '') +
      '</div>';
  }).join('') + '</div>';
}

function triageTags(m, t) {
  t = t || {};
  return '<div class="tag-row">' + labelTag('Category', 'category', t.category) + labelTag('Urgency', 'urgency', t.urgency) +
    labelTag('About', 'subject', t.subject_type) + labelTag('Sentiment', 'sentiment', t.sentiment) +
    tag('Competitor', t.competitor) + tag('Drug', t.drug) + tag('Product', t.product) + '</div>';
}

// The why line, for high/urgent mentions and anything escalated.
function detailWhy(d) {
  const u = d.triage && d.triage.urgency;
  return (u === 'urgent' || u === 'high' || (d.escalations || []).length) ? whyHtml(d.why) : '';
}

function mentionHead(m) {
  const author = authorText(m.platform, m.author_handle);
  return '<div class="to-line">' + platformHtml(m.platform) +
    (author ? '<span>' + escHtml(author) + '</span>' : '') +
    '<span>' + (m.posted_at ? 'posted ' + timeHtml(m.posted_at) : 'collected ' + timeHtml(m.collected_at)) + '</span>' +
    badge(m.status) + '</div>';
}

function draftAuthor(draft) {
  const model = String(draft.model || '');
  return model.startsWith('human:') ? 'edited by ' + model.slice(6) : 'drafted by Pulse';
}

function renderReviewPane() {
  const d = reviewDetail;
  const m = d.mention, draft = d.latest_draft, status = m.status;
  const inReview = status === 'in_review', approved = status === 'approved';
  const reviewer = can('review');
  const blockers = (d.approval && d.approval.blockers) || [];
  const claimOptions = (CLAIMS || []).map(c => '<option value="' + escHtml(c.id) + '">' + escHtml(c.id) +
    (c.publishable ? '' : ' (pending)') + ' — ' + escHtml(truncate(c.text, 70)) + '</option>').join('');
  let html = mentionHead(m) +
    '<div class="subject">' + escHtml(m.title || truncate(m.text, 90)) + '</div>' + detailWhy(d) +
    '<div class="body">' + escHtml(m.text) + '</div>' +
    '<p class="post-link">' + extLink(m.url, isDemoPost(m.url) ? 'Open demo post ↗' : 'Open original ↗') + '</p>' +
    triageTags(m, d.triage) + engagementHtml(d);

  html += '<div class="desk-block"><div class="subhead">Draft reply' + (draft ? ' · v' + escHtml(String(draft.version)) +
      ' · ' + escHtml(draftAuthor(draft)) : '') + '</div>' +
    '<textarea class="form-input editor" id="editor"' + (reviewer && (inReview || approved) ? '' : ' readonly') + '>' +
      escHtml(draft ? draft.text : '') + '</textarea>' +
    '<div class="desk-block"><div class="subhead">Claims</div><div id="claim-chips"></div>' +
    (reviewer && inReview ? '<div class="claim-add"><select class="form-input" id="claim-select">' + claimOptions +
      '</select><button class="btn btn-secondary btn-sm" data-action="claim-add">Add claim</button></div>' : '') + '</div>';

  html += trackedLinkHtml(d.tracked_link);

  if (draft) {
    html += '<div class="desk-block"><div class="subhead">Checks</div>' + tierBadge(draft.tier) + ' ' +
      verdictBadge(draft.review_verdict) +
      (draft.filter_hits.length ? '<ul class="hit-list">' + draft.filter_hits.map(h => '<li>' + escHtml(h) + '</li>').join('') + '</ul>' : '') +
      (draft.review_reasons.length ? '<ul class="hit-list">' + draft.review_reasons.map(r => '<li>' + escHtml(r) + '</li>').join('') + '</ul>' : '') +
      '</div>';
  }

  const clinicalOnly = !reviewer && can('approve_clinical') && d.approval && d.approval.clinical_required;
  if (clinicalOnly && inReview) {
    html += '<div class="desk-actions"><button class="btn btn-primary" id="approve-btn" data-action="approve"' +
      (blockers.length ? ' disabled' : '') + '>Approve (clinical)</button></div>';
    if (blockers.length) {
      html += '<ul class="blockers">' + blockers.map(b => '<li>Approve is blocked: ' + escHtml(b) + '</li>').join('') + '</ul>';
    }
  } else if (reviewer) {
    html += '<div class="desk-actions">';
    if (inReview) {
      html += '<button class="btn btn-secondary" data-action="save-edit">Save edit (re-check)</button>' +
        '<button class="btn btn-primary" id="approve-btn" data-action="approve"' + (blockers.length ? ' disabled' : '') + '>Approve</button>' +
        '<button class="btn btn-danger" data-action="reject">Reject…</button>';
    }
    if (approved) {
      const demo = isDemoPost(m.url);
      html += '<button class="btn btn-primary" data-action="copy-open">' +
          (demo ? 'Copy reply &amp; open demo post' : 'Copy reply &amp; open post') + '</button>' +
        '<button class="btn btn-secondary" data-action="mark-posted">Mark posted…</button>' +
        (demo ? '<button class="btn btn-demo" data-action="demo-post" title="DEMO only: posts into the local demo sandbox, never a real platform">' +
          'DEMO: Post to demo sandbox</button>' : '') +
        '<button class="btn btn-secondary" data-action="save-edit">Save edit (voids approval)</button>';
    }
    html += '<select class="form-input" id="escalate-kind">' + ESC_KINDS.map(k => '<option value="' + escHtml(k) + '" title="' + escHtml(k) + '">' + escHtml(label('kind', k)) + '</option>').join('') +
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

// Rules of engagement (docs/RULES-OF-ENGAGEMENT.md): situation and template,
// persona, community rules, the 80/20 share (a planning metric, never a gate),
// clinical approval, FINALIZE items, the competitor / switching protocol's
// decision, score and reasons, and whether an escalation really reached its owner.
const PARTICIPATION = {
  allowed: ['Brand replies allowed', 'good'], with_permission: ['Needs admin permission', 'waiting'],
  prohibited: ['Brand replies prohibited', 'bad'], unknown: ['Community rules unverified', 'waiting'],
  none: ['No community rules (platform rules apply)', 'idle'],
};

function engagementHtml(d) {
  const e = d.engagement;
  if (!e) return '';
  const sit = e.situation || {};
  const chips = [];
  chips.push(tag('Situation', sit.label));
  if (sit.template) chips.push(tag('Template', sit.template + (sit.template_name ? ' — ' + sit.template_name : '')));
  const g = e.guide;
  if (g && (g.mode === 'link' || g.mode === 'name') && g.title) {
    chips.push(tag('Guide', g.title + ' → ' + (g.chapter || '') + (g.mode === 'name' ? ' (named, no link)' : '')));
    if (g.drafted && !g.satisfied) {
      chips.push(toneBadge('Guide reference missing', 'bad'));
    }
  }
  if (e.persona) chips.push(tag('Speaking as', e.persona.persona === 'identified_employee'
    ? 'identified employee (' + e.persona.display_name + ')' : 'official account'));
  const c = e.community || {};
  const part = PARTICIPATION[c.participation] || [c.participation || 'unknown', 'idle'];
  chips.push(toneBadge(part[0] + (c.id ? ' · ' + c.name : ''), part[1]));
  if (c.links_allowed === false) chips.push(toneBadge('No links here', 'bad'));
  if (e.mix && e.mix.replies) {
    const pct = Math.round(e.mix.share * 100);
    chips.push('<span class="tag" title="Planning metric over the last ' + escHtml(String(e.mix.window)) +
      ' WellPeps replies here; never a per-reply quota (Protocol §1)"><span class="k">80/20</span>' +
      escHtml(String(e.mix.promotional)) + ' of ' + escHtml(String(e.mix.replies)) + ' promotional (' + escHtml(String(pct)) +
      '%)' + (e.mix.over ? ' · over 20%' : '') + '</span>');
  }
  if (e.draft_kind) chips.push(tag('This draft', e.draft_kind === 'promotion'
    ? 'promotion (' + (e.promotional_elements || []).join(', ') + ')' : 'education'));
  if (e.clinical_approval_required) chips.push(toneBadge('Clinical approval required', 'bad'));
  let html = '<div class="desk-block"><div class="subhead">Rules of engagement</div><div class="tag-row">' +
    chips.join('') + '</div>';
  if ((e.finalize || []).length) {
    html += '<ul class="hit-list">' + e.finalize.map(f => '<li>FINALIZE (' + escHtml(f.claim_id) + '): ' +
      escHtml(f.missing) + '</li>').join('') + '</ul>';
  }
  if (e.handoff) {
    html += '<p class="' + (e.handoff.paged || e.handoff.acked ? 'muted' : 'blockers') + '">Escalation handoff: ' +
      escHtml(e.handoff.status) + '</p>';
  }
  html += protocolHtml(e.protocol);
  return html + '</div>';
}

function protocolHtml(p) {
  if (!p) return '';
  const tone = {appropriate_alternative: 'good', educational_only: 'active', clinical_caution: 'waiting',
    escalate: 'bad', monitor_only: 'idle', hold: 'waiting', do_not_engage: 'bad'}[p.decision] || 'idle';
  const opp = p.opportunity;
  let html = '<div class="protocol"><div class="tag-row">' + toneBadge('Protocol: ' + (p.label || p.decision), tone);
  if (opp) {
    html += '<span class="tag" title="alternative intent ' + escHtml(String(opp.alternative_intent)) + ', need clarity ' +
      escHtml(String(opp.need_clarity)) + ', approved capability fit ' + escHtml(String(opp.capability_fit)) +
      ', useful contribution ' + escHtml(String(opp.useful_contribution)) + ' (each 0-2)"><span class="k">Opportunity</span>' +
      escHtml(String(opp.total)) + '/8 · ' + escHtml(String(opp.band || '')) + '</span>';
  } else {
    html += tag('Opportunity', 'not scored (gated)');
  }
  if (p.need_label) html += tag('Need', p.need_label);
  if (p.brand_mode) html += tag('WellPeps presence', String(p.brand_mode).replace(/_/g, ' '));
  if (p.required_review) html += tag('Review', p.required_review);
  const risks = p.risks || {};
  if (risks.competitor_claim_risk) html += tag('Competitor-claim risk', risks.competitor_claim_risk);
  if (risks.clinical_flag) html += toneBadge('Clinical flag', 'waiting');
  if (risks.privacy_flag) html += toneBadge('Privacy flag', 'waiting');
  html += '</div>';
  const reasons = (p.rationale || []).concat((p.blockers || []).map(b => 'Blocker: ' + b));
  if (reasons.length) html += '<ul class="hit-list">' + reasons.map(r => '<li>' + escHtml(r) + '</li>').join('') + '</ul>';
  if (p.brand_limits) html += '<p class="muted">' + escHtml(p.brand_limits) + '</p>';
  return html + '</div>';
}

// The registry link the draft carries: label, live / not-live badge, UTM tag.
function trackedLinkHtml(t) {
  if (!t) return '';
  const tag = t.utm_content ? '<span class="muted">utm_content=' + escHtml(t.utm_content) + '</span>'
    : '<span class="muted">no utm_content (not tracked)</span>';
  return '<div class="desk-block"><div class="subhead">Tracked link</div><div class="tracked-link">' +
    '<span class="claim-chip ' + (t.live ? 'ok' : 'pending') + '" title="' + escHtml(t.url || '') + '">Tracked link: ' +
    escHtml(t.label || t.id) + '</span> ' + (t.live ? toneBadge('Live', 'good') : toneBadge('Not live yet', 'bad')) + ' ' + tag +
    (t.live ? '' : '<p class="muted">Approval stays blocked until this page is live (pulse links check, then live: true in config/links.yaml).</p>') +
    '</div></div>';
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
  window.open(postUrl(url), '_blank', 'noopener,noreferrer');
  try { await copying; } catch { showToast('Could not copy automatically; select the text and copy it.', 'error'); return; }
  const r = await send('/api/mentions/' + currentMentionId() + '/copied');
  if (r.ok) {
    showToast(isDemoPost(url) ? 'Reply copied. Paste it in the demo post, then mark it posted with the comment link.'
      : 'Reply copied. Paste it on the post, then mark it posted.', 'success');
    loadReview(true);
  }
}

async function markPosted() {
  const ask = isDemoPost(reviewDetail.mention.url)
    ? 'Link to the reply you posted (in the demo sandbox: "Copy this comment link"):'
    : 'Link to the reply you posted (optional):';
  const url = window.prompt(ask, '');
  if (url === null) return;
  const r = await send('/api/mentions/' + currentMentionId() + '/mark-posted', {posted_url: url.trim()});
  if (r.ok) { showToast('Marked posted.', 'success'); loadReview(); }
}

// DEMO only: post the approved reply into the local sandbox as the brand
// account and mark it posted with the new comment's link (one human click).
async function demoPostCurrent() {
  if (!isDemoPost(reviewDetail.mention.url)) return;
  if (!window.confirm('DEMO: post this approved reply into the local demo sandbox as ' +
      (DEMO.brand_handle || 'the brand account') + ' and mark it posted?')) return;
  const r = await send('/api/mentions/' + currentMentionId() + '/demo-post');
  if (r.ok) {
    showToast('DEMO: posted to the demo sandbox and marked posted.', 'success');
    window.open(postUrl(r.data.posted_url), '_blank', 'noopener,noreferrer');
    loadReview();
  }
}

async function escalateCurrent() {
  const kind = document.getElementById('escalate-kind').value;
  if (!window.confirm('Escalate this mention as "' + label('kind', kind) + '"? The owner is paged.')) return;
  const r = await send('/api/mentions/' + currentMentionId() + '/escalate', {kind});
  if (r.ok) { showToast('Escalated' + (r.data.paged ? ' and paged.' : '; page not sent (check Slack).'), 'success'); loadReview(); pollUrgent(); }
}

// Options show the human label; the raw code is the value (and tooltip).
function fillSelect(id, values, group) {
  const sel = document.getElementById(id);
  if (!sel) return;
  for (const v of values) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.title = v;
    opt.textContent = label(group, v);
    sel.appendChild(opt);
  }
}

// The Feed, the mention drawer and the urgent banner live in feed.js.

// ── Users (admin) ──

async function loadUsers() {
  const el = document.getElementById('users-list');
  const users = await api('/api/users');
  if (!users) { el.innerHTML = offlineState(); return; }
  let html = '<div class="table-card"><table><thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead><tbody>';
  for (const u of users) {
    const self = ME && u.email === ME.email;
    html += '<tr><td>' + escHtml(u.email) + '</td><td>' + escHtml(u.display_name) + '</td><td>' + labelHtml('role', u.role) + '</td>' +
      '<td>' + (u.active ? toneBadge('Active', 'good') : toneBadge('Disabled', 'idle')) +
      (u.active && u.must_change_password ? ' ' + toneBadge(LABELS.pw.pending, 'waiting') : '') + '</td>' +
      '<td class="muted">' + formatDate(u.last_login_at) + '</td>' +
      '<td class="row-actions">' + (u.active && !self ? '<button class="btn btn-secondary btn-sm" data-action="reset-user" data-email="' + escHtml(u.email) + '">' +
        escHtml(LABELS.pw.reset_button) + '</button>' : '') +
      (u.active ? '<button class="btn btn-danger btn-sm" data-action="disable-user" data-email="' + escHtml(u.email) + '">Disable</button>' : '') + '</td></tr>';
  }
  el.innerHTML = html + '</tbody></table></div>';
}

async function createUser(form) {
  const body = {...Object.fromEntries(new FormData(form).entries()),
    must_change_password: form.elements.must_change_password.checked};
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
  'refresh': () => loadCurrentTab(),
  'logout': async () => { await request('/api/logout', {method: 'POST', body: {}}); window.location.assign('/login'); },
  'ack': el => ackEscalation(el.dataset.id),
  'view-details': el => openDrawer(el.dataset.id),
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
  'demo-post': () => demoPostCurrent(),
  'escalate': () => escalateCurrent(),
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

document.getElementById('user-form').addEventListener('submit', (event) => {
  event.preventDefault();
  createUser(event.target);
});

document.addEventListener('keydown', (event) => {
  if (document.querySelector('dialog[open]')) return;  // dialogs handle their own keys (password.js)
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
    '<span class="role" title="' + escHtml(ME.role) + '">' + escHtml(label('role', ME.role)) + '</span>' +
    '<span class="caret" aria-hidden="true">&#9662;</span>';
  if (ME.must_change_password) { enterForcedChange(); return; }  // nothing else loads until it's done
  document.getElementById('nav-users').classList.toggle('hidden', !can('admin'));
  await loadDemoFlags();
  route();
  if (currentTab !== 'urgent') pollUrgent();  // the nav badge and the Feed banner
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

// Local demo mode (never in production): the DEMO config banner, the sandbox
// banner, and the Review desk's demo labels.
async function loadDemoFlags() {
  DEMO = (await api('/api/demo')) || DEMO;
  document.getElementById('demo-config-banner').classList.toggle('hidden', !DEMO.demo_config);
  document.getElementById('demo-sandbox-banner').classList.toggle('hidden', !DEMO.sandbox);
}

async function loadReviewCount() {
  const data = await api('/api/mentions?status=in_review&limit=1');
  if (data) navCount('nav-review', data.total, false);
}

init();
