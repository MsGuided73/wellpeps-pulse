'use strict';

// WellPeps Pulse — Operator HQ: the agents (status from the heartbeat,
// start/stop for admins), spend, "What people are struggling with", a
// communities summary (the full page is communities.js), the guardrails
// and links to the older tools. Loaded after home.js; uses app.js helpers
// (api, request, send, showToast, escHtml, extLink, can, label, applyWidths,
// emptyState, toneBadge, TAB_LOAD) and Home.loadSpendInto.
//
// Routes: GET /api/harvey/status, POST /api/harvey/start|stop (admin),
// GET /api/summary, /api/urgent, /api/briefs, /api/insights/pain-points,
// GET /api/communities (summary only),
// GET /api/spend. A 404 on a route that is not deployed yet shows a calm
// "Not available yet" note instead of an error.

const Hq = (() => {
  const INSIGHT_DAYS = 7;
  const AGENT_KEYS = {
    community: ['triager', 'drafter', 'reviewer'],
    safety: ['safety'],
    briefs: ['brief', 'briefs'],
  };

  let communities = [];

  const $ = id => document.getElementById(id);
  const na = text => '<p class="na"><b>Not available yet.</b> ' + escHtml(text) + '</p>';
  const num = n => escHtml(String(n === null || n === undefined ? 0 : n));

  // ── Agents ──

  function sumAgents(byAgent, keys) {
    return keys.reduce((t, k) => t + Number((byAgent || {})[k] || 0), 0);
  }

  function agentCard(a) {
    return '<article class="agent" aria-labelledby="' + a.id + '-h"><div class="agent-top"><h3 id="' + a.id + '-h">' + escHtml(a.name) + '</h3>' +
      a.pill + (a.actions ? '<div class="row">' + a.actions + '</div>' : '') + '</div>' +
      '<p class="note">' + escHtml(a.about) + '</p>' +
      '<dl class="facts">' + a.facts.map(f => '<div><dt>' + escHtml(f[0]) + '</dt><dd>' + f[1] + '</dd></div>').join('') + '</dl></article>';
  }

  function renderAgents(ctx) {
    const hb = ctx.heartbeat;
    const running = hb && hb.running;
    const spend = ctx.spend && ctx.spend.claude ? ctx.spend.claude.by_agent : null;
    const money = keys => (spend ? Home.usd(sumAgents(spend, keys)) : 'Not available yet');
    const hbPill = !hb ? '<span class="pill">Status unknown</span>'
      : running ? '<span class="pill ok">Running</span>' : '<span class="pill">Stopped</span>';
    const admin = can('admin');
    const hbActions = hb ? (running
      ? '<button type="button" class="btn btn-secondary" data-hq="stop"' + (admin ? '' : ' disabled title="Only admins can stop Pulse"') + '>Stop</button>'
      : '<button type="button" class="btn btn-primary" data-hq="start"' + (admin ? '' : ' disabled title="Only admins can start Pulse"') + '>Start</button>') +
      '<a class="btn btn-ghost" href="#controls">Logs</a>' : '';
    const startedAt = hb && hb.started_at ? timeHtml(hb.started_at) : '';
    const care = ((ctx.urgent && ctx.urgent.items) || []).filter(i => i.kind !== 'safety_watch').length;
    const brief = ctx.brief;
    const agents = [
      {id: 'ag-heart', name: 'Pulse heartbeat', pill: hbPill, actions: hbActions,
        about: 'Wakes every few minutes, collects new posts and runs the agents below. Nothing is posted by it.',
        facts: [['Status', running ? 'Running' + (hb.pid ? ' (process ' + num(hb.pid) + ')' : '') : hb ? 'Stopped' : 'Unknown'],
          ['Started', startedAt || 'Not running']]},
      {id: 'ag-community', name: 'Community agent',
        pill: '<span class="pill info">' + num(ctx.waiting) + (ctx.waiting === 1 ? ' draft' : ' drafts') + ' waiting</span>',
        actions: '<a class="btn btn-secondary" href="#pulse">Open Pulse</a>',
        about: 'Finds conversations about WellPeps and its programs, drafts replies and holds them for a person to approve.',
        facts: [['Spend this month', money(AGENT_KEYS.community)], ['Knows', '<a href="#knowledge">The Knowledge library</a>']]},
      {id: 'ag-safety', name: 'Safety screen',
        pill: care ? '<span class="pill warn">' + num(care) + ' with the care team</span>' : '<span class="pill ok">Nothing open</span>',
        actions: '<a class="btn btn-secondary" href="#urgent">Open Urgent</a>',
        about: 'Re-checks every health-related post for side effects, dosing questions or possible harm and sends them to the care team. It never replies.',
        facts: [['Spend this month', money(AGENT_KEYS.safety)]]},
      {id: 'ag-briefs', name: 'Brief writer',
        pill: brief ? '<span class="pill ok">Latest ' + escHtml(String(brief.window_start || '').slice(0, 10)) + '</span>' : '<span class="pill">No brief yet</span>',
        actions: '<a class="btn btn-secondary" href="#briefs">Open briefs</a>',
        about: 'Writes the daily and weekly brief and the list of what people are asking for.',
        facts: [['Spend this month', money(AGENT_KEYS.briefs)], ['Latest', brief ? escHtml(brief.headline || 'Brief ready') : 'None yet']]},
    ];
    $('hq-agents').innerHTML = agents.map(agentCard).join('');
  }

  // ── Spend ──

  async function renderSpend() {
    const d = await Home.loadSpendInto('hq-spend');
    if (d && d.claude && d.claude.by_agent) {
      const rows = Object.entries(d.claude.by_agent).filter(e => e[1]).sort((a, b) => b[1] - a[1]);
      if (rows.length) {
        $('hq-spend').insertAdjacentHTML('beforeend', '<div class="tbl narrow"><table><caption class="visually-hidden">Claude spend by agent</caption>' +
          '<thead><tr><th>Agent</th><th class="num">Spend</th></tr></thead><tbody>' +
          rows.map(r => '<tr><td>' + escHtml(titleCase(r[0])) + '</td><td class="num">' + escHtml(Home.usd(r[1])) + '</td></tr>').join('') +
          '</tbody></table></div>');
      }
    }
    return d;
  }

  // ── What people are struggling with ──

  function quoteLink(e) {
    if (!e || !e.quote) return '';
    const link = extLink(e.url, '“' + e.quote + '”', 'pp-quote');
    return link || '<span class="pp-quote">“' + escHtml(e.quote) + '”</span>';
  }

  // Recurring questions Pulse skipped or held for lack of an approved answer:
  // ideas for guides, web pages and Knowledge documents. Calm on purpose.
  function gapsBlock(gaps) {
    if (!gaps.length) return '';
    const rows = gaps.slice(0, 5).map(g =>
      '<li><div class="pp-row"><span>' + escHtml(g.label) + '</span><span class="pp-n">' + num(g.count) +
      (g.count === 1 ? ' post' : ' posts') + ' · ' + escHtml(g.kind_label) + '</span></div>' +
      quoteLink((g.examples || [])[0]) + '</li>').join('');
    return '<div class="pp-col pp-gaps"><h3>Questions we couldn’t answer</h3>' +
      '<p class="note">Asked more than once, with no approved answer yet. Ideas for a guide, a web page or a Knowledge document.</p>' +
      '<ul class="pp-comp">' + rows + '</ul></div>';
  }

  function renderInsights(r) {
    const body = $('pp-body');
    const has = r && ((r.pain_points || []).length || (r.competitor_complaints || []).length || (r.segments || []).length ||
      (r.knowledge_gaps || []).length);
    $('pp-window').textContent = r ? 'Last ' + INSIGHT_DAYS + ' days · ' + (r.mentions || 0) + ' relevant posts' : '';
    if (!r) { body.innerHTML = na('Could not load this just now. Try Refresh.'); return; }
    if (!has) {
      body.innerHTML = emptyState('&#9678;', 'Nothing to report yet', 'A pain point shows up here once at least ' + (r.min_count || 2) +
        ' relevant posts mention it in the same week.');
      return;
    }
    const top = Math.max(1, ...(r.pain_points || []).map(p => p.count));
    const pains = (r.pain_points || []).slice(0, 5).map(p =>
      '<li><div class="pp-row"><span>' + escHtml(p.label) + '</span><span class="pp-n">' + num(p.count) + (p.count === 1 ? ' post' : ' posts') + '</span></div>' +
      '<div class="bar"><span data-width="' + Math.round(p.count / top * 100) + '"></span></div>' +
      quoteLink((p.examples || [])[0]) + '</li>').join('');
    const comps = (r.competitor_complaints || []).slice(0, 5).map(c =>
      '<li><b>' + escHtml(c.competitor) + '</b> <span class="pp-n">' + num(c.count) + (c.count === 1 ? ' complaint' : ' complaints') +
      (c.main_pain && c.main_pain.label ? ' · ' + escHtml(c.main_pain.label) : '') + '</span>' + quoteLink(c.example) + '</li>').join('');
    const segs = (r.segments || []).slice(0, 5).map(s =>
      '<li><div class="pp-row"><b>' + escHtml(s.label) + '</b><span class="pp-n">' + num(s.count) +
      (s.emerging ? ' <span class="pill info">New</span>' : '') + '</span></div>' +
      (s.description ? '<p>' + escHtml(s.description) + '</p>' : '') + '</li>').join('');
    body.innerHTML = '<div class="pp-grid">' +
      '<div class="pp-col"><h3>Top pain points</h3>' + (pains ? '<ol class="pp-list">' + pains + '</ol>' : '<p class="note">None this week.</p>') + '</div>' +
      '<div class="pp-col"><h3>Complaints about other providers</h3>' + (comps ? '<ul class="pp-comp">' + comps + '</ul>' : '<p class="note">None this week.</p>') + '</div>' +
      '<div class="pp-col"><h3>Emerging customer types</h3>' + (segs ? '<ul class="pp-icp">' + segs + '</ul>' : '<p class="note">None this week.</p>') + '</div></div>' +
      gapsBlock(r.knowledge_gaps || []) +
      '<p class="note">For the full tables and trends, open <a href="#briefs">Briefs and live trends</a>.</p>';
    applyWidths(body);
  }

  // ── Communities (summary; the full page is #communities, communities.js) ──

  async function loadCommunities() {
    const r = await request('/api/communities');
    if (!r.ok) { $('cm-body').innerHTML = '<p class="note">Couldn&#39;t load communities just now. Try Refresh.</p>'; $('cm-count').textContent = ''; return null; }
    communities = ((r.data && r.data.communities) || []).map(c => ({
      id: String(c.id || ''), participation: c.brand_participation || 'unknown',
      status: (c.clearance && c.clearance.status) || 'not_checked', posts: Number(c.mentions_total || 0),
    }));
    const cleared = communities.filter(c => c.status === 'cleared').length;
    const waiting = communities.filter(c => c.posts > 0 && c.status !== 'cleared' && c.status !== 'not_allowed').length;
    $('cm-count').textContent = num(cleared) + ' of ' + num(communities.length) + ' cleared';
    $('cm-body').innerHTML = '<p>' + (waiting
      ? num(waiting) + (waiting === 1 ? ' community Pulse collects from is' : ' communities Pulse collects from are') +
        ' not cleared yet, so replies there can\'t name a guide or include a link.'
      : 'Every community Pulse collects from is cleared or checked.') + '</p>';
    return communities;
  }

  // ── Summary tiles ──

  let ctxCache = {};

  function renderSummary() {
    const c = ctxCache;
    const cleared = communities.filter(x => x.status === 'cleared').length;
    const care = ((c.urgent && c.urgent.items) || []).filter(i => i.kind !== 'safety_watch').length;
    const tiles = [
      ['Waiting on approval', num(c.waiting), '<a href="#pulse">Open Pulse</a>'],
      ['Open safety items', num(((c.urgent && c.urgent.items) || []).length), care ? num(care) + ' with the care team' : 'None need the care team'],
      ['Communities cleared to draft', communities.length ? num(cleared) + ' <small>/ ' + num(communities.length) + '</small>' : '<small>Not available yet</small>', ''],
      [c.spend && c.spend.month_label ? 'Spend, ' + escHtml(c.spend.month_label) : 'Spend this month',
        c.spend ? escHtml(Home.usd(c.spend.total)) : '<small>Not available yet</small>', ''],
    ];
    $('hq-sum').innerHTML = tiles.map(t => '<div class="panel"><span class="label">' + t[0] + '</span><span class="v">' + t[1] +
      '</span>' + (t[2] ? '<span class="sub">' + t[2] + '</span>' : '') + '</div>').join('');
  }

  // ── Load ──

  async function load() {
    const [heartbeat, urgent, waiting, briefs, insights] = await Promise.all([
      api('/api/harvey/status'), api('/api/urgent'), api('/api/mentions?status=in_review&limit=1'),
      api('/api/briefs?limit=1'), api('/api/insights/pain-points?days=' + INSIGHT_DAYS)]);
    ctxCache = {heartbeat, urgent, waiting: waiting ? waiting.total : 0, brief: briefs && briefs.items ? briefs.items[0] : null};
    renderSummary();
    renderAgents(ctxCache);
    renderInsights(insights);
    ctxCache.spend = await renderSpend();
    renderAgents(ctxCache);
    await loadCommunities();
    renderSummary();
  }

  async function heartbeat(action) {
    const r = await send('/api/harvey/' + action);
    if (r.ok && r.data && r.data.success) showToast('Pulse ' + (action === 'start' ? 'started.' : 'stopped.'), 'success');
    else if (r.ok) showToast((r.data && r.data.message) || 'That did not work.', 'error');
    loadHarveyStatus();
    ctxCache.heartbeat = await api('/api/harvey/status');
    renderAgents(ctxCache);
    const btn = document.querySelector('#hq-agents [data-hq="start"], #hq-agents [data-hq="stop"]');
    if (btn) btn.focus();
  }

  function init() {
    TAB_LOAD.hq = load;
    $('hq').addEventListener('click', event => {
      const b = event.target.closest('[data-hq]');
      if (!b || b.disabled) return;
      const act = b.dataset.hq;
      if (act === 'start' || act === 'stop') heartbeat(act);
    });
  }

  init();
  return {load};
})();
