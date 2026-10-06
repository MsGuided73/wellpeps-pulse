'use strict';

// Demo Sandbox: fictional community pages for local demos (harvey/sandbox).
// Same rules as the Pulse dashboard: the CSP forbids inline script/style, so
// clicks go through one delegated listener (data-act) and every server value
// passes through escHtml (text) or a checked href before reaching innerHTML.
// Votes and hearts are local to this page; only comments are saved.

const SB = {session: null, thread: null, comments: [], targetId: null, replyTo: null};
const GUIDE_HOST = /(^|\.)wellpeps\.com$/i;
const GUIDE_PATH = /^\/smart-patient-guides(?:\/([a-z0-9-]+))?\/?$/i;
const URL_RE = /https?:\/\/[^\s<>"']+/g;
const TRAILING = /[.,;:!?)\]}'"]+$/;

function escHtml(s) {
  if (s === null || s === undefined || s === '') return '';
  return String(s).replace(/[&<>"']/g, ch => (
    {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]
  ));
}

function byId(id) { return document.getElementById(id); }

// ── Time ──

function ago(iso) {
  const t = Date.parse(String(iso || '').replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? '' : 'Z'));
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  if (s < 86400 * 30) return Math.floor(s / 86400) + 'd ago';
  return Math.floor(s / (86400 * 30)) + 'mo ago';
}

function timeHtml(iso) {
  return '<time title="' + escHtml(iso) + '">' + escHtml(ago(iso)) + '</time>';
}

// ── Markdown-lite: paragraphs, line breaks, safe links ──

function linkHtml(raw) {
  const url = raw.replace(TRAILING, '');
  const rest = raw.slice(url.length);
  let parsed;
  try { parsed = new URL(url); } catch { return escHtml(raw); }
  if (!/^https?:$/.test(parsed.protocol)) return escHtml(raw);
  const guide = GUIDE_HOST.test(parsed.hostname) && parsed.pathname.match(GUIDE_PATH);
  if (guide) {
    // Demo: a registry guide link opens the sandbox's demo guide page.
    const href = '/sandbox/guides' + (guide[1] ? '/' + encodeURIComponent(guide[1].toLowerCase()) : '');
    return '<a class="guide-link" href="' + escHtml(href) + '" title="Demo: opens the sandbox guide page, not the real site">' +
      escHtml(url) + '</a>' + escHtml(rest);
  }
  return '<a href="' + escHtml(parsed.href) + '" target="_blank" rel="noopener noreferrer nofollow">' + escHtml(url) + '</a>' + escHtml(rest);
}

function inlineHtml(text) {
  let out = '', last = 0;
  for (const m of text.matchAll(URL_RE)) {
    out += escHtml(text.slice(last, m.index)) + linkHtml(m[0]);
    last = m.index + m[0].length;
  }
  return out + escHtml(text.slice(last));
}

function mdHtml(text) {
  const paras = String(text || '').trim().split(/\n{2,}/).filter(p => p.trim());
  return paras.map(p => '<p>' + p.split('\n').map(inlineHtml).join('<br>') + '</p>').join('');
}

// ── API ──

async function getJson(path) {
  const r = await fetch(path, {credentials: 'same-origin'});
  if (!r.ok) throw Object.assign(new Error('HTTP ' + r.status), {status: r.status});
  return r.json();
}

async function postComment(threadId, parentId, body) {
  const r = await fetch('/sandbox/api/comments', {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Sandbox-CSRF': SB.session.csrf},
    body: JSON.stringify({thread_id: threadId, parent_id: parentId, body}),
  });
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  return {ok: r.ok, status: r.status, data};
}

// ── Shared bits ──

function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

function communityHref(c) {
  const name = encodeURIComponent(c.name || c.community);
  if (c.kind === 'photo') return '/sandbox/u/' + name;
  if (c.kind === 'review') return '/sandbox/l/' + name;
  return '/sandbox/f/c/' + name;
}

function communityLabel(kind, name) {
  if (kind === 'photo') return '@' + name;
  if (kind === 'review') return name;
  return 'c/' + name;
}

function threadHref(t) {
  if (t.kind === 'photo') return '/sandbox/p/' + t.id;
  if (t.kind === 'review') return '/sandbox/r/' + t.id;
  return '/sandbox/f/c/' + encodeURIComponent(t.community) + '/' + t.id;
}

