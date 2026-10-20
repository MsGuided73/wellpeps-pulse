'use strict';

// WellPeps Pulse — the Knowledge view: the documents the agent follows.
// A library table (admins approve or reject what is in review), an upload
// flow (file -> base64 JSON, 10 MB limit, "Must follow" needs an approver),
// and the Smart Patient's Guides on file. Loaded after hq.js; uses app.js
// helpers (request, showToast, escHtml, can, timeHtml, emptyState, TAB_LOAD).
//
// Routes: GET /api/knowledge/documents -> {documents:[{id, filename, kind,
// authority, programs, approved_by, effective_date, supersedes_id, note,
// status (in_review | active | rejected | superseded), size_bytes,
// created_at, ...}]}; POST /api/knowledge/documents {filename,
// content_base64, kind, authority, programs, approved_by, effective_date,
// supersedes_id, note}; POST /api/knowledge/documents/{id}/approve|reject
// (admin); GET /api/knowledge/documents/{id}/text -> {id, text}; POST
// /api/knowledge/documents/{id}/edit {text?, kind, authority, programs,
// approved_by, effective_date, note} (reviewer and up: a new version in
// review). Errors arrive as {detail}. A 404 shows "Not available yet".

const Knowledge = (() => {
  const KINDS = [
    ['approved_messaging', 'Approved messaging'], ['compliance_rule', 'Compliance rule'],
    ['voice_style', 'Voice and style'], ['program_status', 'Program or product status'],
    ['guide', "Smart Patient's Guide"], ['community_rules', 'Community or forum rules'],
    ['facts_faq', 'Facts and FAQ'], ['strategy_training', 'Strategy or training'], ['other', 'Other'],
  ];
  const KIND_LABEL = Object.fromEntries(KINDS);
  const AUTH = {must_follow: ['must', 'Must follow'], reference: ['ref', 'Use as reference'], example: ['ex', 'Example only']};
  const STATUS = {active: ['ok', 'In use'], in_review: ['info', 'In review'], rejected: ['', 'Rejected'], superseded: ['', 'Replaced']};
  const PROGRAMS = ['All programs', 'Weight Loss', 'Hair Restoration', 'Sexual Wellness', 'Healthy Aging & Vitality'];
  const EXTENSIONS = ['.pdf', '.docx', '.txt', '.md'];
  const MAX_BYTES = 10 * 1024 * 1024;
  const BYTES_PER_MB = 1024 * 1024;
  const BYTES_PER_KB = 1024;
  const MUST_FOLLOW_KINDS = ['compliance_rule', 'approved_messaging', 'voice_style', 'community_rules'];
  const EDITABLE = ['active', 'in_review', 'rejected'];
  const MAX_EDIT_CHARS = 1000000;

  let docs = [];
  let editing = null;        // {id, text} while the edit form is open
  let filter = 'all';
  let rejecting = null;      // document id awaiting "Yes, reject"
  let seq = 0;
  const staged = new Map();  // card id -> File

  const $ = id => document.getElementById(id);
  const isReviewing = d => d.status === 'in_review';
  const fmtSize = n => (n >= BYTES_PER_MB ? (n / BYTES_PER_MB).toFixed(1) + ' MB' : Math.max(1, Math.round(n / BYTES_PER_KB)) + ' KB');
  const docName = id => { const d = docs.find(x => x.id === id); return d ? d.filename : ''; };

  // ── Library ──

  function inFilter(d) {
    if (filter === 'all') return true;
    if (filter === 'active') return d.status === 'active';
    if (filter === 'review') return d.status === 'in_review';
    return d.status === 'rejected' || d.status === 'superseded';
  }

  const pendingEditOf = d => docs.find(x => x.supersedes_id === d.id && x.status === 'in_review');

  function editCell(d) {
    if (!can('review') || !EDITABLE.includes(d.status)) return '';
    const pending = pendingEditOf(d);
    if (pending) return '<span class="cell-sub">An edit is waiting for review</span>';
    return '<div class="row"><button type="button" class="btn btn-ghost" data-kn="edit" data-id="' + d.id + '"' +
      (editing && editing.id === d.id ? ' aria-expanded="true"' : '') + '>Edit</button></div>';
  }

  function actionsCell(d) {
    if (!isReviewing(d) || !can('admin')) return editCell(d);
    if (rejecting === d.id) {
      return '<span class="cell-sub">Reject this document?</span><div class="row">' +
        '<button type="button" class="btn btn-danger" data-kn="reject-go" data-id="' + d.id + '">Yes, reject</button>' +
        '<button type="button" class="btn btn-ghost" data-kn="reject-cancel" data-id="' + d.id + '">Cancel</button></div>';
    }
    return '<div class="row"><button type="button" class="btn btn-primary" data-kn="approve" data-id="' + d.id + '">Approve</button>' +
      '<button type="button" class="btn btn-secondary" data-kn="reject" data-id="' + d.id + '">Reject</button></div>' + editCell(d);
  }

  function rowHtml(d) {
    const auth = AUTH[d.authority] || ['ref', d.authority || ''];
    const st = STATUS[d.status] || ['', d.status || ''];
    const progs = (d.programs || []).length ? (d.programs || []).join(', ') : 'All programs';
    const by = d.approved_by ? '<span class="cell-sub">Approved by ' + escHtml(d.approved_by) + '</span>' : '';
    const note = d.note ? '<span class="cell-sub">' + escHtml(d.note) + '</span>' : '';
    const repl = d.supersedes_id ? '<span class="cell-sub">Replaces: ' + escHtml(docName(d.supersedes_id) || 'document ' + d.supersedes_id) + '</span>' : '';
    return '<tr><td data-label="Document"><b>' + escHtml(d.filename) + '</b>' + by + note + '</td>' +
      '<td data-label="Kind">' + escHtml(KIND_LABEL[d.kind] || titleCase(d.kind)) + '<span class="cell-sub">' + escHtml(progs) + '</span></td>' +
      '<td data-label="How the agent treats it"><span class="auth ' + auth[0] + '">' + escHtml(auth[1]) + '</span>' + repl + '</td>' +
      '<td data-label="Added">' + timeHtml(d.created_at) + (d.effective_date ? '<span class="cell-sub">Takes effect ' + escHtml(d.effective_date) + '</span>' : '') + '</td>' +
      '<td data-label="Status"><span class="pill ' + st[0] + '">' + escHtml(st[1]) + '</span>' + actionsCell(d) + '</td></tr>';
  }

  function renderLibrary() {
    const rows = docs.filter(inFilter);
    $('kn-stats').innerHTML = '<div><b>' + docs.filter(d => d.status === 'active').length + '</b><span>in use</span></div>' +
      '<div><b>' + docs.filter(isReviewing).length + '</b><span>in review</span></div>';
    $('kn-lib').innerHTML = rows.length
      ? '<div class="tbl stack-sm"><table><caption class="visually-hidden">Documents the agent follows</caption><thead><tr>' +
        '<th scope="col">Document</th><th scope="col">Kind</th><th scope="col">How the agent treats it</th><th scope="col">Added</th><th scope="col">Status</th>' +
        '</tr></thead><tbody>' + rows.map(rowHtml).join('') + '</tbody></table></div>'
      : emptyState('&#9678;', docs.length ? 'Nothing here' : 'No documents yet',
        docs.length ? 'No document matches this filter.' : 'Add the first document above and it will wait here for a reviewer.');
    renderGuides();
  }

  function renderGuides() {
    const guides = docs.filter(d => d.kind === 'guide' && d.status === 'active');
    $('kn-guides').innerHTML = guides.length
      ? '<div class="gcards">' + guides.map(g => '<div class="gcard"><b>' + escHtml(g.filename) + '</b><span class="s">' +
        escHtml((g.programs || []).join(', ') || 'All programs') + '</span><span class="pill ok">In use</span></div>').join('') + '</div>'
      : '<p class="na">No guide documents are in use yet. Add a guide above (kind: Smart Patient\'s Guide) and it shows here once a reviewer approves it.</p>';
  }

  async function loadLibrary() {
    const r = await request('/api/knowledge/documents');
    if (r.status === 404) {
      $('kn-lib').innerHTML = '<p class="na"><b>Not available yet.</b> The document library turns on with the next update.</p>';
      $('kn-stats').innerHTML = '';
      return;
    }
    if (!r.ok) { $('kn-lib').innerHTML = offlineState(); return; }
    docs = (r.data && r.data.documents) || [];
    renderLibrary();
  }

  async function decide(id, verb) {
    const r = await send('/api/knowledge/documents/' + encodeURIComponent(id) + '/' + verb);
    rejecting = null;
    if (r.ok) showToast(verb === 'approve' ? 'Approved. The agent uses it from now on.' : 'Rejected. The agent will not use it.', 'success');
    await loadLibrary();
  }

  // ── Edit ──
  // An edit is stored as a new version in review that replaces this one. The
  // current version stays in use until an admin approves the edit.

  function editFormHtml(d, text) {
    const id = 'ke';
    const progs = d.programs || [];
    const pending = d.extraction === 'pending' && !text;
    const inUse = d.status === 'active';
    return '<article class="doc kn-edit" aria-labelledby="ke-h">' +
      '<div class="doc-head"><span class="name" id="ke-h">Edit ' + escHtml(d.filename) + '</span>' +
      '<button type="button" class="btn btn-ghost" data-kn="edit-cancel">Close</button></div>' +
      '<p class="note">' + (inUse
        ? 'Your edit is saved as a new version and waits for an admin’s approval. Until then the agent keeps using the current version.'
        : 'Your edit is saved as a new version and waits for an admin’s approval.') + '</p>' +
      '<div class="fields">' +
      '<div class="field wide"><label for="ke-text">Text the agent reads</label>' +
        (pending ? '<span class="hint" id="ke-text-hint">Pulse couldn’t read this PDF’s text. Paste it here so the agent can use it.</span>' : '') +
        '<textarea id="ke-text" class="kn-text" maxlength="' + MAX_EDIT_CHARS + '"' + (pending ? ' aria-describedby="ke-text-hint"' : '') + '>' +
        escHtml(text) + '</textarea></div>' +
      '<div class="field"><label for="' + id + '-kind">What kind of document is this?</label><select id="' + id + '-kind">' +
        KINDS.map(k => '<option value="' + k[0] + '"' + (k[0] === d.kind ? ' selected' : '') + '>' + escHtml(k[1]) + '</option>').join('') + '</select></div>' +
      '<div class="field"><span class="flabel" id="' + id + '-auth-l">How should the agent treat it?</span>' +
        '<div class="seg-radio" role="radiogroup" aria-labelledby="' + id + '-auth-l">' +
        Object.entries({must_follow: 'Must follow', reference: 'Reference', example: 'Example only'}).map(([v, l]) =>
          '<label><input type="radio" name="' + id + '-auth" value="' + v + '"' + (d.authority === v ? ' checked' : '') + '>' + l + '</label>').join('') +
        '</div><span class="hint">If you change the words of a "Must follow" document, put the name of whoever signed off this version.</span></div>' +
      '<div class="field wide"><span class="flabel" id="' + id + '-prog-l">Which programs does it apply to?</span>' +
        '<div class="chips" role="group" aria-labelledby="' + id + '-prog-l">' + PROGRAMS.map((p, i) =>
          '<label><input type="checkbox" value="' + escHtml(p) + '"' + ((i === 0 ? !progs.length : progs.includes(p)) ? ' checked' : '') + '>' +
          escHtml(p) + '</label>').join('') + '</div></div>' +
      '<div class="field"><label for="' + id + '-by">Approved by</label><input type="text" id="' + id + '-by" maxlength="120" autocomplete="off" value="' +
        escHtml(d.approved_by || '') + '"></div>' +
      '<div class="field"><label for="' + id + '-date">Takes effect</label><input type="date" id="' + id + '-date" value="' + escHtml(d.effective_date || '') + '"></div>' +
      '<div class="field wide"><label for="' + id + '-note">Anything the agent should know about it? (optional)</label>' +
        '<textarea id="' + id + '-note" maxlength="1000">' + escHtml(d.note || '') + '</textarea></div></div>' +
      '<div class="doc-foot"><button type="button" class="btn btn-primary" data-kn="edit-send">Send edit for review</button>' +
      '<button type="button" class="btn btn-ghost" data-kn="edit-cancel">Cancel</button>' +
      '<span class="field-err hidden" id="ke-err" role="alert"></span></div></article>';
  }

  async function openEdit(docId) {
    const d = docs.find(x => x.id === docId);
    if (!d) return;
    const r = await request('/api/knowledge/documents/' + encodeURIComponent(docId) + '/text');
    if (!r.ok) { showToast("Couldn't load this document's text. Try again.", 'error'); return; }
    editing = {id: docId, text: (r.data && r.data.text) || ''};
    $('kn-edit').innerHTML = editFormHtml(d, editing.text);
    renderLibrary();
    $('ke-text').focus();
  }

  function closeEdit() {
    const id = editing && editing.id;
    editing = null;
    $('kn-edit').innerHTML = '';
    renderLibrary();
    const btn = id && document.querySelector('[data-kn="edit"][data-id="' + id + '"]');
    if (btn) btn.focus();
  }

  async function sendEdit() {
    const d = docs.find(x => x.id === (editing && editing.id));
    if (!d) return;
    const err = $('ke-err');
    err.classList.add('hidden');
    const authority = document.querySelector('input[name="ke-auth"]:checked').value;
    const text = $('ke-text').value;
    const by = $('ke-by');
    if (authority === 'must_follow' && !by.value.trim()) {
      err.textContent = 'Add who approved it, or choose Reference.';
      err.classList.remove('hidden');
      by.setAttribute('aria-invalid', 'true');
      by.focus();
      return;
    }
    const programs = [...document.querySelectorAll('.kn-edit .chips input:checked')].map(x => x.value).filter(p => p !== PROGRAMS[0]);
    const body = {kind: $('ke-kind').value, authority, programs, approved_by: by.value.trim(),
      effective_date: $('ke-date').value, note: $('ke-note').value.trim()};
    if (text !== editing.text) body.text = text;
    const btn = document.querySelector('[data-kn="edit-send"]');
    btn.disabled = true;
    btn.textContent = 'Sending…';
    const r = await request('/api/knowledge/documents/' + encodeURIComponent(d.id) + '/edit', {method: 'POST', body});
    btn.disabled = false;
    btn.textContent = 'Send edit for review';
    if (!r.ok) {
      err.textContent = (r.data && r.data.detail && String(r.data.detail)) || "That didn't save. Check your connection and try again.";
      err.classList.remove('hidden');
      return;
    }
    editing = null;
    $('kn-edit').innerHTML = '';
    showToast('Edit sent for review. The agent uses it once an admin approves it.', 'success');
    await loadLibrary();
    $('lib-h').focus();
  }

  // ── Upload ──

  function guessKind(name) {
    const s = name.toLowerCase();
    if (/voice|tone|style/.test(s)) return 'voice_style';
    if (/link|messag|response/.test(s)) return 'approved_messaging';
    if (/complian|legit|protocol|policy/.test(s)) return 'compliance_rule';
    if (/guide|ebook/.test(s)) return 'guide';
    if (/forum|group|reddit|community rules/.test(s)) return 'community_rules';
    if (/faq|fact/.test(s)) return 'facts_faq';
    if (/status|program|product|live reference/.test(s)) return 'program_status';
    if (/engage|goal|strategy|training|manual|gameplan/.test(s)) return 'strategy_training';
    return 'other';
  }

  function stageCard(file) {
    const id = 'kf' + (++seq);
    staged.set(id, file);
    const ext = (file.name.split('.').pop() || '').slice(0, 4);
    const kind = guessKind(file.name);
    const mustDefault = MUST_FOLLOW_KINDS.includes(kind);
    const active = docs.filter(d => d.status === 'active');
    const card = document.createElement('article');
    card.className = 'doc';
    card.id = id;
    card.setAttribute('aria-label', 'Add ' + file.name);
    card.innerHTML = '<div class="doc-head"><span class="ext">' + escHtml(ext) + '</span><span class="name">' + escHtml(file.name) +
      '</span><span class="size">' + fmtSize(file.size) + '</span>' +
      '<button type="button" class="btn btn-ghost" data-kn="remove" data-card="' + id + '" aria-label="Remove ' + escHtml(file.name) + '">Remove</button></div>' +
      '<div class="fields">' +
      '<div class="field"><label for="' + id + '-kind">What kind of document is this?</label><select id="' + id + '-kind">' +
        KINDS.map(k => '<option value="' + k[0] + '"' + (k[0] === kind ? ' selected' : '') + '>' + escHtml(k[1]) + '</option>').join('') + '</select></div>' +
      '<div class="field"><span class="flabel" id="' + id + '-auth-l">How should the agent treat it?</span>' +
        '<div class="seg-radio" role="radiogroup" aria-labelledby="' + id + '-auth-l">' +
        '<label><input type="radio" name="' + id + '-auth" value="must_follow"' + (mustDefault ? ' checked' : '') + '>Must follow</label>' +
        '<label><input type="radio" name="' + id + '-auth" value="reference"' + (mustDefault ? '' : ' checked') + '>Reference</label>' +
        '<label><input type="radio" name="' + id + '-auth" value="example">Example only</label></div>' +
        '<span class="hint">"Must follow" needs the name of whoever approved it.</span></div>' +
      '<div class="field wide"><span class="flabel" id="' + id + '-prog-l">Which programs does it apply to?</span>' +
        '<div class="chips" role="group" aria-labelledby="' + id + '-prog-l">' + PROGRAMS.map((p, i) =>
          '<label><input type="checkbox" value="' + escHtml(p) + '"' + (i === 0 ? ' checked' : '') + '>' + escHtml(p) + '</label>').join('') + '</div></div>' +
      '<div class="field"><label for="' + id + '-by">Approved by</label><input type="text" id="' + id + '-by" maxlength="120" placeholder="e.g. a compliance reviewer\'s name" autocomplete="off"></div>' +
      '<div class="field"><label for="' + id + '-date">Takes effect</label><input type="date" id="' + id + '-date" value="' + new Date().toLocaleDateString('en-CA') + '"></div>' +
      '<div class="field"><label for="' + id + '-sup">Replaces an older document?</label><select id="' + id + '-sup">' +
        '<option value="">No, it adds to what is there</option>' + active.map(d => '<option value="' + d.id + '">' + escHtml(d.filename) + '</option>').join('') + '</select></div>' +
      '<div class="field wide"><label for="' + id + '-note">Anything the agent should know about it? (optional)</label>' +
        '<textarea id="' + id + '-note" maxlength="1000" placeholder="e.g. Use this for how replies should sound."></textarea></div></div>' +
      '<div class="doc-foot"><button type="button" class="btn btn-primary" data-kn="send" data-card="' + id + '">Send for review</button>' +
      '<span class="field-err hidden" id="' + id + '-err" role="alert"></span></div>';
    $('kn-staged').appendChild(card);
    card.querySelector('select').focus();
  }

  function takeFiles(files) {
    [...files].forEach(f => {
      const lower = f.name.toLowerCase();
      if (!EXTENSIONS.some(e => lower.endsWith(e))) {
        showToast(f.name + ' is not a supported type. Use PDF, Word (.docx), text or Markdown.', 'error');
      } else if (f.size > MAX_BYTES) {
        showToast(f.name + ' is over 10 MB. Split it or compress it, then try again.', 'error');
      } else if (!f.size) {
        showToast(f.name + ' is empty.', 'error');
      } else {
        stageCard(f);
      }
    });
  }

  function readBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  function showCardError(id, message) {
    const err = $(id + '-err');
    err.textContent = message;
    err.classList.remove('hidden');
  }

  async function sendCard(id) {
    const card = $(id), file = staged.get(id);
    const err = $(id + '-err');
    err.classList.add('hidden');
    const authority = card.querySelector('input[name="' + id + '-auth"]:checked').value;
    const by = $(id + '-by');
    by.removeAttribute('aria-invalid');
    by.removeAttribute('aria-describedby');
    if (authority === 'must_follow' && !by.value.trim()) {
      showCardError(id, 'Add who approved it, or choose Reference.');
      by.setAttribute('aria-invalid', 'true');
      by.setAttribute('aria-describedby', id + '-err');
      by.focus();
      return;
    }
    const programs = [...card.querySelectorAll('.chips input:checked')].map(x => x.value).filter(p => p !== PROGRAMS[0]);
    const btn = card.querySelector('[data-kn="send"]');
    btn.disabled = true;
    btn.textContent = 'Sending…';
    let content;
    try { content = await readBase64(file); } catch { btn.disabled = false; btn.textContent = 'Send for review'; showCardError(id, "Couldn't read that file."); return; }
    const sup = $(id + '-sup').value;
    const body = {filename: file.name, content_base64: content, kind: $(id + '-kind').value, authority, programs,
      approved_by: by.value.trim(), effective_date: $(id + '-date').value, note: $(id + '-note').value.trim()};
    if (sup) body.supersedes_id = Number(sup);
    const r = await request('/api/knowledge/documents', {method: 'POST', body});
    btn.disabled = false;
    btn.textContent = 'Send for review';
    if (!r.ok) {
      showCardError(id, r.status === 404 ? 'Uploading turns on with the next update.'
        : (r.data && r.data.detail && String(r.data.detail)) || "That didn't upload. Check your connection and try again.");
      return;
    }
    staged.delete(id);
    card.remove();
    showToast('Sent for review. A reviewer will check it before the agent uses it.', 'success');
    await loadLibrary();
    $('lib-h').focus();
  }

  // ── Wiring ──

  function init() {
    TAB_LOAD.knowledge = async () => {
      const canAdd = can('review');
      $('kn-drop').classList.toggle('hidden', !canAdd);
      $('kn-file').disabled = !canAdd;
      if (!canAdd && !$('kn-roles')) {
        $('kn-staged').insertAdjacentHTML('beforebegin', '<p class="na" id="kn-roles">Your role can read the library but not add documents.</p>');
      }
      await loadLibrary();
    };
    $('lib-h').setAttribute('tabindex', '-1');
    $('kn-file').addEventListener('change', () => { takeFiles($('kn-file').files); $('kn-file').value = ''; });
    const drop = $('kn-drop');
    ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => takeFiles(e.dataTransfer.files));
    $('kn-filter').addEventListener('click', e => {
      const b = e.target.closest('button[data-f]');
      if (!b) return;
      filter = b.dataset.f;
      document.querySelectorAll('#kn-filter button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      renderLibrary();
    });
    $('knowledge').addEventListener('click', e => {
      const b = e.target.closest('[data-kn]');
      if (!b || b.disabled) return;
      const act = b.dataset.kn, id = Number(b.dataset.id);
      if (act === 'remove') { staged.delete(b.dataset.card); $(b.dataset.card).remove(); }
      else if (act === 'send') sendCard(b.dataset.card);
      else if (act === 'approve') decide(id, 'approve');
      else if (act === 'reject') { rejecting = id; renderLibrary(); const go = document.querySelector('[data-kn="reject-go"]'); if (go) go.focus(); }
      else if (act === 'reject-cancel') { rejecting = null; renderLibrary(); }
      else if (act === 'reject-go') decide(id, 'reject');
      else if (act === 'edit') openEdit(id);
      else if (act === 'edit-cancel') closeEdit();
      else if (act === 'edit-send') sendEdit();
    });
  }

  init();
  return {loadLibrary};
})();
