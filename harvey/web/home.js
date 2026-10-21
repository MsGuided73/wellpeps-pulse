'use strict';

// WellPeps Pulse — the Pulse view (the home screen): a time-of-day greeting,
// "Today's one thing" (computed from real data), the approval queue, the
// safety watch, a one-line weekly summary and live spend. Loaded after
// app.js and rewrite.js; uses their helpers (api, request, send, showToast,
// escHtml, extLink, safeHref, can, label, timeHtml, platformHtml, emptyState,
// offlineState, ackEscalation, queueRank, TAB_LOAD).
//
// Pulse never posts. Approve records the human decision and copies the reply;
// a WellPeps rep posts it on the platform and marks it posted here. Routes:
//   GET  /api/mentions?status=in_review|approved   the queue
//   GET  /api/mentions/{id}                        draft, checks, guide, community
//   POST /api/mentions/{id}/edit | approve | reject | copied | mark-posted
//   GET  /api/urgent, POST /api/escalations/{id}/ack   the safety watch
//   GET  /api/trends, /api/analytics/replies, /api/spend, /api/harvey/status
// Same CSP rules as app.js: no inline style or handlers; server values go
// through escHtml; actions use data-qact so the app-wide handler ignores them.

const Home = (() => {
  const DETAIL_BATCH = 10;          // queue cards whose full detail loads at once
  const MORNING_ENDS = 12;
  const AFTERNOON_ENDS = 17;
  const SNIPPET_CHARS = 320;
  const REJECT_REASONS = ['Not relevant to WellPeps', 'The tone is off', 'A compliance concern',
    'Already answered elsewhere', 'Other'];

  const cards = new Map();          // mention id -> card state
  let order = [];                   // card ids in display order
  let shown = 0;                    // how many cards have their detail loaded
  let signals = {urgent: null, heartbeat: null};
  let rendered = false;

  const $ = id => document.getElementById(id);

  // ── Greeting ──

  function greet() {
    const now = new Date(), h = now.getHours();
    const part = h < MORNING_ENDS ? 'morning' : h < AFTERNOON_ENDS ? 'afternoon' : 'evening';
    $('greeting').textContent = 'Good ' + part + ', WellPeps team';
    $('today').textContent = now.toLocaleDateString(undefined, {weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'});
  }

  // ── Text helpers ──

  function linkify(text) {
    return escHtml(text).replace(/https?:\/\/[^\s<]*[^\s<.,;:!?)]/g,
      u => '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + '</a>');
  }

  function snippet(text) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    return t.length > SNIPPET_CHARS ? t.slice(0, SNIPPET_CHARS).trimEnd() + '…' : t;
  }

  function communityOf(card) {
    const c = card.d && card.d.engagement && card.d.engagement.community;
    const url = card.item.url || '';
    const fromUrl = (url.match(/\/r\/([A-Za-z0-9_]+)/) || [])[1];
    // A registered community's name already reads "r/Name"; fall back to the permalink.
    return (c && c.name) || (fromUrl ? 'r/' + fromUrl : '');
  }

  const platformName = card => label('platform', card.item.platform) || 'the platform';

  function reviewer() { return can('review'); }

  function clinicalOnly(card) {
    return !reviewer() && can('approve_clinical') && !!(card.d && card.d.approval && card.d.approval.clinical_required);
  }

  const isDirty = card => card.d && card.d.latest_draft && card.text !== card.saved;

  // ── Plain-language checks ──

  function guideChip(card) {
    const e = card.d && card.d.engagement;
    const g = e && e.guide;
    if (g && (g.mode === 'link' || g.mode === 'name') && g.title) {
      return '<span class="guide"><svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2 3.5C2 2.7 2.7 2 3.5 2H7v11H3.5c-.8 0-1.5.3-1.5.8V3.5ZM14 3.5c0-.8-.7-1.5-1.5-1.5H9v11h3.5c.8 0 1.5.3 1.5.8V3.5Z" fill="currentColor"/></svg>' +
        escHtml(g.title + (g.chapter ? ' › ' + g.chapter : '')) + '</span>';
    }
    if (g && g.mode === 'omit') {
      return '<span class="guide none">No guide: ' + escHtml(g.why || 'no chapter answers this question') + '</span>';
    }
    return '';
  }

  // Why Pulse picked this post: the situation, the Selection Module's decision
  // and reason, what still holds posting, and planning warnings. Calm on
  // purpose: approval blockers are already listed in the amber "needs" box.
  const HELD_PERMISSIONS = {
    unknown: "This community's rules aren't recorded yet.",
    permission_missing: "This community needs a moderator's permission first.",
    prohibited: "This community doesn't allow brand replies.",
  };

  function pickHtml(card) {
    const e = card.d && card.d.engagement;
    const sel = e && e.selection;
    if (!sel || !sel.engagement_decision) return '';
    const situation = e.situation && e.situation.label;
    const reason = label('pickReason', sel.reason_code);
    let text = (situation ? escHtml(situation) + '. ' : '') + escHtml(label('pick', sel.engagement_decision)) +
      (reason ? ': ' + escHtml(reason) : '') + '.' + (sel.priority === 'high' ? ' High priority.' : '');
    if (sel.engagement_decision === 'ROUTE') {
      if (e.handoff && e.handoff.acked) text += ' The care team has acknowledged it.';
      if (e.situation && e.situation.reply === 'boundary_only' && card.d.latest_draft) {
        text += ' Only the approved safety reply may be posted' +
          (e.clinical_approval_required ? ', and a clinician or admin must approve it.' : '.');
      }
    }
    const held = HELD_PERMISSIONS[sel.public_permission];
    if (held && sel.posting_hold) {
      const already = ['RULES_UNKNOWN', 'PERMISSION_MISSING', 'PARTICIPATION_PROHIBITED'].includes(sel.reason_code);
      text += ' ' + (already ? '' : escHtml(held) + ' ') + (sel.public_permission === 'prohibited' ? '' :
        'Record the rules or permission in <a href="#communities">Operator HQ › Communities</a> before posting.');
    }
    const warns = (e.warnings || []).map(w => '<li>' + escHtml(String(w).replace(/^[a-z0-9 ]+:\s*/i, '')) + '</li>').join('');
    return '<div class="pick" title="' + escHtml(sel.engagement_decision + ' ' + sel.reason_code) + '">' +
      '<p><b>Why this post:</b> ' + text + '</p>' +
      (warns ? '<p class="pick-h">Heads-up</p><ul>' + warns + '</ul>' : '') + '</div>';
  }

  // The Knowledge-library sections the draft relied on (context harness, R-27): names
  // only, from latest_draft.context; nothing when the harness was off.
  function reliedHtml(card) {
    const draft = card.d && card.d.latest_draft;
    const used = draft && Array.isArray(draft.context) ? draft.context : [];
    const names = [];
    used.forEach(c => {
      const name = String((c && c.doc_name) || '') + (c && c.heading ? ' > ' + c.heading : '');
      if (name && names.indexOf(name) < 0) names.push(name);
    });
    if (!names.length) return '';
    return '<p class="relied">Relied on: ' + escHtml(names.join('; ')) + '</p>';
  }

  function check(kind, text) {
    return '<span class="chk' + (kind ? ' ' + kind : '') + '">' + escHtml(text) + '</span>';
  }

  function checksHtml(card) {
    const d = card.d, dr = d && d.latest_draft;
    const out = [];
    if (dr) {
      if (dr.tier === 'green') out.push(check('', 'Passed the compliance filter'));
      else if (dr.tier === 'yellow') out.push(check('info', 'Compliance filter: worth a closer read'));
      else if (dr.tier === 'red') out.push(check('warn', 'Blocked by the compliance filter'));
      if (dr.review_verdict === 'pass') out.push(check('', 'A second reviewer agreed'));
      else if (dr.review_verdict === 'needs_human') out.push(check('info', 'Needs a human read'));
      else if (dr.review_verdict === 'reject') out.push(check('warn', 'The reviewer flagged this reply'));
      // Voice is judged apart from compliance (Community Agent Voice): a note, never a block.
      (dr.review_reasons || []).filter(r => /^VOICE:/.test(String(r))).slice(0, 2)
        .forEach(r => out.push(check('info', 'Voice: ' + String(r).replace(/^VOICE:\s*/, ''))));
      const ids = dr.claim_ids || [];
      const info = d.claims || {};
      if (ids.length) {
        const bad = ids.filter(c => !(info[c] && info[c].publishable));
        out.push(bad.length ? check('warn', 'A claim is waiting for sign-off') : check('', 'Uses only approved claims'));
      }
      const tl = d.tracked_link;
      if (tl) out.push(tl.live ? check('', 'Link is approved and live') : check('warn', "Link isn't live yet"));
      const g = d.engagement && d.engagement.guide;
      if (g && g.drafted && !g.satisfied && (g.mode === 'link' || g.mode === 'name')) out.push(check('warn', 'Guide reference missing'));
    }
    if (card.rewritten) out.push(check('info', 'Rewritten with AI'));
    return out.length ? '<div class="checks">' + out.join('') + '</div>' : '';
  }

  function needsHtml(card) {
    const d = card.d;
    const blockers = (d && d.approval && d.approval.blockers) || [];
    const hits = (d && d.latest_draft && d.latest_draft.tier !== 'green' && d.latest_draft.filter_hits) || [];
    if (!blockers.length && !hits.length) return '';
    const link = '<a href="#review?id=' + escHtml(String(card.id)) + '">Fix it in the Review desk</a>';
    let html = '<div class="needs" id="needs-' + escHtml(String(card.id)) + '">';
    if (blockers.length) {
      html += '<b>Before this can be approved</b><ul>' + blockers.map(b => '<li>' + escHtml(b) + '</li>').join('') + '</ul>' + link;
    }
    if (hits.length) {
      html += '<b>Compliance notes from when this was drafted</b><ul>' + hits.map(h => '<li>' + escHtml(h) + '</li>').join('') + '</ul>';
    }
    return html + '</div>';
  }

  // ── Card rendering ──

  // Posting as (Derek Goldberg, Community Agent Voice): the rep posts as themselves
  // ("I work with WellPeps on patient education.") or as the official account
  // ("We're the WellPeps team."). Pulse never writes a name; the switch only swaps
  // the disclosure, and saving re-runs the compliance filter.
  const BRAND_DISCLOSURE = "We're the WellPeps team";
  const PERSON_DISCLOSURE = 'I work with WellPeps on patient education';
  const PERSON_RX = /I work with WellPeps(?: on patient education)?|I'?m part of the WellPeps team|I am part of the WellPeps team/;
  const BRAND_RX = /We(?:'| a)re the WellPeps team/;

  const postingAs = card => (BRAND_RX.test(card.text) ? 'brand' : 'me');

  function setPostingAs(card, who) {
    if (postingAs(card) === who) return false;
    const next = who === 'brand' ? card.text.replace(PERSON_RX, BRAND_DISCLOSURE)
      : card.text.replace(BRAND_RX, PERSON_DISCLOSURE);
    if (next === card.text) return false;
    card.text = next;
    return true;
  }

  function postingAsHtml(card) {
    if (card.phase !== 'review' || card.mode !== 'view' || !reviewer() || !card.text.trim()) return '';
    // Approved safety replies are posted word for word: no switch there.
    const situation = card.d && card.d.engagement && card.d.engagement.situation;
    if (situation && situation.reply === 'boundary_only') return '';
    if (!PERSON_RX.test(card.text) && !BRAND_RX.test(card.text)) return '';
    const id = escHtml(String(card.id));
    const who = postingAs(card);
    const opt = (value, text) => '<button type="button" class="btn btn-ghost" data-qact="as-' + value + '" data-id="' + id + '"' +
      ' aria-pressed="' + String(who === value) + '">' + text + '</button>';
    return '<div class="posting-as" role="group" aria-label="Posting as"><span class="label">Posting as</span>' +
      opt('me', 'Me') + opt('brand', 'WellPeps account') + '</div>' +
      (who === 'brand' ? '<p class="note">Check the rest reads as the WellPeps account ("we"); AI rewrite can help.</p>' : '');
  }

  function draftBox(card) {
    const id = escHtml(String(card.id));
    if (card.mode === 'edit') {
      return '<div class="draft"><label class="label" for="ta-' + id + '">Edit reply</label>' +
        '<textarea id="ta-' + id + '" data-qinput="text">' + escHtml(card.text) + '</textarea></div>';
    }
    const note = isDirty(card) ? '<p class="note">You changed this reply. It is not saved yet; approving saves it first.</p>' : '';
    if (!card.text.trim() && card.phase === 'review') {
      return '<div class="draft"><p class="label">Drafted reply</p><p class="note">No reply has been drafted for this post yet. Choose Edit to write one.</p></div>';
    }
    return '<div class="draft"><p class="label">' + (card.phase === 'review' ? 'Drafted reply' : 'Reply') + '</p>' +
      '<p class="txt">' + linkify(card.text) + '</p>' + note + postingAsHtml(card) + '</div>';
  }

  function actionsHtml(card) {
    const id = escHtml(String(card.id));
    const m = card.item;
    if (card.phase === 'posted') {
      return '<div class="act"><span class="status">Posted by you. Pulse logged it.</span>' +
        (extLink(m.url, 'View on ' + platformName(card) + ' ↗', 'btn btn-ghost') || '') + '</div>';
    }
    if (card.phase === 'discarded') return '<div class="act"><span class="status rej">Not posted. Pulse learns from this.</span></div>';
    if (card.phase === 'approved') {
      return '<div class="act"><span class="status">Approved. Paste the reply on ' + escHtml(platformName(card)) +
        ' as yourself, then mark it posted.</span></div>' +
        '<div class="act">' + (extLink(m.url, 'Open the post ↗', 'btn btn-secondary') || '') +
        '<button type="button" class="btn btn-secondary" data-qact="copy" data-id="' + id + '">Copy reply</button></div>' +
        (reviewer() ? '<div class="field"><label for="pu-' + id + '">Link to your reply (optional)</label>' +
          '<input type="url" id="pu-' + id + '" maxlength="2000" autocomplete="off" placeholder="Paste the link once it is live"></div>' +
          '<div class="act"><button type="button" class="btn btn-primary" data-qact="posted" data-id="' + id + '">Mark as posted</button></div>' : '');
    }
    if (card.mode === 'reject') {
      return '<div class="reject-form"><div class="field"><label for="rr-' + id + '">Why not post this one?</label><select id="rr-' + id + '">' +
        REJECT_REASONS.map(r => '<option>' + escHtml(r) + '</option>').join('') + '</select></div>' +
        '<div class="field"><label for="rd-' + id + '">Anything to add? (optional)</label><input type="text" id="rd-' + id + '" maxlength="300"></div>' +
        '<div class="act"><button type="button" class="btn btn-primary" data-qact="reject-go" data-id="' + id + '">Don\'t post this reply</button>' +
        '<button type="button" class="btn btn-ghost" data-qact="reject-cancel" data-id="' + id + '">Cancel</button></div></div>';
    }
    if (!card.d) return '<div class="act"><span class="note">Loading the draft…</span></div>';
    if (!reviewer() && !clinicalOnly(card)) {
      return '<div class="act"><span class="note">Your role can read the queue but not approve replies.</span></div>';
    }
    const blockers = (card.d.approval && card.d.approval.blockers) || [];
    const stuck = blockers.length > 0 && !isDirty(card);
    const approveLabel = card.mode === 'edit' || isDirty(card) ? 'Save, approve and copy reply' : 'Approve and copy reply';
    const busy = card.busy ? ' disabled' : '';
    let html = '<div class="act"><button type="button" class="btn btn-primary" data-qact="approve" data-id="' + id + '"' +
      (stuck || card.busy ? ' disabled' : '') + (stuck ? ' aria-describedby="needs-' + id + '"' : '') + '>' +
      (card.busy ? 'Working…' : approveLabel) + '</button>';
    if (card.mode === 'edit') {
      html += '<button type="button" class="btn btn-secondary" data-qact="save" data-id="' + id + '"' + busy + '>Save draft</button>' +
        '<button type="button" class="btn btn-ghost" data-qact="edit-cancel" data-id="' + id + '">Cancel</button>';
    } else if (reviewer()) {
      html += '<button type="button" class="btn btn-secondary" data-qact="edit" data-id="' + id + '"' + busy + '>Edit</button>';
      if (isDirty(card)) {
        html += '<button type="button" class="btn btn-secondary" data-qact="save" data-id="' + id + '"' + busy + '>Save draft</button>' +
          '<button type="button" class="btn btn-ghost" data-qact="undo" data-id="' + id + '">Undo changes</button>';
      }
    }
    if (reviewer() && card.mode !== 'edit') {
      html += '<button type="button" class="btn btn-ghost" data-qact="reject" data-id="' + id + '"' + busy + '>Don\'t post</button>';
    }
    return html + '</div>';
  }

  function cardHtml(card) {
    const m = card.item;
    const id = escHtml(String(card.id));
    const comm = communityOf(card);
    const where = comm ? escHtml(comm) : '';
    const platform = escHtml(platformName(card));
    const when = timeHtml(m.posted_at || m.collected_at);
    const url = extLink(m.url, 'View on ' + platformName(card) + ' ↗', 'open');
    const body = card.d ? card.d.mention.text : m.text;
    const note = '<p class="human-note"><b>A WellPeps rep posts this one.</b> Pulse only drafts. Read the post on ' +
      platform + ', then post the reply yourself.</p>';
    const done = card.phase === 'posted' || card.phase === 'discarded';
    return '<article class="item' + (done ? ' is-done' : '') + '" id="card-' + id + '" data-id="' + id + '" aria-labelledby="ct-' + id + '">' +
      '<div class="src"><b>' + platform + '</b>' + (where ? '<span>' + where + '</span>' : '') + '<span>' + when + '</span>' + url + '</div>' +
      '<h3 id="ct-' + id + '">' + (extLink(m.url, m.title || snippet(m.text).slice(0, 90), 'post-title') || escHtml(m.title || 'Untitled post')) + '</h3>' +
      '<p class="quote">“' + escHtml(snippet(body)) + '”</p>' + (card.phase === 'review' ? pickHtml(card) + reliedHtml(card) : '') +
      (done ? '' : note) +
      (card.d ? draftBox(card) : '<div class="draft"><p class="note">Loading the draft…</p></div>') +
      (card.phase === 'review' ? guideChip(card) : '') + checksHtml(card) + (card.phase === 'review' ? needsHtml(card) : '') +
      actionsHtml(card) + '</article>';
  }

  function renderCard(card, focusSel) {
    const old = $('card-' + card.id);
    if (!old) return;
    const tpl = document.createElement('template');
    tpl.innerHTML = cardHtml(card);
    old.replaceWith(tpl.content.firstElementChild);
    if (focusSel) {
      const target = document.querySelector('#card-' + card.id + ' ' + focusSel);
      if (target) target.focus();
    }
    refreshHeader();
  }

  function renderQueue() {
    const q = $('queue');
    const visible = order.slice(0, shown).map(i => cards.get(i));
    if (!order.length) {
      q.innerHTML = emptyState('&#10003;', "You're all caught up",
        'Nothing is waiting for approval. Pulse keeps listening and will line up new drafts here.');
      refreshHeader();
      return;
    }
    q.innerHTML = visible.map(cardHtml).join('') + (shown < order.length
      ? '<div class="act"><button type="button" class="btn btn-secondary" data-qact="more">Show ' +
        Math.min(DETAIL_BATCH, order.length - shown) + ' more</button><span class="note">' + (order.length - shown) + ' not shown yet</span></div>' : '');
    refreshHeader();
  }

  // ── Header pieces: count, one thing ──

  const phaseCount = phase => [...cards.values()].filter(c => c.phase === phase).length;

  function refreshHeader() {
    const waiting = phaseCount('review'), approved = phaseCount('approved');
    const parts = [];
    if (waiting) parts.push(waiting + (waiting === 1 ? ' reply' : ' replies') + ' to review');
    if (approved) parts.push(approved + ' to post');
    $('appr-count').textContent = parts.length ? parts.join(' · ') : 'All caught up';
    navCount('nav-review', waiting, false);
    renderOneThing();
  }

  function guidePointers() {
    return [...cards.values()].filter(c => c.phase === 'review' && c.d && c.d.engagement && c.d.engagement.guide &&
      ['link', 'name'].includes(c.d.engagement.guide.mode)).length;
  }

  function oneThing() {
    const items = (signals.urgent && signals.urgent.items) || [];
    const care = items.filter(i => i.kind !== 'safety_watch');
    const late = care.filter(i => i.breached);
    const waiting = phaseCount('review'), approved = phaseCount('approved');
    const s = n => (n === 1 ? '' : 's');
    if (late.length) {
      return {text: late.length + ' safety item' + s(late.length) + (late.length === 1 ? ' is' : ' are') +
        ' past the care team response target. Please check ' + (late.length === 1 ? 'it' : 'they') + ' got picked up.',
        actions: [['Open Urgent', '#urgent', true]]};
    }
    if (waiting) {
      const g = guidePointers();
      return {text: waiting + (waiting === 1 ? ' reply is' : ' replies are') + ' ready for you' +
        (g ? ', ' + (g === 1 ? 'one' : g) + ' pointing people to a Smart Patient\'s Guide' : '') + '.',
        actions: [['Review replies', '#approvals', true]]};
    }
    if (approved) {
      return {text: approved + (approved === 1 ? ' approved reply is' : ' approved replies are') +
        ' waiting for a rep to post ' + (approved === 1 ? 'it' : 'them') + '.', actions: [['See approved replies', '#approvals', true]]};
    }
    if (care.length) {
      return {text: care.length + ' safety item' + s(care.length) + (care.length === 1 ? ' is' : ' are') +
        ' with the care team. Nothing else needs you.', actions: [['Open Urgent', '#urgent', false]]};
    }
    if (signals.heartbeat === false) {
      return {text: "Pulse isn't running, so no new posts are coming in. Start it from Operator HQ.",
        actions: [['Open Operator HQ', '#hq', true]]};
    }
    return {text: "You're all caught up. Pulse is listening and nothing needs you right now.", actions: []};
  }

  function renderOneThing() {
    if (!rendered) return;
    const t = oneThing();
    $('onething-text').textContent = t.text;
    $('onething-actions').innerHTML = t.actions.map(a => '<a class="btn ' + (a[2] ? 'btn-primary' : 'btn-secondary') +
      '" href="' + a[1] + '">' + escHtml(a[0]) + '</a>').join('');
  }

  // ── Safety watch ──

  function watchItem(e) {
    const care = e.kind !== 'safety_watch';
    const ackAllowed = e.kind === 'adverse_event' ? can('ack_adverse') : can('ack');
    const tone = e.breached ? ' act-late' : care ? ' act-needed' : '';
    const pill = care
      ? (e.breached ? '<span class="pill bad">Past the response target</span>' : '<span class="pill warn">With the care team</span>')
      : '<span class="pill">On the safety watch list</span>';
    return '<article class="watch-item' + tone + '" id="escalation-' + escHtml(String(e.id)) + '">' +
      '<div class="src">' + platformHtml(e.platform) + pill +
      (care ? '<span class="countdown' + (e.breached ? ' late' : '') + '" data-due="' + escHtml(e.sla_due_at || '') + '">' +
        escHtml(countdownText(e.seconds_left)) + '</span>' : '') +
      '<span>' + timeHtml(e.posted_at || e.collected_at) + '</span></div>' +
      '<h3 class="post-title">' + escHtml(label('kind', e.kind)) + (e.title ? ': ' + escHtml(truncate(e.title, 120)) : '') + '</h3>' +
      (e.excerpt ? '<p class="quote">“' + escHtml(e.excerpt) + '”</p>' : '') +
      (e.why && e.why.text ? '<p class="note">' + escHtml(e.why.text) + '</p>' : '') +
      '<div class="act">' + (extLink(e.permalink, 'View on ' + label('platform', e.platform) + ' ↗', 'btn btn-secondary') || '') +
      '<button type="button" class="btn btn-ghost" data-action="view-details" data-id="' + escHtml(String(e.mention_id)) + '">Details</button>' +
      '<button type="button" class="btn btn-primary" data-qact="ack" data-id="' + escHtml(String(e.id)) + '"' +
      (ackAllowed ? '' : ' disabled title="Your role cannot acknowledge this"') + '>' + (care ? 'Acknowledge' : 'Mark reviewed') + '</button></div></article>';
  }

  function renderWatch() {
    const data = signals.urgent;
    const el = $('watch');
    if (!data) { el.innerHTML = '<p class="na">Could not load the safety watch just now. Try Refresh.</p>'; $('watch-count').textContent = ''; return; }
    $('watch-count').textContent = data.items.length ? data.items.length + (data.items.length === 1 ? ' item' : ' items') : 'Nothing open';
    el.innerHTML = data.items.length ? data.items.map(watchItem).join('')
      : '<p class="note">Nothing open. When a post mentions a side effect or a legal or privacy concern, it shows up here.</p>';
    tickCountdowns();
    renderOneThing();
  }

  async function ackWatch(id) {
    const r = await send('/api/escalations/' + encodeURIComponent(id) + '/ack');
    if (!r.ok) return;
    showToast('Done. Marked reviewed.', 'success');
    signals.urgent = await pollUrgent();
    renderWatch();
    const next = $('watch').querySelector('button, a');
    if (next) next.focus(); else $('watch-panel').scrollIntoView({block: 'nearest'});
  }

  // ── Weekly line and spend ──

  async function loadWeek() {
    const [trends, replies] = await Promise.all([api('/api/trends?days=7'), api('/api/analytics/replies?days=7')]);
    const parts = [];
    if (trends) {
      const diff = trends.mentions - (trends.previous_mentions || 0);
      parts.push('<b>' + escHtml(String(trends.mentions)) + '</b> relevant mentions this week' +
        (trends.previous_mentions ? ' (' + (diff >= 0 ? '+' : '−') + escHtml(String(Math.abs(diff))) + ' on last week)' : ''));
    }
    if (replies) {
      parts.push('<b>' + escHtml(String(replies.approved)) + '</b> replies approved, <b>' + escHtml(String(replies.posted)) + '</b> posted');
    }
    $('weekline').innerHTML = parts.join(' <span class="sep" aria-hidden="true">|</span> ');
    $('weekline').classList.toggle('hidden', !parts.length);
  }

  const usd = n => '$' + Number(n || 0).toFixed(2);

  async function loadSpendInto(id) {
    const el = $(id);
    const r = await request('/api/spend');
    if (!r.ok || !r.data || r.data.ok === false) {
      el.innerHTML = '<p class="na">' + (r.status === 404 ? '<b>Not available yet.</b> Live spend turns on with the next update.'
        : "Couldn't load spend just now.") + '</p>';
      return null;
    }
    const d = r.data;
    el.innerHTML = '<p class="spend-line"><b>' + escHtml(usd(d.total)) + '</b><span>so far in ' + escHtml(d.month_label || 'this month') + '</span>' +
      '<span>Claude ' + escHtml(usd(d.claude && d.claude.total)) + (d.claude && d.claude.billing ? ' (' + escHtml(d.claude.billing) + ')' : '') +
      '</span><span>Apify ' + escHtml(usd(d.apify && d.apify.total)) + '</span></p>';
    return d;
  }

  // ── Loading ──

  function newCard(item, phase) {
    return {id: item.id, item, d: null, phase, mode: 'view', text: '', saved: '', rewritten: false, busy: false};
  }

  function adoptDetail(card, d) {
    card.d = d;
    card.item = Object.assign({}, card.item, d.mention);
    const draft = d.latest_draft;
    if (!(card.mode === 'edit' && card.text !== card.saved)) {
      card.text = draft ? draft.text : '';
      card.saved = card.text;
    } else {
      card.saved = draft ? draft.text : '';
    }
  }

  async function fetchDetail(card) {
    const d = await api('/api/mentions/' + encodeURIComponent(card.id));
    if (d) adoptDetail(card, d);
    return !!d;
  }

  async function loadMore() {
    const batch = order.slice(shown, shown + DETAIL_BATCH).map(i => cards.get(i));
    shown += batch.length;
    renderQueue();
    await Promise.all(batch.map(async c => { if (await fetchDetail(c)) renderCard(c); }));
    refreshHeader();
  }

  async function loadQueue() {
    const q = $('queue');
    if (!rendered) q.innerHTML = '<p class="note">Loading the queue…</p>';
    const [waiting, approved] = await Promise.all([api('/api/mentions?status=in_review&limit=200'),
      api('/api/mentions?status=approved&limit=200')]);
    if (!waiting || !approved) { q.innerHTML = offlineState(); return; }
    const keep = new Map([...cards].filter(([, c]) => c.mode === 'edit' && c.text !== c.saved));
    cards.clear();
    const next = waiting.items.slice().reverse().sort((a, b) => queueRank(a) - queueRank(b))
      .map(m => newCard(m, 'review')).concat(approved.items.slice().reverse().map(m => newCard(m, 'approved')));
    next.forEach(c => cards.set(c.id, keep.get(c.id) || c));
    order = next.map(c => c.id);
    shown = 0;
    rendered = true;
    navCount('nav-review', waiting.total, false);
    await loadMore();
  }

  async function loadHome() {
    greet();
    const [urgent, hb] = await Promise.all([pollUrgent(), api('/api/harvey/status')]);
    signals = {urgent, heartbeat: hb ? !!hb.running : null};
    renderWatch();
    loadWeek();
    loadSpendInto('pulse-spend');
    await loadQueue();
  }

  // ── Actions ──

  const cardOf = el => cards.get(Number(el.dataset.id));

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
  }

  async function saveDraft(card) {
    const ids = (card.d.latest_draft && card.d.latest_draft.claim_ids) || [];
    const r = await send('/api/mentions/' + card.id + '/edit', {text: card.text, claim_ids: ids});
    if (!r.ok) return null;
    await fetchDetail(card);
    card.saved = card.d.latest_draft ? card.d.latest_draft.text : card.text;
    card.text = card.saved;
    return r.data;
  }

  async function approve(card) {
    card.busy = true;
    renderCard(card);
    let ok = true;
    if (isDirty(card)) ok = !!(await saveDraft(card));
    if (ok) {
      const draft = card.d.latest_draft;
      const r = await send('/api/mentions/' + card.id + '/approve', {draft_id: draft ? draft.id : null});
      ok = r.ok;
    }
    card.busy = false;
    if (!ok) {
      card.mode = 'view';
      await fetchDetail(card);
      renderCard(card, '[data-qact="approve"]');
      return;
    }
    const copied = await copyText(card.text);
    if (copied) await send('/api/mentions/' + card.id + '/copied');
    card.phase = 'approved';
    card.mode = 'view';
    renderCard(card, 'a.btn, button.btn');
    showToast(copied ? 'Approved and copied. Open the post and paste your reply.'
      : 'Approved. Copy the reply from the card, then open the post.', 'success');
  }

  async function markPosted(card) {
    const input = $('pu-' + card.id);
    const r = await send('/api/mentions/' + card.id + '/mark-posted', {posted_url: input ? input.value.trim() : ''});
    if (!r.ok) return;
    card.phase = 'posted';
    renderCard(card);
    showToast('Marked as posted.', 'success');
  }

  async function reject(card) {
    const base = $('rr-' + card.id).value;
    const extra = $('rd-' + card.id).value.trim();
    const r = await send('/api/mentions/' + card.id + '/reject', {reason: extra ? base + ': ' + extra : base});
    if (!r.ok) return;
    card.phase = 'discarded';
    card.mode = 'view';
    renderCard(card);
    showToast('Draft discarded.', 'success');
  }

  async function copyAgain(card) {
    const copied = await copyText(card.text);
    if (copied) { await send('/api/mentions/' + card.id + '/copied'); showToast('Reply copied.', 'success'); }
    else showToast('Could not copy automatically. Select the reply and copy it.', 'error');
  }

  const QACTIONS = {
    approve: c => approve(c),
    edit: c => { c.mode = 'edit'; renderCard(c, 'textarea'); },
    'edit-cancel': c => { c.mode = 'view'; c.text = c.saved; renderCard(c, '[data-qact="edit"]'); },
    undo: c => { c.text = c.saved; renderCard(c, '[data-qact="approve"]'); },
    save: async c => {
      const out = await saveDraft(c);
      if (!out) return;
      c.mode = 'view';
      renderCard(c, '[data-qact="approve"]');
      showToast('Saved as version ' + out.version + '. Compliance filter: ' + (out.tier || 'checked') + '.', 'success');
    },
    reject: c => { c.mode = 'reject'; renderCard(c, 'select'); },
    'reject-cancel': c => { c.mode = 'view'; renderCard(c, '[data-qact="reject"]'); },
    'reject-go': c => reject(c),
    'as-me': c => { if (setPostingAs(c, 'me')) renderCard(c, '[data-qact="as-me"]'); },
    'as-brand': c => { if (setPostingAs(c, 'brand')) renderCard(c, '[data-qact="as-brand"]'); },
    posted: c => markPosted(c),
    copy: c => copyAgain(c),
  };

  function onQueueClick(event) {
    const btn = event.target.closest('[data-qact]');
    if (!btn || btn.disabled) return;
    if (btn.dataset.qact === 'more') { loadMore(); return; }
    const card = cardOf(btn);
    if (card && QACTIONS[btn.dataset.qact]) QACTIONS[btn.dataset.qact](card);
  }

  // ── Rewrite host ──

  const rewriteHost = {
    find(node) {
      const item = (node.nodeType === 1 ? node : node.parentElement).closest('#queue .item');
      const card = item && cards.get(Number(item.dataset.id));
      return card && card.phase === 'review' && card.d ? card : null;
    },
    apply(card, from, to) {
      if (!card.text.includes(from)) return false;
      card.text = card.text.replace(from, () => to);
      const txt = document.querySelector('#card-' + card.id + ' .draft .txt');
      if (txt) txt.innerHTML = linkify(card.text);
      renderCard(card);
      return true;
    },
    markRewritten(card) {
      card.rewritten = true;
      const item = $('card-' + card.id);
      if (!item || item.textContent.includes('Rewritten with AI')) return;
      const checks = item.querySelector('.checks');
      if (checks) checks.insertAdjacentHTML('beforeend', check('info', 'Rewritten with AI'));
      else item.querySelector('.draft').insertAdjacentHTML('afterend', '<div class="checks">' + check('info', 'Rewritten with AI') + '</div>');
    },
  };

  function init() {
    TAB_LOAD.pulse = loadHome;
    greet();
    setInterval(greet, 60000);
    const q = $('queue');
    q.addEventListener('click', onQueueClick);
    q.addEventListener('input', event => {
      const ta = event.target.closest('textarea[data-qinput]');
      const card = ta && cards.get(Number(ta.closest('.item').dataset.id));
      if (card) card.text = ta.value;
    });
    $('watch').addEventListener('click', event => {
      const b = event.target.closest('[data-qact="ack"]');
      if (b && !b.disabled) ackWatch(b.dataset.id);
    });
    RewriteUI.attach(q, rewriteHost);
    setInterval(async () => {
      if (document.hidden || currentTab !== 'pulse') return;
      signals.urgent = await pollUrgent();
      renderWatch();
    }, 60000);
  }

  init();
  return {loadSpendInto, usd};
})();