function commentHref(t, cid) {
  return threadHref(t) + '/comment/' + cid + '#c' + cid;
}

function hue(seed, salt) { return Math.abs((Number(seed) || 1) * (salt * 2654435761 % 4096)) % 360; }

function paintImages(root) {
  (root || document).querySelectorAll('.ph-image[data-seed]').forEach(el => {
    const seed = el.dataset.seed;
    el.style.background = 'linear-gradient(' + (hue(seed, 7) % 180) + 'deg, hsl(' + hue(seed, 3) + ', 55%, 72%), hsl(' +
      hue(seed, 5) + ', 45%, 48%))';
  });
  (root || document).querySelectorAll('.avatar[data-seed]').forEach(el => {
    el.style.background = 'hsl(' + hue(el.dataset.seed.length * 31 + el.dataset.seed.charCodeAt(0), 11) + ', 35%, 60%)';
  });
}

function stars(n) {
  n = Math.max(0, Math.min(5, Number(n) || 0));
  return '<span class="stars" role="img" aria-label="' + n + ' out of 5 stars">' + '★'.repeat(n) + '☆'.repeat(5 - n) + '</span>';
}

function flairHtml(flair, brand) {
  return flair ? '<span class="flair' + (brand ? ' brand' : '') + '">' + escHtml(flair) + '</span>' : '';
}

function notFound(what) {
  byId('sb-main').innerHTML = '<div class="card pad"><h1 class="sb-h">Not found</h1><p class="sb-muted">' +
    escHtml(what) + ' does not exist in this demo sandbox.</p><p><a href="/sandbox">Back to the sandbox</a></p></div>';
}

// ── Index ──

async function renderIndex() {
  const data = await getJson('/sandbox/api/index');
  const groups = {forum: [], photo: [], review: []};
  data.communities.forEach(c => (groups[c.kind] || []).push(c));
  const section = (id, title, list, blurb) => '<h2 class="sb-h2" id="' + id + '">' + escHtml(title) + '</h2>' +
    '<p class="sb-muted">' + escHtml(blurb) + '</p><div class="community-grid">' + list.map(c =>
      '<a class="card" href="' + escHtml(communityHref(c)) + '"><div class="name">' + escHtml(communityLabel(c.kind, c.name)) +
      '</div><div class="meta">' + escHtml(c.title) + ' · ' + escHtml(plural(c.threads, 'post', 'posts')) + '</div></a>').join('') + '</div>';
  byId('sb-main').innerHTML = '<h1 class="sb-h">Demo Sandbox</h1><p class="sb-muted">Fictional communities for ' +
    'end-to-end reply demos. Every handle and post here is made up.</p>' +
    section('forum', 'Demo Forum', groups.forum, 'Threaded discussion communities.') +
    section('photos', 'Demo Photos', groups.photo, 'Photo and video posts with comments.') +
    section('reviews', 'Demo Reviews', groups.review, 'Review listings.') +
    '<h2 class="sb-h2">Recent posts</h2><div class="list">' + data.recent.map(threadRow).join('') + '</div>';
}

function threadRow(t) {
  const title = t.title || (t.body || '').slice(0, 90);
  return '<a class="card row" href="' + escHtml(threadHref(t)) + '"><div class="score-pill">' + escHtml(String(t.points)) +
    '</div><div><div class="title">' + escHtml(title) + ' ' + flairHtml(t.flair) + '</div><div class="meta">' +
    escHtml(communityLabel(t.kind, t.community)) + ' · by ' + escHtml(t.author) + ' · ' + timeHtml(t.created_at) + ' · ' +
    escHtml(plural(t.comments || 0, 'comment', 'comments')) + '</div></div></a>';
}

// ── Community / account / listing ──

