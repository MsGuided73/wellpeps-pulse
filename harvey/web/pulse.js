'use strict';

// WellPeps Pulse — the Pulse tab (Phase 8). Loaded after app.js and uses its
// helpers (api, send, escHtml, tag, toneBadge, emptyState, applyWidths, can,
// showTab, ACTIONS). Same rules as app.js: no inline style or handlers, every
// server value through escHtml, and model-written text (headline, summary,
// action cards) is rendered as escaped text; the summary's **bold** and "- "
// lists go through the tiny whitelist renderer below, never as raw HTML.

let pulseView = 'brief';
let pulseBriefId = (window.location.hash.match(/^#pulse-brief-(\d+)$/) || [])[1] || null;
let bankOffset = 0;
let bankItems = [];
const BANK_PAGE = 50;
const OWNER_TONE = {compliance: 'bad', clinical: 'bad', leadership: 'note', marketing: 'active', product: 'active', support: 'waiting'};
const URGENCY_TONE = {this_week: 'bad', this_month: 'waiting', watch: 'idle'};

// ── Small renderers ──

function renderMd(text) {
  // Whitelist: paragraphs, "- "/"* " bullet lists, **bold**. Escape first,
  // then add only <p>, <ul>, <li>, <b>, <br>.
  const bold = s => escHtml(s).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  return String(text || '').split(/\n\s*\n/).map(block => {
    const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) return '';
    if (lines.every(l => /^[-*]\s+/.test(l))) {
      return '<ul>' + lines.map(l => '<li>' + bold(l.replace(/^[-*]\s+/, '')) + '</li>').join('') + '</ul>';
    }
    return '<p>' + lines.map(bold).join('<br>') + '</p>';
  }).join('');
}

function fmtNum(n, digits) {
  if (n === null || n === undefined || isNaN(Number(n))) return '—';
  return Number(n).toFixed(digits === undefined ? 0 : digits);
}

function deltaChip(value, unit, digits) {
  if (value === null || value === undefined) return '<span class="delta muted">new</span>';
  const v = Number(value);
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
  const sign = v > 0 ? '+' : v < 0 ? '−' : '±';
  return '<span class="delta ' + cls + '">' + sign + escHtml(fmtNum(Math.abs(v), digits)) + escHtml(unit || '') + '</span>';
}

function windowLabel(b) {
  const start = String(b.window_start || '').slice(0, 10);
  return b.period === 'weekly' ? 'week of ' + start : start;
}

function termCell(term) {
  return '<button class="term-link" data-action="pulse-term" data-term="' + escHtml(term) + '" title="Show matching mentions in the Feed">' +
    escHtml(term) + '</button>';
}

function block(title, inner, note) {
  return '<div class="card pulse-card"><h2>' + escHtml(title) + '</h2>' +
    (note ? '<p class="lede">' + escHtml(note) + '</p>' : '') + inner + '</div>';
}

// ── Aggregate tables (shared by a stored brief and live trends) ──

function termsTable(terms) {
  if (!terms || !terms.length) return '<p class="muted">No term reached the minimum count in this window.</p>';
  return '<div class="table-card"><table><thead><tr><th>Term</th><th class="num">Mentions</th>' +
    '<th class="num">Baseline</th><th class="num">Velocity</th><th></th></tr></thead><tbody>' +
    terms.map(t => '<tr><td>' + termCell(t.term) + '</td><td class="num">' + escHtml(String(t.count)) + '</td>' +
      '<td class="num muted">' + escHtml(String(t.baseline_count)) + '</td>' +
      '<td class="num">' + escHtml(fmtNum(t.velocity, 1)) + '×</td>' +
      '<td>' + (t.is_new ? toneBadge('NEW', 'note') : '') + '</td></tr>').join('') +
    '</tbody></table></div>';
}

function shareBars(rows) {
  if (!rows || !rows.length) return '<p class="muted">No brand-level mentions in this window.</p>';
  return rows.map(r => '<div class="bar-row"><span class="bar-label">' + escHtml(r.subject) + '</span>' +
    '<div class="bar-track"><div class="bar-fill" data-width="' + escHtml(fmtNum(r.share * 100, 1)) + '"></div></div>' +
    '<span class="bar-val">' + escHtml(fmtNum(r.share * 100, 1)) + '% <span class="muted">(' + escHtml(String(r.count)) + ')</span> ' +
    deltaChip(r.prev_count ? r.delta * 100 : null, 'pp', 1) + '</span></div>').join('');
}

function sentimentTable(rows) {
  if (!rows || !rows.length) return '<p class="muted">No scored mentions in this window.</p>';
  return '<div class="table-card"><table><thead><tr><th>Subject</th><th></th><th class="num">Mean</th>' +
    '<th class="num">Previous</th><th class="num">Change</th><th class="num">n</th></tr></thead><tbody>' +
    rows.map(r => '<tr><td>' + escHtml(r.subject) + '</td><td class="muted">' + escHtml(r.kind) + '</td>' +
      '<td class="num">' + escHtml(fmtNum(r.mean, 2)) + '</td><td class="num muted">' + escHtml(fmtNum(r.prev_mean, 2)) + '</td>' +
      '<td class="num">' + (r.mean === null ? '' : deltaChip(r.delta, '', 2)) + '</td>' +
      '<td class="num muted">' + escHtml(String(r.n)) + '</td></tr>').join('') + '</tbody></table></div>';
}

function mixList(rows, key) {
  if (!rows || !rows.length) return '<p class="muted">Nothing yet.</p>';
  return '<div class="mix">' + rows.map(r => '<div class="mix-row"><span>' + escHtml(String(r[key]).replace(/_/g, ' ')) + '</span>' +
    '<span class="num">' + escHtml(String(r.count)) + ' ' + deltaChip(r.delta, '', 0) + '</span></div>').join('') + '</div>';
}

function themesList(themes) {
  if (!themes || !themes.length) return '<p class="muted">No repeated complaint themes in this window.</p>';
  return themes.map(t => '<div class="theme"><div class="theme-head">' + escHtml(t.subject) +
    ' <span class="muted">· ' + escHtml(String(t.mentions)) + ' complaint(s)</span></div><div class="tag-row">' +
    (t.terms.length ? t.terms.map(x => '<span class="tag">' + escHtml(x.term) + ' <span class="k">' + escHtml(String(x.count)) + '</span></span>').join('')
      : '<span class="muted">no repeated terms</span>') + '</div></div>').join('');
}

function renderTables(data, terms) {
  return '<div class="pulse-grid">' +
    block('Emerging terms', termsTable(terms), 'Ranked by velocity against the baseline × volume. Click a term to see the mentions.') +
    block('Share of voice', shareBars(data.share_of_voice), 'WellPeps and competitors, change vs the previous window in points.') +
    block('Sentiment', sentimentTable(data.sentiment), 'Mean triage sentiment, −1 to 1.') +
    block('Complaint themes', themesList(data.complaint_themes)) +
    block('Category mix', mixList(data.category_mix, 'category')) +
    block('Drug mix', mixList(data.drug_mix, 'drug')) +
    '</div>';
}

// ── Brief view ──

function actionCard(c) {
  return '<div class="action-card"><div class="ac-title">' + escHtml(c.title) + '</div>' +
    '<div class="ac-chips">' + toneBadge(c.owner_hint, OWNER_TONE[c.owner_hint] || 'idle') + ' ' +
    toneBadge(String(c.urgency || '').replace(/_/g, ' '), URGENCY_TONE[c.urgency] || 'idle') + '</div>' +
    '<p class="ac-why">' + escHtml(c.why) + '</p><p class="ac-action"><b>Do:</b> ' + escHtml(c.action) + '</p>' +
    ((c.evidence_terms || []).length ? '<div class="tag-row">' + c.evidence_terms.map(t => '<span class="tag">' + escHtml(t) + '</span>').join('') + '</div>' : '') +
    '</div>';
}

function renderBrief(detail) {
  const data = detail.data || {};
  const fallback = detail.status === 'fallback';
  let html = '<div class="brief-head"><div class="to-line">' + toneBadge(detail.period, 'active') + ' ' +
    escHtml(windowLabel(detail)) + ' · ' + escHtml(String(data.mentions || 0)) + ' mention(s)' +
    (fallback ? ' ' + toneBadge('tables only', 'waiting') : '') +
    (detail.slack_sent_at ? ' ' + toneBadge('sent to Slack', 'good') : '') + '</div>' +
    '<h3 class="brief-headline">' + escHtml(detail.headline) + '</h3>' +
    '<div class="md">' + renderMd(detail.summary_md) + '</div></div>';
  const cards = detail.action_cards || [];
  if (cards.length) html += '<div class="subhead">Action cards</div><div class="cards-grid">' + cards.map(actionCard).join('') + '</div>';
  const stripped = (data.verification && data.verification.stripped) || [];
  if (stripped.length) {
    html += '<p class="muted">' + escHtml(String(stripped.length)) + ' card(s) removed because they cited numbers that are not in the data.</p>';
  }
  if ((detail.watchlist || []).length) {
    html += '<div class="subhead">Watchlist</div><div class="tag-row">' + detail.watchlist.map(t => '<span class="tag">' + termCell(t) + '</span>').join('') + '</div>';
  }
  html += '<div class="subhead">The numbers behind it</div>' + renderTables(data, detail.trend_terms || data.terms);
  return html;
}

async function loadBriefView() {
  const el = document.getElementById('pulse-brief');
  document.getElementById('pulse-generate-tools').classList.toggle('hidden', !can('admin'));
  const list = await api('/api/briefs?limit=60');
  if (!list) { el.innerHTML = offlineState(); return; }
  const sel = document.getElementById('pulse-history');
  sel.innerHTML = list.items.map(b => '<option value="' + escHtml(String(b.id)) + '">' + escHtml(b.period + ' · ' + windowLabel(b)) +
    (b.status === 'fallback' ? ' (tables only)' : '') + '</option>').join('');
  if (!list.items.length) {
    el.innerHTML = emptyState('&#9678;', 'No brief yet',
      'The heartbeat writes a daily brief each morning and a weekly one on Mondays, once there are triaged mentions. Admins can generate one now; Live trends works without a brief.');
    return;
  }
  const wanted = list.items.some(b => String(b.id) === String(pulseBriefId)) ? String(pulseBriefId) : String(list.items[0].id);
  sel.value = wanted;
  const detail = await api('/api/briefs/' + encodeURIComponent(wanted));
  if (!detail) { el.innerHTML = offlineState(); return; }
  pulseBriefId = wanted;
  el.innerHTML = renderBrief(detail);
  applyWidths(el);
}

async function generateBrief() {
  const period = document.getElementById('pulse-period').value;
  showToast('Writing the ' + period + ' brief…', 'success');
  let r = await send('/api/briefs/generate', {period});
  if (r.ok && !r.data.created &&
      window.confirm('A ' + period + ' brief for this window already exists. Regenerate it (one Claude call)?')) {
    r = await send('/api/briefs/generate', {period, force: true});
  }
  if (r.ok) { pulseBriefId = String(r.data.id); loadBriefView(); }
}

// ── Live trends ──

async function loadLiveView() {
  const el = document.getElementById('pulse-live');
  const days = document.getElementById('pulse-days').value;
  const data = await api('/api/trends?days=' + encodeURIComponent(days));
  if (!data) { el.innerHTML = offlineState(); return; }
  el.innerHTML = '<p class="muted">' + escHtml(String(data.mentions)) + ' triaged mention(s) in the window, ' +
    escHtml(String(data.previous_mentions)) + ' in the window before. Computed now; no Claude call.</p>' + renderTables(data, data.terms);
  applyWidths(el);
}

// ── Language bank ──

function bankQuery() {
  const params = new URLSearchParams();
  new FormData(document.getElementById('bank-filters')).forEach((v, k) => { if (String(v).trim()) params.set(k, String(v).trim()); });
  params.set('limit', String(BANK_PAGE));
  params.set('offset', String(bankOffset));
  return params.toString();
}

async function loadBankView() {
  const el = document.getElementById('bank-list');
  const pager = document.getElementById('bank-pager');
  const data = await api('/api/language-bank?' + bankQuery());
  if (!data) { el.innerHTML = offlineState(); pager.innerHTML = ''; return; }
  bankItems = data.items;
  if (!data.items.length) {
    el.innerHTML = emptyState('&#10077;', 'No phrases yet', 'Triage keeps short verbatim phrases from relevant posts; they collect here once mentions are triaged.');
    pager.innerHTML = '';
    return;
  }
  el.innerHTML = '<div class="table-card"><table><thead><tr><th>Phrase</th><th>Product / drug</th><th>Category</th>' +
    '<th class="num">Mentions</th><th>First seen</th><th>Last seen</th><th></th></tr></thead><tbody>' +
    data.items.map((p, i) => '<tr><td class="phrase">“' + escHtml(p.phrase) + '”</td><td>' + escHtml(p.scope) + '</td>' +
      '<td>' + escHtml(String(p.category || '').replace(/_/g, ' ')) + '</td><td class="num">' + escHtml(String(p.count)) + '</td>' +
      '<td class="muted">' + formatDate(p.first_seen) + '</td><td class="muted">' + formatDate(p.last_seen) + '</td>' +
      '<td><button class="btn btn-secondary btn-sm" data-action="bank-copy" data-index="' + i + '">Copy</button></td></tr>').join('') +
    '</tbody></table></div>';
  const end = Math.min(data.offset + data.items.length, data.total);
  pager.innerHTML = '<span>' + escHtml(String(data.offset + 1)) + '–' + escHtml(String(end)) + ' of ' + escHtml(String(data.total)) + '</span>' +
    '<button class="btn btn-secondary btn-sm" data-action="bank-prev"' + (data.offset > 0 ? '' : ' disabled') + '>Previous</button>' +
    '<button class="btn btn-secondary btn-sm" data-action="bank-next"' + (end < data.total ? '' : ' disabled') + '>Next</button>';
}

async function copyPhrase(index) {
  const item = bankItems[index];
  if (!item) return;
  try {
    await navigator.clipboard.writeText(item.phrase);
    showToast('Phrase copied.', 'success');
  } catch {
    showToast('Could not copy automatically; select the phrase and copy it.', 'error');
  }
}

// ── View switching ──

function setPulseView(view) {
  pulseView = view;
  document.querySelectorAll('#pulse .seg button').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  document.getElementById('pulse-brief').classList.toggle('hidden', view !== 'brief');
  document.getElementById('pulse-live').classList.toggle('hidden', view !== 'live');
  document.getElementById('pulse-bank').classList.toggle('hidden', view !== 'bank');
  document.getElementById('pulse-brief-tools').classList.toggle('hidden', view !== 'brief');
  document.getElementById('pulse-live-tools').classList.toggle('hidden', view !== 'live');
  loadPulse();
}

function loadPulse() {
  if (pulseView === 'live') loadLiveView();
  else if (pulseView === 'bank') loadBankView();
  else loadBriefView();
}

function showTermInFeed(term) {
  const q = document.getElementById('f-q');
  if (q) q.value = term;
  feedOffset = 0;
  showTab('feed');
}

fillSelect('b-category', CATEGORIES);

Object.assign(ACTIONS, {
  'pulse-view': el => setPulseView(el.dataset.view),
  'pulse-generate': () => generateBrief(),
  'pulse-term': el => showTermInFeed(el.dataset.term),
  'bank-copy': el => copyPhrase(Number(el.dataset.index)),
  'bank-prev': () => { bankOffset = Math.max(0, bankOffset - BANK_PAGE); loadBankView(); },
  'bank-next': () => { bankOffset += BANK_PAGE; loadBankView(); },
});

document.getElementById('pulse-history').addEventListener('change', (event) => {
  pulseBriefId = event.target.value;
  loadBriefView();
});

document.getElementById('pulse-days').addEventListener('change', () => loadLiveView());

document.getElementById('bank-filters').addEventListener('submit', (event) => {
  event.preventDefault();
  bankOffset = 0;
  loadBankView();
});
