'use strict';

// WellPeps Pulse — the "Replies & links" card on the Analytics tab. Loaded
// after app.js and analytics.js; uses app.js helpers (request, escHtml,
// label). Same CSP rules: no inline style or handlers, server values through
// escHtml. Data: GET /api/analytics/replies (our own replies only).

function rpDays(days) {
  const n = parseInt(days, 10);
  return n >= 1 && n <= 366 ? n : 30;
}

function rpTable(caption, head, rows) {
  if (!rows.length) return '';
  return '<table><caption class="visually-hidden">' + escHtml(caption) + '</caption><thead><tr>' +
    head.map((h, i) => '<th' + (i ? ' class="num"' : '') + '>' + escHtml(h) + '</th>').join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + r.map((c, i) => '<td' + (i ? ' class="num"' : '') + '>' + c + '</td>').join('') + '</tr>').join('') +
    '</tbody></table>';
}

function rpRender(d) {
  document.getElementById('rp-tiles').innerHTML = '<div class="an-mini">' +
    anMini('Approved, not yet posted', String(d.approved)) +
    anMini('Posted', String(d.posted)) +
    anMini('Tracked links in CSV', String(d.rows.length)) + '</div>';
  const platforms = d.by_platform.map(p => [escHtml(label('platform', p.platform)), escHtml(String(p.approved)),
    escHtml(String(p.posted))]);
  const byLink = d.by_link.map(l => [escHtml(l.label) + (l.link_id === 'none' ? '' : ' ' +
    (l.live ? toneBadge('Live', 'good') : toneBadge('Not live yet', 'bad'))),
    escHtml(String(l.approved)), escHtml(String(l.posted))]);
  const tables = rpTable('Replies by platform', ['Platform', 'Approved', 'Posted'], platforms) +
    rpTable('Replies by tracked link', ['Tracked link', 'Approved', 'Posted'], byLink);
  document.getElementById('rp-tables').innerHTML = tables ||
    '<div class="an-empty">No replies were approved in this period.</div>';
}

// The 80/20 planning card: GET /api/analytics/engagement-mix (our own replies).
function mixRows(rows, key, labelFn) {
  return rows.map(r => [escHtml(labelFn(r[key])) + (r.over ? ' ' + toneBadge('over 20%', 'waiting') : ''),
    escHtml(String(r.education)), escHtml(String(r.promotion)),
    escHtml(String(Math.round(r.promotional_share * 100)) + '%')]);
}

function mixRender(d) {
  document.getElementById('mix-tiles').innerHTML = '<div class="an-mini">' +
    anMini('Education', String(d.education)) + anMini('Promotion', String(d.promotion)) +
    anMini('Promotional share', String(Math.round(d.promotional_share * 100)) + '%') + '</div>';
  const head = ['', 'Education', 'Promotion', 'Share'];
  const tables = rpTable('By platform', ['Platform'].concat(head.slice(1)),
      mixRows(d.by_platform, 'platform', p => label('platform', p))) +
    rpTable('By community', ['Community'].concat(head.slice(1)),
      mixRows(d.by_community, 'community', c => c === 'none' ? 'No community (owned / review sites)' : c));
  document.getElementById('mix-tables').innerHTML = tables ||
    '<div class="an-empty">No replies were approved in this period.</div>';
}

async function mixLoad(days) {
  const r = await request('/api/analytics/engagement-mix?days=' + rpDays(days));
  if (!r.ok) {
    document.getElementById('mix-tables').innerHTML = '<div class="an-empty">' +
      escHtml((r.data && r.data.detail) || 'Could not load the education / promotion mix.') + '</div>';
    return;
  }
  mixRender(r.data);
}

async function rpLoad(days) {
  if (document.getElementById('mix-card')) mixLoad(days);
  const n = rpDays(days);
  document.getElementById('rp-csv').setAttribute('href', '/api/analytics/replies?format=csv&days=' + n);
  const r = await request('/api/analytics/replies?days=' + n);
  if (!r.ok) {
    document.getElementById('rp-tables').innerHTML = '<div class="an-empty">' +
      escHtml((r.data && r.data.detail) || 'Could not load replies.') + '</div>';
    return;
  }
  rpRender(r.data);
}