async function renderCommunity(name) {
  let data;
  try { data = await getJson('/sandbox/api/communities/' + encodeURIComponent(name)); }
  catch { notFound('This community'); return; }
  const c = data.community;
  const head = '<div class="crumbs"><a href="/sandbox">Demo Sandbox</a> › ' + escHtml(communityLabel(c.kind, c.name)) + '</div>' +
    '<div class="card pad"><h1 class="sb-h">' + escHtml(c.kind === 'review' ? c.title : communityLabel(c.kind, c.name)) +
    '</h1><p class="sb-muted">' + escHtml(c.description || c.title) + '</p><p class="meta">' +
    escHtml(plural(data.total, 'post', 'posts')) + '</p></div>';
  let body;
  if (c.kind === 'photo') {
    body = '<div class="ph-grid">' + data.threads.map(t => '<a href="' + escHtml(threadHref(t)) + '"><div class="ph-image" data-seed="' +
      escHtml(String(t.image_seed)) + '"></div><div class="meta">' + escHtml(plural(t.comments || 0, 'comment', 'comments')) + '</div></a>').join('') + '</div>';
  } else {
    body = '<div class="list">' + data.threads.map(t => c.kind === 'review' ? reviewRow(t) : threadRow(t)).join('') + '</div>';
  }
  byId('sb-main').innerHTML = head + '<h2 class="sb-h2">Posts</h2>' + body;
  paintImages();
}

function reviewRow(t) {
  return '<a class="card row" href="' + escHtml(threadHref(t)) + '"><div>' + stars(t.rating) + '<div class="title">' +
    escHtml(t.title) + '</div><div class="meta">by ' + escHtml(t.author) + ' · ' + timeHtml(t.created_at) + '</div></div></a>';
}

// ── Threads ──

function childrenOf(parentId) {
  return SB.comments.filter(c => (c.parent_id || null) === parentId);
}

function voteHtml(points, vertical) {
  return '<button type="button" data-act="vote" data-dir="1" aria-label="Upvote">▲</button>' +
    '<span class="score" data-base="' + escHtml(String(points)) + '">' + escHtml(String(points)) + '</span>' +
    '<button type="button" data-act="vote" data-dir="-1" aria-label="Downvote">▼</button>';
}

function commentNode(c) {
  const kids = childrenOf(c.id);
  return '<div class="cmt' + (c.is_brand ? ' is-brand' : '') + '" id="c' + c.id + '" data-id="' + c.id + '">' +
    '<div class="cmt-head"><button type="button" class="collapse" data-act="collapse" aria-label="Collapse">[–]</button>' +
    '<span class="author' + (c.is_brand ? ' brand' : '') + '">' + escHtml(c.author) + '</span>' + flairHtml(c.flair, c.is_brand) +
    '<span>· ' + escHtml(plural(c.points, 'point', 'points')) + '</span><span>· ' + timeHtml(c.created_at) + '</span></div>' +
    '<div class="cmt-body md">' + mdHtml(c.body) + '</div>' +
    '<div class="cmt-actions">' + voteHtml(c.points) +
      '<button type="button" data-act="reply" data-parent="' + c.id + '">Reply</button>' +
      '<button type="button" data-act="copy-link" data-id="' + c.id + '">Copy link</button></div>' +
    (kids.length ? '<div class="children">' + kids.map(commentNode).join('') + '</div>' : '') +
    '</div>';
}

function composerHtml(verb) {
  const brand = SB.session.brand;
  return '<form class="composer" id="composer" novalidate>' +
    '<div class="as">' + escHtml(verb) + ' as <strong>' + escHtml(brand.handle) + '</strong> ' + flairHtml(brand.flair, true) + '</div>' +
    '<div class="replying" id="replying"></div>' +
    '<label class="hidden" for="composer-text">Your reply</label>' +
    '<textarea id="composer-text" maxlength="10000" placeholder="Paste the approved reply here"></textarea>' +
    '<div class="composer-actions"><button type="button" class="btn hidden" id="reply-to-post" data-act="reply-post">Reply to the post instead</button>' +
    '<button type="submit" class="btn primary" id="composer-submit">' + escHtml(verb === 'Responding' ? 'Respond' : 'Comment') + '</button></div>' +
    '<div class="err hidden" id="composer-err" role="alert"></div></form>';
}

function postHeader(t, community) {
  return '<div class="crumbs"><a href="/sandbox">Demo Sandbox</a> › <a href="' + escHtml(communityHref({kind: t.kind, name: t.community})) + '">' +
    escHtml(communityLabel(t.kind, t.community)) + '</a></div>';
}

