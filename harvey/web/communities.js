'use strict';

// WellPeps Pulse — Communities: every community Pulse collects from, a link to
// the group, a clearance checklist (rules read, brand replies, links, moderator
// contact) and, in plain words, what stays blocked until it is cleared.
// Admins update a community's rules here ("Update rules"). Uses app.js helpers
// (request, escHtml, extLink, can, showToast, emptyState, num, $, TAB_LOAD).
//
// Routes: GET /api/communities, POST /api/communities/{id}/permission (admin).

const Communities = (() => {
  const $ = id => document.getElementById(id);
  const num = n => escHtml(Number(n || 0).toLocaleString());
  const STATUS = {
    cleared: ['ok', 'Cleared'],
    partial: ['info', 'Partly cleared'],
    not_checked: ['', 'Rules not checked'],
    not_allowed: ['', 'Brand replies not allowed'],
  };
  const ITEM_ICON = {done: '&#10003;', todo: '&#9675;', blocked: '&#215;', optional: '&#9675;'};
  const ITEM_WORD = {done: 'Done', todo: 'To do', blocked: 'Not allowed', optional: 'Optional'};
  const FILTERS = {
    todo: ['Needs checking', c => c.clearance.status !== 'cleared' && c.clearance.status !== 'not_allowed'],
    cleared: ['Cleared', c => c.clearance.status === 'cleared'],
    all: ['All', () => true],
  };

  let rows = [];
  let filter = 'todo';
  let query = '';
  let editing = null;
  let trigger = null;

  function postsLine(c) {
    const total = Number(c.mentions_total || 0);
    const week = Number(c.mentions_7d || 0);
    if (!total) return 'No posts collected yet';
    return num(total) + (total === 1 ? ' post' : ' posts') + ' collected' + (week ? ' · ' + num(week) + ' this week' : '');
  }

  function checklist(items) {
    return '<ul class="cm-check">' + items.map(i =>
      '<li class="cm-' + escHtml(i.state) + '"><span class="cm-icon" aria-hidden="true">' + (ITEM_ICON[i.state] || '') +
      '</span><span>' + escHtml(i.label) + '</span><span class="visually-hidden"> (' + escHtml(ITEM_WORD[i.state] || i.state) +
      ')</span></li>').join('') + '</ul>';
  }

  function blocks(list) {
    if (!list.length) return '<p class="note">Nothing is blocked here.</p>';
    return '<ul class="cm-blocks">' + list.map(b => '<li>' + escHtml(b) + '</li>').join('') + '</ul>';
  }

  function card(c) {
    const [tone, word] = STATUS[c.clearance.status] || STATUS.not_checked;
    const title = c.url ? extLink(c.url, c.name + ' ↗', 'cm-name') : '<b class="cm-name">' + escHtml(c.name) + '</b>';
    const meta = [c.platform === 'reddit' ? 'Reddit' : c.platform === 'facebook' ? 'Facebook' : c.platform,
      c.topic ? 'topic: ' + c.topic : '', postsLine(c)].filter(Boolean).map(escHtml).join(' · ');
    const recorded = c.source === 'recorded' && c.recorded_at
      ? 'Rules recorded ' + escHtml(String(c.recorded_at).slice(0, 10)) + (c.recorded_by ? ' by ' + escHtml(c.recorded_by) : '')
      : c.source === 'seen' ? 'Found in collected posts; not in the community list yet' : 'From the starting community list';
    const note = c.source === 'recorded' && c.note ? '<p class="cm-note">“' + escHtml(c.note) + '”</p>' : '';
    const action = can('admin')
      ? '<button type="button" class="btn btn-secondary" data-cm="edit" data-id="' + escHtml(c.id) + '">Update rules</button>' : '';
    return '<article class="panel cm-card" aria-label="' + escHtml(c.name) + '">' +
      '<div class="cm-head"><div>' + title + '<p class="meta">' + meta + '</p></div>' +
      '<span class="pill ' + tone + '">' + escHtml(word) + '</span></div>' +
      '<div class="cm-grid"><div><h3>Clearance checklist</h3>' + checklist(c.clearance.items) + '</div>' +
      '<div><h3>Blocked until cleared</h3>' + blocks(c.clearance.blocks) + '</div></div>' +
      note + '<div class="cm-foot"><span class="meta">' + recorded + '</span>' + action + '</div></article>';
  }

  function matches(c) {
    const q = query.trim().toLowerCase();
    return !q || c.name.toLowerCase().includes(q) || (c.topic || '').toLowerCase().includes(q);
  }

  function render() {
    const counts = Object.fromEntries(Object.entries(FILTERS).map(([k, f]) => [k, rows.filter(f[1]).length]));
    $('cm-filter').innerHTML = Object.entries(FILTERS).map(([k, f]) =>
      '<button type="button" data-cm="filter" data-filter="' + k + '" aria-pressed="' + (k === filter) + '"' +
      (k === filter ? ' class="active"' : '') + '>' + escHtml(f[0]) + ' (' + num(counts[k]) + ')</button>').join('');
    const cleared = counts.cleared;
    const withPosts = rows.filter(c => Number(c.mentions_total || 0) > 0 && c.clearance.status !== 'cleared').length;
    $('cm-stats').innerHTML = '<div><b>' + num(rows.length) + '</b><span>communities</span></div>' +
      '<div><b>' + num(cleared) + '</b><span>cleared</span></div>' +
      '<div><b>' + num(withPosts) + '</b><span>with posts, not cleared</span></div>';
    const list = rows.filter(FILTERS[filter][1]).filter(matches);
    $('cm-list').innerHTML = list.length ? list.map(card).join('')
      : emptyState('&#9678;', filter === 'cleared' ? 'No communities cleared yet' : 'Nothing to show',
        filter === 'cleared' ? 'Record a community\'s rules and it appears here.' : 'Try another filter or clear the search.');
  }

  async function load() {
    const r = await request('/api/communities');
    if (!r.ok) {
      $('cm-list').innerHTML = r.status === 0 ? offlineState()
        : emptyState('&#9888;', 'Couldn\'t load communities', 'Try again in a moment.');
      return;
    }
    rows = ((r.data && r.data.communities) || []).map(c => ({...c, name: String(c.name || c.id || ''),
      clearance: c.clearance || {status: 'not_checked', items: [], blocks: []}}));
    render();
  }

  // ── Update rules (admins) ──

  function openEdit(id, button) {
    const c = rows.find(x => x.id === id);
    if (!c) return;
    editing = c;
    trigger = button;
    $('perm-lede').textContent = 'Read the posted rules of ' + c.name + ' (or ask a moderator) and record what they say. ' +
      'Pulse uses this to decide whether replies there can mention a guide or include a link.';
    $('perm-participation').value = ['allowed', 'not_allowed', 'unknown'].includes(c.brand_participation) ? c.brand_participation : 'unknown';
    $('perm-links').value = ['true', 'false', 'unknown'].includes(c.links_allowed) ? c.links_allowed : 'unknown';
    $('perm-contact').value = c.moderator_contact || '';
    $('perm-note').value = c.source === 'recorded' ? (c.note || '') : '';
    $('perm-error').classList.add('hidden');
    $('perm-dialog').showModal();
    $('perm-participation').focus();
  }

  function closeEdit() {
    $('perm-dialog').close();
    if (trigger && document.body.contains(trigger)) trigger.focus();
  }

  async function save(event) {
    event.preventDefault();
    const err = $('perm-error');
    err.classList.add('hidden');
    $('perm-submit').disabled = true;
    const r = await request('/api/communities/' + encodeURI(editing.id) + '/permission', {method: 'POST', body: {
      brand_participation: $('perm-participation').value,
      links_allowed: $('perm-links').value,
      moderator_contact: $('perm-contact').value.trim(),
      note: $('perm-note').value.trim(),
    }});
    $('perm-submit').disabled = false;
    if (!r.ok || (r.data && r.data.ok === false)) {
      const why = r.status === 0 ? "Pulse isn't responding. Check that it's still running, then try again. Your answers are still here."
        : (r.data && (r.data.error || r.data.detail)) || "That didn't save. Try again.";
      err.textContent = typeof why === 'string' ? why : "That didn't save. Check the fields and try again.";
      err.classList.remove('hidden');
      return;
    }
    const name = editing.name;
    closeEdit();
    showToast('Rules recorded for ' + name + '.', 'success');
    await load();
    const again = document.querySelector('#cm-list [data-id="' + CSS.escape(editing.id) + '"]');
    if (again) again.focus();
  }

  function init() {
    TAB_LOAD.communities = load;
    $('communities').addEventListener('click', event => {
      const b = event.target.closest('[data-cm]');
      if (!b || b.disabled) return;
      if (b.dataset.cm === 'filter') { filter = b.dataset.filter; render(); }
      else if (b.dataset.cm === 'edit') openEdit(b.dataset.id, b);
    });
    $('cm-search').addEventListener('input', event => { query = event.target.value; render(); });
    $('perm-form').addEventListener('submit', save);
    $('perm-dialog').addEventListener('click', event => {
      if (event.target.closest('[data-action="perm-cancel"]')) closeEdit();
    });
  }

  init();
  return {load};
})();
