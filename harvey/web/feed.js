'use strict';

// WellPeps Pulse — the Feed (the home screen), the mention drawer, and the
// urgent banner on the Feed. Loaded after app.js and uses its helpers (api,
// escHtml, label, badge, tag, labelTag, platformHtml, timeHtml, emptyState,
// offlineState, showTab, ACTIONS, TAB_HASH, TAB_ROUTE). Same CSP rules as
// app.js: no inline style or handlers, server values through escHtml.
//
// Filters live in the URL hash (#feed?q=shipping&platform=reddit&sort=oldest)
// so a filtered Feed can be shared as a link.

let feedOffset = 0;
const FEED_PAGE = 50;
const FEED_FIELDS = ['q', 'status', 'platform', 'category', 'urgency', 'drug', 'product', 'competitor', 'sort'];

fillSelect('f-status', Object.keys(LABELS.status), 'status');
fillSelect('f-platform', PLATFORMS, 'platform');
fillSelect('f-category', CATEGORIES, 'category');
fillSelect('f-urgency', URGENCIES, 'urgency');

function feedForm() {
  return document.getElementById('feed-filters');
}

// Non-empty filters, without the default sort.
function feedFilters() {
  const params = new URLSearchParams();
  new FormData(feedForm()).forEach((v, k) => {
    const value = String(v).trim();
    if (value && !(k === 'sort' && value === 'newest')) params.set(k, value);
  });
  return params;
}

function feedHash() {
  const params = feedFilters();
  if (feedOffset) params.set('offset', String(feedOffset));
  const q = params.toString();
  return '#feed' + (q ? '?' + q : '');
}

function applyFeedQuery(query) {
  const params = new URLSearchParams(query || '');
  const form = feedForm();
  for (const name of FEED_FIELDS) {
    const field = form.elements[name];
    if (field) field.value = params.get(name) || (name === 'sort' ? 'newest' : '');
  }
  feedOffset = Math.max(0, parseInt(params.get('offset') || '0', 10) || 0);
}

TAB_HASH.feed = feedHash;
TAB_ROUTE.feed = applyFeedQuery;

// Open the Feed with exactly these filters (from Analytics or Pulse).
function showFeedWith(filters) {
  applyFeedQuery(new URLSearchParams(filters || {}).toString());
  showTab('feed');
}

function feedQuery() {
  const params = feedFilters();
  params.set('limit', String(FEED_PAGE));
  params.set('offset', String(feedOffset));
  return params.toString();
}

// ── Urgent banner (Feed) ──

function renderUrgentBanner(open, breached) {
  const el = document.getElementById('urgent-banner');
  if (!el) return;
  el.classList.toggle('hidden', !open);
  el.classList.toggle('bad', breached > 0);
  if (!open) { el.innerHTML = ''; return; }
  el.innerHTML = '<span class="ub-icon" aria-hidden="true">&#9888;</span>' +
    '<span class="ub-text"><b>' + escHtml(String(open)) + ' urgent item' + (open === 1 ? ' needs' : 's need') + ' attention</b>' +
    (breached ? ' · ' + escHtml(String(breached)) + ' SLA breached' : '') + '</span>' +
    '<a class="ub-link" href="#urgent">Open Urgent <span aria-hidden="true">&rarr;</span></a>';
}

// ── Feed ──

async function loadSummary() {
  const el = document.getElementById('feed-summary');
  const data = await api('/api/summary');
  if (!data) { el.innerHTML = ''; return; }
  const chips = Object.entries(data.mentions || {}).filter(([, n]) => n)
    .map(([k, n]) => '<span class="chip">' + escHtml(statusMeta(k).label) + ' <b>' + escHtml(String(n)) + '</b></span>');
  chips.unshift('<span class="chip">Total <b>' + escHtml(String(data.total || 0)) + '</b></span>');
  el.innerHTML = chips.join('');
}

function feedEmptyState() {
  if ([...feedFilters().keys()].length) {
    return '<div class="empty"><div class="glyph">&#9678;</div><div class="title">No mentions match these filters</div>' +
      '<div class="copy">Try a wider search, another platform, or clear the filters to see everything Pulse has collected.</div>' +
      '<div class="btn-group center"><button class="btn btn-secondary btn-sm" data-action="feed-clear">Clear filters</button></div></div>';
  }
  return emptyState('&#9678;', 'No mentions collected yet',
    'Collectors bring in public posts about WellPeps, competitors and the drugs we follow. Start the heartbeat on the Controls tab (admins), or run pulse ingest.');
}

