'use strict';

// WellPeps Pulse — AI Rewrite for a drafted reply. Select text inside a draft
// (or right-click a selection) to get a menu: Friendlier, More professional,
// Shorter, Simpler, or your own instruction. The suggestion comes back with
// the compliance tier and hits for the NEW text; the person chooses Replace,
// Try again or Cancel, and nothing changes until they do.
//
// Server: POST /api/mentions/{id}/rewrite {passage, instruction}
//   -> {ok, text, tier, hits:[{rule, reason}]} | {ok:false, error}
// A 404 means the route is not deployed yet: the menu says so calmly.
//
// Loaded before home.js; uses app.js helpers (request, escHtml, label). The
// host (home.js) tells this module where drafts are and how to change one:
//   host.find(node)             -> card (or null) for a node inside a draft
//   host.apply(card, from, to)  -> true when the passage was replaced
//   host.markRewritten(card)
// Same CSP rules as app.js: no inline style or handlers; positions are set
// through the CSSOM.

const RewriteUI = (() => {
  const OPTIONS = [
    ['Friendlier', 'Make it friendlier and warmer, like a helpful person in the thread.'],
    ['More professional', 'Make it more professional and polished, but still plain and human.'],
    ['Shorter', 'Make it shorter and tighter without losing the point.'],
    ['Simpler', 'Use simpler, everyday words and shorter sentences.'],
  ];
  const PREVIEW_CHARS = 160;
  const SELECTION_DELAY_MS = 120;
  const EDGE = 8;

  let host = null;
  let root = null;
  let bubble = null;
  let menu = null;
  let target = null;     // {card, kind: 'text'|'textarea', passage, el?, start?, end?, rect}
  let last = null;       // {instruction, label, text, tier, hits}
  let aborter = null;
  let timer = 0;

  const asEl = n => (n && n.nodeType === 1 ? n : n && n.parentElement);
  const clip = s => (s.length > PREVIEW_CHARS ? s.slice(0, PREVIEW_CHARS).trimEnd() + '…' : s);

  function currentSelection() {
    const ae = document.activeElement;
    if (ae && ae.tagName === 'TEXTAREA' && root.contains(ae)) {
      const card = host.find(ae);
      const passage = ae.value.slice(ae.selectionStart, ae.selectionEnd);
      if (!card || !passage.trim()) return null;
      return {card, kind: 'textarea', el: ae, passage, start: ae.selectionStart, end: ae.selectionEnd,
        rect: ae.getBoundingClientRect()};
    }
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    let range = sel.getRangeAt(0);
    const inTxt = n => asEl(n) && asEl(n).closest('.draft .txt');
    const txt = inTxt(range.startContainer);
    if (!txt) return null;
    if (inTxt(range.endContainer) !== txt) {
      // A triple-click ends at the start of the next block: clamp the selection to this reply.
      range = range.cloneRange();
      range.setEnd(txt, txt.childNodes.length);
    }
    const card = host.find(range.startContainer);
    const passage = range.toString();
    if (!card || !passage.trim()) return null;
    return {card, kind: 'text', passage, rect: range.getBoundingClientRect()};
  }

  function placeAt(el, x, y) {
    const w = el.offsetWidth, h = el.offsetHeight;
    el.style.left = Math.max(EDGE, Math.min(x, window.innerWidth - w - EDGE)) + 'px';
    el.style.top = Math.max(EDGE, Math.min(y, window.innerHeight - h - EDGE)) + 'px';
  }

  // Keep the menu on screen after its content changes height.
  function reclamp() {
    placeAt(menu, parseFloat(menu.style.left) || EDGE, parseFloat(menu.style.top) || EDGE);
  }

  function updateBubble() {
    if (!menu.hidden) return;
    const t = currentSelection();
    if (!t) { bubble.hidden = true; return; }
    target = t;
    bubble.hidden = false;
    const r = t.rect;
    const y = r.top - bubble.offsetHeight - EDGE;
    placeAt(bubble, r.left + r.width / 2 - bubble.offsetWidth / 2, y < EDGE ? r.bottom + EDGE : y);
  }

  const scheduleBubble = () => { clearTimeout(timer); timer = setTimeout(updateBubble, SELECTION_DELAY_MS); };

  function closeMenu(restoreFocus) {
    if (aborter) { aborter.abort(); aborter = null; }
    menu.hidden = true;
    bubble.hidden = true;
    if (restoreFocus && target && target.kind === 'textarea') target.el.focus();
  }

  function openMenu(x, y) {
    bubble.hidden = true;
    renderOptions();
    menu.hidden = false;
    placeAt(menu, x, y);
    menu.querySelector('.rw-opt').focus();
  }

  // ── Menu screens ──

  function renderOptions() {
    menu.innerHTML = '<p class="rw-title">Rewrite selection</p>' +
      '<p class="rw-quote">' + escHtml(clip(target.passage)) + '</p>' +
      '<div class="rw-opts">' + OPTIONS.map((o, i) =>
        '<button type="button" class="rw-opt" data-rw="' + i + '">' + escHtml(o[0]) + '</button>').join('') + '</div>' +
      '<form class="rw-custom"><label for="rw-ask" class="visually-hidden">Describe the change you want</label>' +
      '<input id="rw-ask" type="text" maxlength="200" placeholder="Or describe it, e.g. sound more upbeat">' +
      '<button class="btn btn-primary" type="submit">Rewrite</button></form>';
  }

  function renderBusy(title) {
    menu.innerHTML = '<p class="rw-title">' + escHtml(title) + '</p>' +
      '<p class="rw-status" role="status">Rewriting… this usually takes 20 to 40 seconds.</p>' +
      '<div class="rw-actions"><button type="button" class="btn btn-secondary" data-rwact="close">Cancel</button></div>';
    reclamp();
    menu.querySelector('[data-rwact="close"]').focus();
  }

  function checkLine(tier, hits) {
    const tone = {green: 'ok', yellow: 'info', red: 'bad'}[tier] || 'info';
    const text = tier ? label('tier', tier) : 'Compliance filter did not run';
    let html = '<p class="rw-check"><span class="pill ' + tone + '">' + escHtml(text) + '</span></p>';
    if (hits && hits.length) {
      html += '<ul class="rw-hits">' + hits.map(h => '<li>' + escHtml((h.rule || h.rule_id) ? (h.rule || h.rule_id) + ': ' : '') +
        escHtml(h.reason || '') + '</li>').join('') + '</ul>';
    }
    if (tier === 'red') {
      html += '<p class="rw-hint">This wording would be blocked at approval. You can still replace it and fix it, or try again.</p>';
    }
    return html;
  }

  function renderResult(title) {
    menu.innerHTML = '<p class="rw-title">' + escHtml(title) + '</p><p class="rw-label">Suggestion</p>' +
      '<p class="rw-result">' + escHtml(last.text) + '</p>' + checkLine(last.tier, last.hits) +
      '<p class="rw-hint">Give it a read before you approve. Pulse checks the whole reply again when you save it.</p>' +
      '<div class="rw-actions"><button type="button" class="btn btn-primary" data-rwact="replace">Replace</button>' +
      '<button type="button" class="btn btn-secondary" data-rwact="again">Try again</button>' +
      '<button type="button" class="btn btn-ghost" data-rwact="close">Cancel</button></div>';
    reclamp();
    menu.querySelector('[data-rwact="replace"]').focus();
  }

  function renderError(message, title, canRetry) {
    menu.innerHTML = '<p class="rw-title">' + escHtml(title) + '</p>' +
      '<p class="rw-error" role="alert">' + escHtml(message) + '</p>' +
      '<div class="rw-actions">' + (canRetry ? '<button type="button" class="btn btn-primary" data-rwact="again">Try again</button>' : '') +
      '<button type="button" class="btn btn-ghost" data-rwact="close">Cancel</button></div>';
    reclamp();
    menu.querySelector('.btn').focus();
  }

  async function requestRewrite(instruction, title) {
    last = {instruction, label: title, text: '', tier: '', hits: []};
    renderBusy(title);
    aborter = new AbortController();
    const signal = aborter.signal;
    try {
      const r = await request('/api/mentions/' + encodeURIComponent(target.card.id) + '/rewrite',
        {method: 'POST', body: {passage: target.passage, instruction}, signal});
      if (signal.aborted) return;
      if (r.status === 404 && !(r.data && (r.data.error || r.data.detail))) {
        renderError("AI Rewrite isn't available yet. Edit the reply by hand for now.", title, false);
        return;
      }
      const data = r.data;
      if (!r.ok || !data || data.ok === false) {
        const why = data && (data.error || data.detail);
        renderError(why ? String(why) : "The rewrite didn't work. Try again.", title, true);
        return;
      }
      last.text = String(data.text || '');
      last.tier = data.tier || '';
      last.hits = Array.isArray(data.hits) ? data.hits : [];
      if (!last.text.trim()) { renderError('The rewrite came back empty. Try again.', title, true); return; }
      renderResult(title);
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      renderError("Couldn't reach Pulse. Check your connection and try again.", title, true);
    } finally {
      aborter = null;
    }
  }

  function replaceNow() {
    const t = target;
    let ok = true;
    if (t.kind === 'textarea') {
      t.el.value = t.el.value.slice(0, t.start) + last.text + t.el.value.slice(t.end);
      t.el.dispatchEvent(new Event('input', {bubbles: true}));
    } else {
      ok = host.apply(t.card, t.passage, last.text);
    }
    if (!ok) { showToast("Couldn't find that passage in the draft. Try selecting within one sentence.", 'error'); return; }
    host.markRewritten(t.card);
    showToast('Passage rewritten. Give it a read before you approve.', 'success');
  }

  // ── Wiring ──

  function attach(rootEl, hostApi) {
    root = rootEl;
    host = hostApi;
    bubble = document.getElementById('rw-bubble');
    menu = document.getElementById('rw-menu');

    document.addEventListener('selectionchange', scheduleBubble);
    root.addEventListener('mouseup', scheduleBubble);
    root.addEventListener('keyup', scheduleBubble);
    window.addEventListener('scroll', () => { bubble.hidden = true; }, {passive: true});

    bubble.addEventListener('mousedown', e => e.preventDefault());   // keep the text selected
    bubble.addEventListener('click', () => {
      const b = bubble.getBoundingClientRect();
      openMenu(b.left, b.bottom + EDGE);
    });
    root.addEventListener('contextmenu', e => {
      const t = currentSelection();
      if (!t) return;                                                 // the normal browser menu otherwise
      e.preventDefault();
      target = t;
      openMenu(e.clientX, e.clientY);
    });
    menu.addEventListener('click', e => {
      const opt = e.target.closest('[data-rw]');
      if (opt) { const o = OPTIONS[Number(opt.dataset.rw)]; requestRewrite(o[1], o[0]); return; }
      const act = e.target.closest('[data-rwact]');
      if (!act) return;
      if (act.dataset.rwact === 'close') closeMenu(true);
      else if (act.dataset.rwact === 'again' && last) requestRewrite(last.instruction, last.label);
      else if (act.dataset.rwact === 'replace' && last && last.text) { replaceNow(); closeMenu(true); }
    });
    menu.addEventListener('submit', e => {
      e.preventDefault();
      const ask = menu.querySelector('#rw-ask');
      const value = ask.value.trim();
      if (!value) { ask.focus(); return; }
      requestRewrite(value, 'Custom rewrite');
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) closeMenu(true); });
    document.addEventListener('mousedown', e => {
      if (!menu.hidden && !menu.contains(e.target)) closeMenu(false);
    });
  }

  return {attach};
})();