function renderForum(t) {
  const tops = childrenOf(null);
  byId('sb-main').innerHTML = postHeader(t) +
    '<article class="card post"><div class="vote">' + voteHtml(t.points) + '</div><div class="post-main">' +
      '<div class="meta">' + escHtml(communityLabel('forum', t.community)) + ' · posted by ' + escHtml(t.author) + ' · ' + timeHtml(t.created_at) +
      ' ' + flairHtml(t.flair) + '</div><h1>' + escHtml(t.title) + '</h1><div class="md">' + mdHtml(t.body) + '</div>' +
      '<div class="post-actions"><span class="meta">' + escHtml(plural(SB.comments.length, 'comment', 'comments')) + '</span>' +
        '<button type="button" data-act="reply-post">Reply</button><button type="button" data-act="copy-link" data-id="">Copy link</button></div>' +
      '<div id="composer-slot-post">' + composerHtml('Commenting') + '</div>' +
    '</div></article>' +
    '<section class="card comments" aria-label="Comments">' + (tops.length ? tops.map(commentNode).join('') : '<p class="sb-muted">No comments yet.</p>') + '</section>';
}

function photoComment(c, isReply) {
  return '<div class="ph-cmt' + (isReply ? ' reply' : '') + (c.is_brand ? ' is-brand' : '') + '" id="c' + c.id + '" data-id="' + c.id + '">' +
    '<span class="who">' + escHtml(c.author) + '</span>' + flairHtml(c.is_brand ? c.flair : '', c.is_brand) + ' ' + inlineHtml(c.body) +
    '<div class="meta">' + timeHtml(c.created_at) + ' · ' + escHtml(plural(c.points, 'like', 'likes')) +
    '<button type="button" data-act="reply" data-parent="' + c.id + '">Reply</button>' +
    '<button type="button" data-act="copy-link" data-id="' + c.id + '">Copy link</button></div></div>' +
    childrenOf(c.id).map(k => photoComment(k, true)).join('');
}

function renderPhoto(t) {
  byId('sb-main').classList.add('wide');
  byId('sb-main').innerHTML = postHeader(t) +
    '<article class="card photo-card"><div class="ph-image" data-seed="' + escHtml(String(t.image_seed)) + '">' +
      (t.flair === 'Video' ? '<span class="ph-play" aria-hidden="true">▶</span>' : '') +
      '<span class="ph-label">Demo image placeholder</span></div>' +
    '<div class="ph-side"><div class="ph-head"><span class="avatar" data-seed="' + escHtml(t.author) + '"></span>' +
      '<span class="who">' + escHtml(t.author) + '</span><span class="meta">' + timeHtml(t.created_at) + '</span></div>' +
      '<div class="ph-scroll"><div class="ph-cmt"><span class="who">' + escHtml(t.author) + '</span> ' + inlineHtml(t.body) + '</div>' +
        childrenOf(null).map(c => photoComment(c, false)).join('') + '</div>' +
      '<div class="ph-actions"><button type="button" class="heart" data-act="heart" aria-pressed="false" aria-label="Like">♥</button>' +
        '<span class="meta" id="likes" data-base="' + escHtml(String(t.points)) + '">' + escHtml(plural(t.points, 'like', 'likes')) + '</span>' +
        '<button type="button" data-act="copy-link" data-id="">Copy link</button></div>' +
      '<div id="composer-slot-post">' + composerHtml('Commenting') + '</div>' +
    '</div></article>';
}

function renderReview(t, community) {
  byId('sb-main').innerHTML = postHeader(t) +
    '<div class="card pad listing-head"><h1 class="sb-h">' + escHtml(community ? community.title : t.community) + '</h1>' +
      '<span class="sb-muted">' + escHtml(community ? community.description : '') + '</span></div>' +
    '<article class="card pad review">' + stars(t.rating) + '<h1>' + escHtml(t.title) + '</h1>' +
      '<div class="meta">by ' + escHtml(t.author) + ' · ' + timeHtml(t.created_at) + ' · ' + escHtml(plural(t.points, 'person', 'people')) + ' found this useful</div>' +
      '<div class="md">' + mdHtml(t.body) + '</div>' +
      '<div class="post-actions"><button type="button" data-act="reply-post">Respond</button><button type="button" data-act="copy-link" data-id="">Copy link</button></div>' +
      '<div id="composer-slot-post">' + composerHtml('Responding') + '</div></article>' +
    '<section class="card comments" aria-label="Responses"><h2 class="sb-h2">Responses</h2>' +
      (SB.comments.length ? childrenOf(null).map(commentNode).join('') : '<p class="sb-muted">No responses yet.</p>') + '</section>';
}

