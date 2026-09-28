'use strict';

// WellPeps Pulse — the Analytics tab. Loaded after app.js and charts.js; uses
// app.js helpers (api, escHtml, label, emptyState, offlineState, showTab,
// ACTIONS) and the Charts module. Same CSP rules as app.js: no inline style or
// handlers; server values through escHtml or textContent.
//
// Filter state lives in the URL hash (#analytics?days=30&platform=reddit,x…)
// so a view can be shared. Every chart re-fetches against the same slice.
//
// Color follows the entity, never its rank: WellPeps is always slot 1 (brand
// blue), the server's "anchor" competitors take slots 2-7 in anchor order,
// anything else folds into gray "Other". The eight hues are the dataviz
// reference palette with slot 1 swapped for WellPeps blue, validated with
// scripts/validate_palette.js on white: adjacent CVD ΔE >= 9.1, normal-vision
// >= 19.6. Aqua, yellow and pink sit under 3:1 on white, so every chart keeps
// a legend, tooltips and a table view (the relief channel).

const AN_PALETTE = ['#1576C4', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
const AN_OTHER = '#A7B2C0';
const AN_RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6',
  '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b'];
const AN_RAMP_DARK_FROM = 7;  // ramp steps from here on take white cell labels
const AN_CATEGORY_COLORS = {complaint: 1, praise: 2, question: 3, purchase_intent: 4, misinformation: 5, safety_legal: 6};
const AN_PLATFORM_COLORS = {reddit: 1, x: 2, instagram: 3, tiktok: 4, youtube: 5, facebook: 6, trustpilot: 7, google_reviews: 0};
const AN_KIND_COLORS = {adverse_event: 1, legal: 2, privacy: 3, billing_fraud: 4, viral_negative: 5};
const AN_SERIES_LABELS = {safety_legal: 'Safety & legal', other: 'Other'};
const AN_DEFAULTS = {days: '30', from: '', to: '', bucket: 'auto', platform: [], competitor: [], drug: '', category: ''};
const AN_CARDS = [
  {id: 'volume', title: 'Mention volume over time', wide: true},
  {id: 'share', title: 'Share of voice over time'},
  {id: 'sentiment', title: 'Sentiment trend'},
  {id: 'complaints', title: 'Competitor complaint themes', wide: true},
  {id: 'emerging', title: 'Emerging terms'},
  {id: 'escalations', title: 'Urgent escalations & SLA'},
  {id: 'drugs', title: 'Drug & topic momentum', wide: true},
];

let anState = Object.assign({}, AN_DEFAULTS);
let anOptions = null;
let anData = {};
let anVolumeBy = 'category';
let anSeq = 0;
let anWidth = 0;
const anFreeColors = {};

// ── Colors ──

function anEntityColor(name) {
  if (name === 'WellPeps') return AN_PALETTE[0];
  if (name === 'Other') return AN_OTHER;
  const anchors = (anOptions && anOptions.anchors) || [];
  const i = anchors.indexOf(name);
  if (i >= 0 && i < 6) return AN_PALETTE[i + 1];
  if (!(name in anFreeColors)) {
    const used = new Set(anchors.slice(0, 6).map((_, k) => k + 1).concat(Object.values(anFreeColors)));
    const free = [7, 6, 5, 4, 3, 2, 1].find(k => !used.has(k));
    anFreeColors[name] = free === undefined ? -1 : free;
  }
  return anFreeColors[name] >= 0 ? AN_PALETTE[anFreeColors[name]] : AN_OTHER;
}

function anMapColor(map, key) {
  return key in map ? AN_PALETTE[map[key]] : AN_OTHER;
}

// ── URL state ──

