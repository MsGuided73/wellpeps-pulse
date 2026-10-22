'use strict';

// WellPeps Pulse — "Record what was posted" (R-30). A rep posts by hand (Pulse's
// reply, an edited one or their own words) and records the text actually sent,
// who sent it and when. POST /api/mentions/{id}/record-post keeps it in the
// append-only audit log and runs the compliance filter on that text.
// Loaded after app.js (request, showToast, ME). Same CSP rules as app.js: no inline
// style or handlers; nothing here builds HTML from server data.

const PostRecord = (() => {
  let current = null;            // {id, onSaved}
  let trigger = null;

  const $ = id => document.getElementById(id);

  // "YYYY-MM-DDTHH:MM" in the browser's local time (what a datetime-local input takes).
  function localValue(date) {
    const pad = n => String(n).padStart(2, '0');
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) +
      'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }

  function showError(text) {
    const err = $('post-error');
    err.textContent = text;
    err.classList.toggle('hidden', !text);
  }

  // card: {id, text, postUrl}; onSaved(record) runs after a successful save.
  function open(card, onSaved, from) {
    current = {id: card.id, onSaved};
    trigger = from || null;
    $('post-text').value = card.text || '';
    $('post-who').value = (ME && (ME.name || ME.email)) || '';
    const now = new Date();
    $('post-when').value = localValue(now);
    $('post-when').max = localValue(new Date(now.getTime() + 10 * 60000));
    $('post-url').value = '';
    showError('');
    $('post-dialog').showModal();
    $('post-text').focus();
  }

  function close() {
    $('post-dialog').close();
    if (trigger && document.body.contains(trigger)) trigger.focus();
    current = null;
  }

  async function save(event) {
    event.preventDefault();
    if (!current) return;
    const text = $('post-text').value.trim();
    const who = $('post-who').value.trim();
    const when = $('post-when').value;
    if (!text) { showError('Paste the reply exactly as it was posted.'); $('post-text').focus(); return; }
    if (!who) { showError('Say who posted it.'); $('post-who').focus(); return; }
    const at = when ? new Date(when) : null;
    if (!at || isNaN(at.getTime())) { showError('Choose the date and time it was posted.'); $('post-when').focus(); return; }
    showError('');
    $('post-submit').disabled = true;
    // request(), not send(): errors show inside the dialog, not as a toast.
    const r = await request('/api/mentions/' + encodeURIComponent(current.id) + '/record-post', {method: 'POST', body: {
      text, responder: who, posted_at: at.toISOString(), posted_url: $('post-url').value.trim(),
    }});
    $('post-submit').disabled = false;
    if (!r.ok) {
      const why = r.status === 0 ? "Pulse isn't responding. Check that it's still running, then try again. Your text is still here."
        : (r.data && (r.data.error || r.data.detail)) || "That didn't save. Try again.";
      showError(typeof why === 'string' ? why : "That didn't save. Check the fields and try again.");
      return;
    }
    const done = current.onSaved;
    close();
    const record = r.data && r.data.record;
    showToast(record && record.tier === 'red'
      ? 'Recorded. The compliance check flagged this reply; it is marked in the audit record.'
      : 'Recorded. Pulse logged who posted it and when.', 'success');
    if (done) done(record);
  }

  function init() {
    const form = $('post-form');
    if (!form) return;
    form.addEventListener('submit', save);
    $('post-cancel').addEventListener('click', close);
    $('post-dialog').addEventListener('cancel', event => { event.preventDefault(); close(); });
  }

  init();
  return {open};
})();