async function renderThread(threadId, targetId) {
  let data;
  try { data = await getJson('/sandbox/api/threads/' + encodeURIComponent(threadId)); }
  catch { notFound('This post'); return; }
  SB.thread = data.thread;
  SB.comments = data.comments;
  SB.targetId = targetId;
  const t = data.thread;
  document.title = (t.title || t.author) + ' · Demo Sandbox';
  if (t.kind === 'photo') renderPhoto(t);
  else if (t.kind === 'review') renderReview(t, data.community);
  else renderForum(t);
  paintImages();
  const target = targetId && SB.comments.some(c => c.id === targetId) ? targetId : null;
  // Reply under the highlighted comment, unless it is the brand's own reply.
  const targetComment = SB.comments.find(c => c.id === target);
  setReplyTarget(targetComment && !targetComment.is_brand ? target : null);
  if (target) highlight(target);
}

function highlight(id) {
  const el = byId('c' + id);
  if (!el) return;
  el.classList.remove('target');
  void el.offsetWidth;  // restart the pulse animation
  el.classList.add('target');
  el.scrollIntoView({block: 'center', behavior: 'smooth'});
}

// The composer sits under the post, or under the comment being replied to.
function setReplyTarget(parentId) {
  const form = byId('composer');
  if (!form) return;
  SB.replyTo = parentId;
  const label = byId('replying');
  const toPost = byId('reply-to-post');
  if (parentId) {
    const c = SB.comments.find(x => x.id === parentId);
    const host = byId('c' + parentId);
    if (host) {
      const anchor = host.querySelector(':scope > .cmt-actions') || host.querySelector(':scope > .meta') || host;
      anchor.insertAdjacentElement('afterend', form);
    }
    label.innerHTML = 'Replying to <strong>' + escHtml(c ? c.author : 'comment') + '</strong>';
    toPost.classList.remove('hidden');
  } else {
    byId('composer-slot-post').appendChild(form);
    label.innerHTML = '';
    toPost.classList.add('hidden');
  }
}

async function submitComposer(event) {
  event.preventDefault();
  const text = byId('composer-text').value;
  const err = byId('composer-err');
  err.classList.add('hidden');
  if (!text.trim()) { err.textContent = 'Write or paste a reply first.'; err.classList.remove('hidden'); return; }
  const btn = byId('composer-submit');
  btn.disabled = true;
  const r = await postComment(SB.thread.id, SB.replyTo, text);
  btn.disabled = false;
  if (!r.ok) {
    err.textContent = (r.data && r.data.detail && String(r.data.detail)) || 'Could not post (' + r.status + ').';
    err.classList.remove('hidden');
    return;
  }
  await renderThread(SB.thread.id, null);
  highlight(r.data.id);
  showPosted(r.data.permalink);
}

function showPosted(permalink) {
  const el = byId('sb-posted');
  el.innerHTML = '<div class="line"><strong>Posted.</strong><span>Copy this comment link and paste it into Pulse "Mark posted".</span>' +
    '<button type="button" class="close" data-act="close-posted" aria-label="Close">✕</button></div>' +
    '<div class="line"><input type="text" readonly id="posted-url" value="' + escHtml(permalink) + '" aria-label="Comment link">' +
    '<button type="button" class="btn" data-act="copy-posted">Copy this comment link</button></div>';
  el.classList.remove('hidden');
}

async function copyText(text, okMsg) {
  try { await navigator.clipboard.writeText(text); flash(okMsg); }
  catch { flash('Copy failed: select the link and copy it.'); }
}

function flash(msg) {
  const who = byId('sb-who');
  const before = who.textContent;
  who.textContent = msg;
  setTimeout(() => { who.textContent = before; }, 2200);
}

// ── Guides ──