function anReadHash() {
  const m = window.location.hash.match(/^#analytics\?(.*)$/);
  const q = new URLSearchParams(m ? m[1] : '');
  const list = k => (q.get(k) || '').split(',').map(s => s.trim()).filter(Boolean);
  anState = {
    days: q.get('days') || (q.get('from') ? 'custom' : AN_DEFAULTS.days), from: q.get('from') || '',
    to: q.get('to') || '', bucket: q.get('bucket') || 'auto', platform: list('platform'),
    competitor: list('competitor'), drug: q.get('drug') || '', category: q.get('category') || '',
  };
  anVolumeBy = q.get('by') === 'platform' ? 'platform' : 'category';
}

function anQuery(includeView) {
  const q = new URLSearchParams();
  if (anState.days === 'custom') { q.set('from', anState.from); q.set('to', anState.to); }
  else q.set('days', anState.days);
  if (anState.bucket !== 'auto') q.set('bucket', anState.bucket);
  if (anState.platform.length) q.set('platform', anState.platform.join(','));
  if (anState.competitor.length) q.set('competitor', anState.competitor.join(','));
  if (anState.drug) q.set('drug', anState.drug);
  if (anState.category) q.set('category', anState.category);
  if (includeView && anVolumeBy !== 'category') q.set('by', anVolumeBy);
  return q.toString();
}

function anWriteHash() {
  const hash = '#analytics?' + anQuery(true);
  if (window.location.hash !== hash) history.replaceState(null, '', hash);
}

TAB_HASH.analytics = () => '#analytics?' + anQuery(true);

// ── Filters ──

function anCheckList(id, values, selected, labelOf) {
  const box = document.getElementById(id);
  box.innerHTML = values.map(v => '<label class="check"><input type="checkbox" value="' + escHtml(v) + '"' +
    (selected.includes(v) ? ' checked' : '') + '> <span>' + escHtml(labelOf(v)) + '</span></label>').join('');
}

function anSummaryText(values, labelOf, none) {
  if (!values.length) return none;
  return values.length <= 2 ? values.map(labelOf).join(', ') : values.length + ' selected';
}

function anSyncForm() {
  const o = anOptions || {platforms: [], competitors: [], drugs: [], categories: []};
  document.getElementById('an-range').value = anState.days === 'custom' ? 'custom' :
    (['7', '30', '90'].includes(anState.days) ? anState.days : '30');
  document.getElementById('an-from').value = anState.from;
  document.getElementById('an-to').value = anState.to;
  document.getElementById('an-custom').classList.toggle('hidden', anState.days !== 'custom');
  document.getElementById('an-bucket').value = anState.bucket;
  anCheckList('an-platform-list', o.platforms, anState.platform, v => label('platform', v));
  anCheckList('an-competitor-list', o.competitors, anState.competitor, v => v);
  document.getElementById('an-platform-sum').textContent = anSummaryText(anState.platform, v => label('platform', v), 'All platforms');
  document.getElementById('an-competitor-sum').textContent = anSummaryText(anState.competitor, v => v, 'Top competitors');
  const drug = document.getElementById('an-drug');
  drug.innerHTML = '<option value="">All drugs &amp; topics</option>' + o.drugs.map(d => '<option value="' + escHtml(d) + '">' + escHtml(d) + '</option>').join('');
  drug.value = anState.drug;
  const cat = document.getElementById('an-category');
  cat.innerHTML = '<option value="">All categories</option>' + o.categories.map(c => '<option value="' + escHtml(c) + '">' + escHtml(label('category', c)) + '</option>').join('');
  cat.value = anState.category;
}

function anReadForm() {
  const range = document.getElementById('an-range').value;
  const checked = id => Array.from(document.querySelectorAll('#' + id + ' input:checked')).map(i => i.value);
  anState = {
    days: range, from: document.getElementById('an-from').value, to: document.getElementById('an-to').value,
    bucket: document.getElementById('an-bucket').value, platform: checked('an-platform-list'),
    competitor: checked('an-competitor-list').slice(0, 8), drug: document.getElementById('an-drug').value,
    category: document.getElementById('an-category').value,
  };
}

function anFiltersChanged() {
  anReadForm();
  document.getElementById('an-custom').classList.toggle('hidden', anState.days !== 'custom');
  if (anState.days === 'custom' && !(anState.from && anState.to)) return;  // wait for both dates
  anSyncForm();
  anWriteHash();
  anLoadAll();
}

// ── Cards ──

function anBuildCards() {
  const grid = document.getElementById('an-grid');
  if (grid.dataset.built) return;
  grid.innerHTML = AN_CARDS.map(c => '<section class="card an-card' + (c.wide ? ' wide' : '') + '" id="an-card-' + c.id + '" aria-labelledby="an-title-' + c.id + '">' +
    '<div class="an-card-head"><div><h3 class="an-title" id="an-title-' + c.id + '">' + escHtml(c.title) + '</h3>' +
    '<p class="an-takeaway" id="an-take-' + c.id + '">Loading…</p></div>' +
    '<div class="an-card-tools">' + (c.id === 'volume' ? '<div class="seg seg-sm" role="group" aria-label="Stack volume by">' +
      '<button type="button" data-action="an-by" data-by="category">Category</button><button type="button" data-action="an-by" data-by="platform">Platform</button></div>' : '') +
    '<button type="button" class="btn btn-ghost btn-sm" data-action="an-table" data-card="' + c.id + '" aria-pressed="false">View as table</button></div></div>' +
    '<div class="an-extra" id="an-extra-' + c.id + '"></div>' +
    '<div class="an-plot" id="an-plot-' + c.id + '" tabindex="0" role="img" aria-describedby="an-take-' + c.id + '"></div>' +
    '<div class="an-legend" id="an-legend-' + c.id + '"></div>' +
    '<div class="an-table hidden" id="an-table-' + c.id + '"></div>' +
    '<p class="an-note muted" id="an-note-' + c.id + '"></p></section>').join('');
  grid.dataset.built = '1';
}

function anSetTake(id, text) {
  document.getElementById('an-take-' + id).textContent = text || '';
  const plot = document.getElementById('an-plot-' + id);
  plot.setAttribute('aria-label', AN_CARDS.find(c => c.id === id).title + '. Use the arrow keys to read each point.');
}

function anEmpty(id, text) {
  const plot = document.getElementById('an-plot-' + id);
  Charts.reset(plot);
  plot.innerHTML = '<div class="an-empty">' + escHtml(text) + '</div>';
  document.getElementById('an-legend-' + id).innerHTML = '';
}

function anSeriesLabel(key, by) {
  if (by === 'platform') return key === 'other' ? 'Other' : label('platform', key);
  return AN_SERIES_LABELS[key] || label('category', key);
}

function anBucketRows(labels, series, fmt, bucket) {
  return labels.map((k, i) => [Charts.bucketLabel(k, bucket, true)].concat(series.map(s => fmt(s.values[i], s, i))));
}

// a. Volume
function anRenderVolume(d) {
  const by = d.by;
  const colorOf = k => k === 'other' ? AN_OTHER : anMapColor(by === 'platform' ? AN_PLATFORM_COLORS : AN_CATEGORY_COLORS, k);
  const series = d.series.map(s => ({label: anSeriesLabel(s.key, by), color: colorOf(s.key), values: s.values, total: s.total}));
  document.querySelectorAll('[data-action="an-by"]').forEach(b => b.classList.toggle('active', b.dataset.by === by));
  if (!d.total) return anEmpty('volume', 'No relevant mentions match these filters in this period.');
  Charts.columns(document.getElementById('an-plot-volume'), {labels: d.buckets, bucket: d.params.bucket, series, height: 260});
  Charts.legend(document.getElementById('an-legend-volume'), series.map(s => ({label: s.label, color: s.color, detail: Charts.fmtInt(s.total)})));
  Charts.table(document.getElementById('an-table-volume'),
    [{label: d.params.bucket === 'week' ? 'Week' : 'Day'}].concat(series.map(s => ({label: s.label, num: true})), [{label: 'Total', num: true}]),
    anBucketRows(d.buckets, series, v => Charts.fmtInt(v), d.params.bucket).map((r, i) => r.concat(Charts.fmtInt(d.totals[i]))),
    'Mention volume by ' + by);
}

// b. Share of voice
function anRenderShare(d) {
  const series = d.series.map(s => ({label: s.key, color: anEntityColor(s.key), values: s.values.map(v => v || 0), raw: s}));
  if (!d.total) return anEmpty('share', 'No brand-level mentions (WellPeps or a competitor) in this period.');
  Charts.columns(document.getElementById('an-plot-share'), {labels: d.buckets, bucket: d.params.bucket, series, percent: true, height: 240,
    format: (v, s, i) => Charts.fmtPct(v) + ' (' + Charts.fmtInt(s.raw.counts[i]) + ')'});
  Charts.legend(document.getElementById('an-legend-share'), series.map(s => ({label: s.label, color: s.color,
    detail: Charts.fmtPct(s.raw.share) + (s.raw.delta_pts === null ? '' : ' ' + Charts.fmtSigned(s.raw.delta_pts, 1) + ' pts')})));
  Charts.table(document.getElementById('an-table-share'),
    [{label: 'Brand'}, {label: 'Mentions', num: true}, {label: 'Share', num: true}, {label: 'Previous', num: true}, {label: 'Change (pts)', num: true}],
    d.series.map(s => [s.key, Charts.fmtInt(s.total), Charts.fmtPct(s.share), Charts.fmtPct(s.prev_share), s.delta_pts === null ? '—' : Charts.fmtSigned(s.delta_pts, 1)]),
    'Share of voice this period');
}

// c. Sentiment
function anRenderSentiment(d) {
  const series = d.series.map(s => ({label: s.key, color: anEntityColor(s.key), values: s.values, n: s.n, raw: s}));
  if (!series.some(s => s.values.some(v => v !== null))) {
    anEmpty('sentiment', 'Not enough scored mentions yet: a point needs at least ' + d.min_n + ' mentions of a brand in one ' + d.params.bucket + '.');
  } else {
    Charts.lines(document.getElementById('an-plot-sentiment'), {labels: d.buckets, bucket: d.params.bucket, series, domain: [-1, 1], height: 240});
    Charts.legend(document.getElementById('an-legend-sentiment'), series.map(s => ({label: s.label, color: s.color, shape: 'line',
      detail: s.raw.mean === null ? 'too few' : Charts.fmtSigned(s.raw.mean, 2)})));
  }
  Charts.table(document.getElementById('an-table-sentiment'),
    [{label: d.params.bucket === 'week' ? 'Week' : 'Day'}].concat(series.map(s => ({label: s.label + ' (n)', num: true}))),
    d.buckets.map((k, i) => [Charts.bucketLabel(k, d.params.bucket, true)].concat(series.map(s =>
      (s.values[i] === null ? '—' : Charts.fmtSigned(s.values[i], 2)) + ' (' + s.n[i] + ')'))), 'Average sentiment');
  document.getElementById('an-note-sentiment').textContent = 'Mean triage sentiment, −1 to +1. Points with fewer than ' + d.min_n + ' mentions are left out.';
}

// d. Complaint heatmap
function anRenderComplaints(d) {
  if (!d.rows.length || !d.themes.length) {
    anEmpty('complaints', 'No complaint theme reached 2 mentions for any brand in this period.');
  } else {
    Charts.heatmap(document.getElementById('an-plot-complaints'), {rows: d.rows, cols: d.themes.map(t => t.label), cells: d.cells,
      ramp: AN_RAMP, darkFrom: AN_RAMP_DARK_FROM});
    const legend = document.getElementById('an-legend-complaints');
    legend.innerHTML = '<span class="ramp-legend"><span>fewer</span><span class="ramp" id="an-ramp"></span><span>more complaints</span></span>';
    const ramp = document.getElementById('an-ramp');
    AN_RAMP.filter((_, i) => i % 2 === 0).forEach(c => { const s = Charts.htmlEl('span', null, null, ramp); s.style.backgroundColor = c; });
  }
  Charts.table(document.getElementById('an-table-complaints'), [{label: 'Brand'}].concat(d.themes.map(t => ({label: t.label, num: true}))),
    d.rows.map((r, i) => [r].concat(d.cells[i].map(c => c.count ? Charts.fmtInt(c.count) + (c.terms.length ? ' · ' + c.terms.map(t => t.term).join(', ') : '') : c.suppressed ? '<2' : '0'))),
    'Complaint mentions by brand and theme');
  document.getElementById('an-note-complaints').textContent = d.method;
}

// e. Emerging terms
function anRenderEmerging(d) {
  if (!d.terms.length) {
    anEmpty('emerging', 'No term rose above the minimum count in this period.');
  } else {
    Charts.hbars(document.getElementById('an-plot-emerging'), {color: AN_PALETTE[0],
      rows: d.terms.map(t => ({label: t.term, value: t.count, badge: t.is_new ? 'NEW' : '',
        detail: [['mentions', Charts.fmtInt(t.count)], ['in the ' + d.baseline_days + '-day baseline', Charts.fmtInt(t.baseline_count)],
          ['velocity', t.is_new ? 'new' : t.velocity.toFixed(1) + '×'], ['Enter or click', 'open in Feed']]})),
      onSelect: i => anOpenFeed({q: d.terms[i].term})});
    document.getElementById('an-legend-emerging').innerHTML = '';
  }
  Charts.table(document.getElementById('an-table-emerging'),
    [{label: 'Term'}, {label: 'Mentions', num: true}, {label: 'Baseline', num: true}, {label: 'Velocity', num: true}, {label: 'New'}],
    d.terms.map(t => [t.term, Charts.fmtInt(t.count), Charts.fmtInt(t.baseline_count), t.velocity.toFixed(1) + '×', t.is_new ? 'NEW' : '']),
    'Emerging terms');
  document.getElementById('an-note-emerging').textContent = 'Velocity = rate in this period vs the ' + d.baseline_days +
    '-day baseline, ranked with volume. Terms need at least 2 mentions. Select a bar to see the mentions in the Feed.';
}

// f. Drug momentum (small multiples)
function anRenderDrugs(d) {
  const plot = document.getElementById('an-plot-drugs');
  plot.setAttribute('role', 'group');
  plot.removeAttribute('tabindex');
  if (!d.drugs.some(x => !x.suppressed)) {
    anEmpty('drugs', 'No drug or topic reached 2 mentions in this period.');
  } else {
    plot.innerHTML = '<div class="spark-grid">' + d.drugs.map((x, i) => '<button type="button" class="spark" data-action="an-drug" data-drug="' + escHtml(x.drug) + '"' +
      (x.suppressed ? ' disabled' : '') + ' title="' + escHtml(x.suppressed ? 'Fewer than 2 mentions' : 'Show ' + x.drug + ' mentions in the Feed') + '">' +
      '<span class="spark-head"><span class="spark-name">' + escHtml(x.drug) + '</span>' + anDelta(x.change_pct, '%', 0, null) + '</span>' +
      '<span class="spark-total">' + (x.suppressed ? '<span class="muted">fewer than 2</span>' : escHtml(Charts.fmtInt(x.total)) + ' <span class="muted">mentions</span>') + '</span>' +
      '<span class="spark-line" id="an-spark-' + i + '"></span></button>').join('') + '</div>';
    d.drugs.forEach((x, i) => { if (!x.suppressed) Charts.sparkline(document.getElementById('an-spark-' + i), x.values, AN_PALETTE[0]); });
  }
  document.getElementById('an-legend-drugs').innerHTML = '';
  Charts.table(document.getElementById('an-table-drugs'),
    [{label: 'Drug / topic'}, {label: 'Mentions', num: true}, {label: 'Previous', num: true}, {label: 'Change', num: true}],
    d.drugs.map(x => [x.drug, x.suppressed ? '<2' : Charts.fmtInt(x.total), x.prev_total === null ? '<2' : Charts.fmtInt(x.prev_total),
      x.change_pct === null ? '—' : Charts.fmtSigned(x.change_pct, 0) + '%']), 'Drug and topic momentum');
  document.getElementById('an-note-drugs').textContent = 'Each line is that drug’s own scale. Change compares with the previous period of the same length.';
}

// g. Escalations & SLA
function anRenderEscalations(d) {
  document.getElementById('an-extra-escalations').innerHTML = '<div class="an-mini">' +
    anMini('Median time to acknowledge', d.median_ack_minutes === null ? '—' : d.median_ack_minutes + ' min') +
    anMini('Breached the SLA', d.breached_pct === null ? '—' : d.breached_pct + '%', d.breached ? 'bad' : '') +
    anMini('Open now', String(d.open), d.open_breached ? 'bad' : '', d.open_breached ? d.open_breached + ' breached' : '') + '</div>';
  const series = d.series.map(s => ({label: label('kind', s.key), color: anMapColor(AN_KIND_COLORS, s.key), values: s.values, total: s.total}));
  if (!d.total) {
    anEmpty('escalations', 'No escalations were opened in this period.');
  } else {
    Charts.columns(document.getElementById('an-plot-escalations'), {labels: d.buckets, bucket: d.params.bucket, series, height: 200});
    Charts.legend(document.getElementById('an-legend-escalations'), series.map(s => ({label: s.label, color: s.color, detail: Charts.fmtInt(s.total)})));
  }
  Charts.table(document.getElementById('an-table-escalations'),
    [{label: d.params.bucket === 'week' ? 'Week' : 'Day'}].concat(series.map(s => ({label: s.label, num: true}))),
    anBucketRows(d.buckets, series, v => Charts.fmtInt(v), d.params.bucket), 'Escalations by kind');
}

function anMini(title, value, tone, sub) {
  return '<div class="an-mini-tile"><div class="label">' + escHtml(title) + '</div><div class="value' + (tone ? ' ' + tone : '') + '">' +
    (tone === 'bad' ? '<span aria-hidden="true">&#9888;</span> ' : '') + escHtml(value) + '</div>' +
    (sub ? '<div class="sub">' + escHtml(sub) + '</div>' : '') + '</div>';
}

// h. Stat tiles
function anDelta(value, unit, digits, upGood) {
  if (value === null || value === undefined) return '<span class="delta flat">no comparison</span>';
  const v = Number(value);
  const dir = v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
  const tone = upGood === null ? 'flat' : dir === 'flat' ? 'flat' : (dir === 'up') === upGood ? 'up' : 'down';
  const arrow = v > 0 ? '&#9650;' : v < 0 ? '&#9660;' : '';
  return '<span class="delta ' + tone + '"><span aria-hidden="true">' + arrow + '</span> ' +
    escHtml(Charts.fmtSigned(v, digits).replace('±', '') + unit) + '</span>';
}

function anRenderTiles(d) {
  const t = d.tiles;
  const period = 'vs previous ' + d.params.days + ' days';
  const tile = (title, value, delta, note) => '<div class="stat-card an-tile"><div class="label">' + escHtml(title) + '</div>' +
    '<div class="value">' + value + '</div><div class="an-tile-delta">' + delta + ' <span class="muted">' + escHtml(note || period) + '</span></div></div>';
  document.getElementById('an-tiles').innerHTML =
    tile('Relevant mentions', escHtml(Charts.fmtInt(t.mentions.value)), anDelta(t.mentions.change_pct, '%', 0, null)) +
    tile('WellPeps share of voice', escHtml(t.share.value === null ? '—' : Charts.fmtPct(t.share.value)), anDelta(t.share.delta_pts, ' pts', 1, true)) +
    tile('WellPeps avg sentiment', escHtml(t.sentiment.value === null ? '—' : Charts.fmtSigned(t.sentiment.value, 2)),
      anDelta(t.sentiment.delta, '', 2, true), t.sentiment.value === null ? 'needs 3+ scored mentions' : '') +
    tile('Open escalations', escHtml(String(t.open_escalations.value)),
      t.open_escalations.breached ? '<span class="delta down"><span aria-hidden="true">&#9888;</span> ' + escHtml(String(t.open_escalations.breached)) + ' breached</span>' : '<span class="delta flat">none breached</span>', 'right now') +
    tile('Emerging terms', escHtml(String(t.emerging.value)), '<span class="delta flat">' + escHtml(String(t.emerging.new)) + ' new</span>', 'this period');
}

const AN_RENDER = {volume: anRenderVolume, share: anRenderShare, sentiment: anRenderSentiment, complaints: anRenderComplaints,
  emerging: anRenderEmerging, drugs: anRenderDrugs, escalations: anRenderEscalations};

// ── Loading ──

async function anLoadOptions() {
  if (anOptions) return anOptions;
  anOptions = await api('/api/analytics/options');
  return anOptions;
}

function anRenderCard(id) {
  const d = anData[id];
  if (!d) return;
  anSetTake(id, d.takeaway);
  AN_RENDER[id](d);
}

async function anFetch(id, query) {
  const r = await request('/api/analytics/' + id + '?' + query);
  if (r.ok) return r.data;
  return {error: (r.data && r.data.detail) || (r.status ? 'Could not load (' + r.status + ').' : 'The dashboard can’t reach the server.')};
}

async function anLoadAll() {
  anBuildCards();
  const seq = ++anSeq;
  const query = anQuery(false);
  document.getElementById('analytics').classList.add('is-loading');
  const ids = ['summary'].concat(AN_CARDS.map(c => c.id));
  const results = await Promise.all(ids.map(id => anFetch(id, id === 'volume' ? query + '&by=' + anVolumeBy : query)));
  if (seq !== anSeq) return;  // a newer filter change won
  document.getElementById('analytics').classList.remove('is-loading');
  const errorEl = document.getElementById('an-error');
  const failed = results.find(r => r.error);
  errorEl.textContent = failed ? failed.error : '';
  errorEl.classList.toggle('hidden', !failed);
  ids.forEach((id, i) => {
    const d = results[i];
    if (d.error) {
      if (id !== 'summary') { anSetTake(id, ''); anEmpty(id, d.error); }
      return;
    }
    anData[id] = d;
    if (id === 'summary') anRenderTiles(d);
    else anRenderCard(id);
  });
  anWidth = document.getElementById('an-grid').clientWidth;
}

async function loadAnalytics() {
  anReadHash();
  await anLoadOptions();
  if (!anOptions) { document.getElementById('an-grid').innerHTML = offlineState(); return; }
  document.getElementById('an-tz').textContent = (anData.summary && anData.summary.params.timezone) || '';
  anSyncForm();
  anWriteHash();
  await anLoadAll();
  document.getElementById('an-tz').textContent = anData.summary ? anData.summary.params.timezone : '';
}

function anOpenFeed(filters) {
  showFeedWith(filters);
}

function anToggleTable(el) {
  const id = el.dataset.card;
  const on = el.getAttribute('aria-pressed') !== 'true';
  el.setAttribute('aria-pressed', on ? 'true' : 'false');
  el.textContent = on ? 'View as chart' : 'View as table';
  ['an-plot-', 'an-legend-'].forEach(p => document.getElementById(p + id).classList.toggle('hidden', on));
  document.getElementById('an-table-' + id).classList.toggle('hidden', !on);
  Charts.hideTip();
}

// ── Events ──

Object.assign(ACTIONS, {
  'an-table': el => anToggleTable(el),
  'an-by': el => { anVolumeBy = el.dataset.by; anWriteHash(); anReloadVolume(); },
  'an-drug': el => anOpenFeed({drug: el.dataset.drug}),
  'an-reset': () => { anState = Object.assign({}, AN_DEFAULTS, {platform: [], competitor: []}); anVolumeBy = 'category'; anSyncForm(); anWriteHash(); anLoadAll(); },
});

async function anReloadVolume() {
  const d = await anFetch('volume', anQuery(false) + '&by=' + anVolumeBy);
  if (!d.error) { anData.volume = d; anRenderCard('volume'); }
}

document.getElementById('an-filters').addEventListener('change', ev => {
  if (ev.target.closest('details') && ev.target.type === 'checkbox') {
    const list = ev.target.closest('.check-list');
    if (list && list.id === 'an-competitor-list' && list.querySelectorAll('input:checked').length > 8) {
      ev.target.checked = false;
      showToast('Compare at most 8 competitors at once.', 'error');
      return;
    }
  }
  anFiltersChanged();
});
document.getElementById('an-filters').addEventListener('submit', ev => { ev.preventDefault(); anFiltersChanged(); });

let anResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(anResizeTimer);
  anResizeTimer = setTimeout(() => {
    if (currentTab !== 'analytics') return;
    const w = document.getElementById('an-grid').clientWidth;
    if (w === anWidth) return;
    anWidth = w;
    AN_CARDS.forEach(c => anRenderCard(c.id));
  }, 150);
});