async function loadFeed() {
  const el = document.getElementById('feed-list');
  if (currentTab === 'feed' && window.location.hash !== feedHash() &&
      !/^#(escalation|pulse-brief)-/.test(window.location.hash)) {
    history.replaceState(null, '', feedHash());
  }
  loadSummary();
  const data = await api('/api/mentions?' + feedQuery());
  const pager = document.getElementById('feed-pager');
  if (!data) { el.innerHTML = offlineState(); pager.innerHTML = ''; return; }
  if (!data.items.length) {
    el.innerHTML = feedEmptyState();
    pager.innerHTML = '';
    return;
  }
  let html = '<div class="table-card"><table><thead><tr>' +
    '<th>Collected</th><th>Platform</th><th>Mention</th><th>Triage</th><th>Status</th><th></th></tr></thead><tbody>';
  for (const m of data.items) {
    const body = m.title ? m.title + ' — ' + (m.text || '') : (m.text || '');
    html += '<tr class="clickable" data-action="open-drawer" data-id="' + escHtml(String(m.id)) + '">' +
      '<td class="muted nowrap">' + formatDate(m.collected_at) + '</td>' +
      '<td class="nowrap">' + platformHtml(m.platform) + '</td>' +
      '<td class="mention-cell">' + escHtml(truncate(body, 200)) + '</td>' +
      '<td><div class="tag-row tight">' + labelTag('', 'category', m.category) + labelTag('', 'urgency', m.urgency === 'urgent' || m.urgency === 'high' ? m.urgency : '') +
        tag('Drug', m.drug) + tag('', m.competitor) + '</div></td>' +
      '<td>' + badge(m.status) + '</td>' +
      '<td class="nowrap">' + extLink(m.url, isDemoPost(m.url) ? 'Open demo post ↗' : 'Open ↗') + '</td></tr>';
  }
  el.innerHTML = html + '</tbody></table></div>';
  const end = Math.min(data.offset + data.items.length, data.total);
  pager.innerHTML = '<span>' + escHtml(String(data.offset + 1)) + '–' + escHtml(String(end)) + ' of ' + escHtml(String(data.total)) + '</span>' +
    '<button class="btn btn-secondary btn-sm" data-action="feed-prev"' + (data.offset > 0 ? '' : ' disabled') + '>Previous</button>' +
    '<button class="btn btn-secondary btn-sm" data-action="feed-next"' + (end < data.total ? '' : ' disabled') + '>Next</button>';
}

// ── Drawer ──

async function openDrawer(id) {
  const drawer = document.getElementById('drawer');
  const d = await api('/api/mentions/' + encodeURIComponent(id));
  if (!d) return;
  const m = d.mention;
  let html = '<div class="drawer-head"><span class="drawer-id">Mention #' + escHtml(String(m.id)) + '</span>' +
    '<button class="btn btn-secondary btn-sm" data-action="close-drawer">Close</button></div>' + mentionHead(m) +
    '<h2 class="subject">' + escHtml(m.title || truncate(m.text, 90)) + '</h2>' + detailWhy(d) +
    '<div class="body">' + escHtml(m.text) + '</div><p class="post-link">' + extLink(m.url, isDemoPost(m.url) ? 'Open demo post ↗' : 'Open original ↗') + '</p>' +
    triageTags(m, d.triage) +
    (d.triage && d.triage.urgency_reason ? '<p class="reasoning"><span class="k">Triage reasoning</span> ' +
      '<span title="' + escHtml(d.triage.urgency_reason) + '">' + escHtml(reasonText(d.triage.urgency_reason)) + '</span></p>' : '');
  if (d.latest_draft) {
    html += '<div class="desk-block"><div class="subhead">Latest draft · v' + escHtml(String(d.latest_draft.version)) +
      ' · ' + escHtml(draftAuthor(d.latest_draft)) + '</div>' +
      '<div class="body">' + escHtml(d.latest_draft.text) + '</div>' + tierBadge(d.latest_draft.tier) + ' ' +
      verdictBadge(d.latest_draft.review_verdict) + '</div>';
  }
  if (d.escalations.length) {
    html += '<div class="desk-block"><div class="subhead">Escalations</div>' + d.escalations.map(e =>
      '<div class="esc-row"><span title="' + escHtml(e.kind) + '">#' + escHtml(String(e.id)) + ' ' + escHtml(label('kind', e.kind)) + '</span>' +
      ownerHtml(e.owner) + '<span>SLA ' + timeHtml(e.sla_due_at) + '</span>' +
      (e.breached ? toneBadge('SLA breached', 'bad') : '') +
      (e.acked_at ? toneBadge('Acknowledged by ' + e.acked_by, 'good') : toneBadge('Open', 'waiting')) + '</div>').join('') + '</div>';
  }
  html += '<div class="desk-block"><div class="subhead">Audit trail</div>' + timeline(d.audit) + '</div>';
  drawer.innerHTML = html;
  drawer.classList.remove('hidden');
  const closeBtn = drawer.querySelector('[data-action="close-drawer"]');
  if (closeBtn) closeBtn.focus();
}

function closeDrawer() {
  document.getElementById('drawer').classList.add('hidden');
}

// ── Events ──

Object.assign(ACTIONS, {
  'open-drawer': el => openDrawer(el.dataset.id),
  'close-drawer': () => closeDrawer(),
  'feed-prev': () => { feedOffset = Math.max(0, feedOffset - FEED_PAGE); loadFeed(); },
  'feed-next': () => { feedOffset += FEED_PAGE; loadFeed(); },
  'feed-clear': () => { applyFeedQuery(''); loadFeed(); },
});

feedForm().addEventListener('submit', (event) => {
  event.preventDefault();
  feedOffset = 0;
  loadFeed();
});

document.getElementById('f-sort').addEventListener('change', () => {
  feedOffset = 0;
  loadFeed();
});