async function renderGuides(slug) {
  if (!slug) {
    const data = await getJson('/sandbox/api/guides');
    byId('sb-main').innerHTML = '<div class="crumbs"><a href="/sandbox">Demo Sandbox</a> › Demo guides</div>' +
      '<div class="guide-hero"><h1>Demo guides</h1><p>Placeholders for the guide pages replies may link to.</p></div><div class="list">' +
      data.guides.filter(g => g.slug !== 'index').map(g => '<a class="card row" href="/sandbox/guides/' + escHtml(encodeURIComponent(g.slug)) +
        '"><div class="title">' + escHtml(g.title) + '</div></a>').join('') + '</div>';
    return;
  }
  let g;
  try { g = await getJson('/sandbox/api/guides/' + encodeURIComponent(slug)); }
  catch { notFound('This guide'); return; }
  byId('sb-main').innerHTML = '<div class="crumbs"><a href="/sandbox">Demo Sandbox</a> › <a href="/sandbox/guides">Demo guides</a></div>' +
    '<div class="guide-hero"><p class="meta">DEMO GUIDE</p><h1>' + escHtml(g.title) + '</h1></div>' +
    '<article class="card pad guide">' + g.paragraphs.map(p => '<p>' + escHtml(p) + '</p>').join('') + '</article>';
}

// ── Router ──

function route() {
  const path = location.pathname.replace(/\/+$/, '');
  let m;
  if ((m = path.match(/^\/sandbox\/f\/c\/([a-z0-9_]+)\/(\d+)(?:\/comment\/(\d+))?$/))) return renderThread(Number(m[2]), m[3] ? Number(m[3]) : hashTarget());
  if ((m = path.match(/^\/sandbox\/[pr]\/(\d+)(?:\/comment\/(\d+))?$/))) return renderThread(Number(m[1]), m[2] ? Number(m[2]) : hashTarget());
  if ((m = path.match(/^\/sandbox\/(?:f\/c|u|l)\/([a-z0-9_]+)$/))) return renderCommunity(m[1]);
  if ((m = path.match(/^\/sandbox\/guides(?:\/([a-z0-9-]+))?$/))) return renderGuides(m[1] || '');
  return renderIndex();
}

function hashTarget() {
  const m = location.hash.match(/^#c(\d+)$/);
  return m ? Number(m[1]) : null;
}

// ── Events ──

const ACTS = {
  'vote': el => {
    const box = el.parentElement;
    const score = box.querySelector('.score');
    const dir = Number(el.dataset.dir);
    const wasOn = el.classList.contains(dir > 0 ? 'on-up' : 'on-down');
    box.querySelectorAll('button[data-act="vote"]').forEach(b => b.classList.remove('on-up', 'on-down'));
    if (!wasOn) el.classList.add(dir > 0 ? 'on-up' : 'on-down');
    score.textContent = String(Number(score.dataset.base) + (wasOn ? 0 : dir));
  },
  'collapse': el => {
    const node = el.closest('.cmt');
    node.classList.toggle('collapsed');
    el.textContent = node.classList.contains('collapsed') ? '[+]' : '[–]';
  },
  'reply': el => { setReplyTarget(Number(el.dataset.parent)); byId('composer-text').focus(); },
  'reply-post': () => { setReplyTarget(null); byId('composer-text').focus(); },
  'copy-link': el => {
    const id = Number(el.dataset.id);
    const path = id ? commentHref(SB.thread, id) : threadHref(SB.thread);
    copyText(location.origin + path, 'Link copied.');
  },
  'heart': el => {
    const on = !el.classList.contains('on');
    el.classList.toggle('on', on);
    el.setAttribute('aria-pressed', String(on));
    const likes = byId('likes');
    const n = Number(likes.dataset.base) + (on ? 1 : 0);
    likes.textContent = plural(n, 'like', 'likes');
  },
  'copy-posted': () => copyText(byId('posted-url').value, 'Comment link copied.'),
  'close-posted': () => byId('sb-posted').classList.add('hidden'),
};

document.addEventListener('click', event => {
  const el = event.target.closest('[data-act]');
  if (!el) return;
  const handler = ACTS[el.dataset.act];
  if (handler) { event.preventDefault(); handler(el); }
});

document.addEventListener('submit', event => {
  if (event.target.id === 'composer') submitComposer(event);
});

window.addEventListener('hashchange', () => { const t = hashTarget(); if (t) highlight(t); });

async function init() {
  try {
    SB.session = await getJson('/sandbox/api/session');
    byId('sb-who').textContent = 'Signed in as ' + SB.session.brand.handle + ' (demo brand account)';
    await route();
  } catch {
    byId('sb-main').innerHTML = '<div class="card pad"><p>The demo sandbox is not available.</p></div>';
  }
}

init();
