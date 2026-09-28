'use strict';

// Passwords: the account menu, "Change password" (any user), the forced
// change screen (must_change_password), and the admin "Reset password"
// dialog on the Users tab. Uses app.js (ME, request, showToast, ACTIONS,
// loadUsers) and labels.js (LABELS). Same CSP rules as app.js: no inline
// style or handlers; every server value goes through escHtml or textContent.
// Password values never leave this page except in the POST body.

const PW_MIN = 12;
// No look-alikes (0/O, 1/l/I). 57 symbols x 20 = ~116 bits of entropy.
const PW_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
const PW_GROUPS = 5;
const PW_GROUP_LEN = 4;

let pwForced = false;
let pwOpener = null;
let resetTarget = '';
let resetOpener = null;

const byId = id => document.getElementById(id);

// ── Helpers ──

function generatePassword() {
  // Rejection sampling over crypto.getRandomValues: no modulo bias.
  const limit = 256 - (256 % PW_ALPHABET.length);
  const out = [];
  while (out.length < PW_GROUPS * PW_GROUP_LEN) {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    for (const b of bytes) {
      if (b < limit && out.length < PW_GROUPS * PW_GROUP_LEN) out.push(PW_ALPHABET[b % PW_ALPHABET.length]);
    }
  }
  const groups = [];
  for (let i = 0; i < PW_GROUPS; i++) groups.push(out.slice(i * PW_GROUP_LEN, (i + 1) * PW_GROUP_LEN).join(''));
  return groups.join('-');
}

function pwMessage(code) {
  return LABELS.pwError[code] || LABELS.pwError.failed;
}

function showFormError(id, code) {
  const el = byId(id);
  el.textContent = pwMessage(code);
  el.classList.remove('hidden');
}

function clearFormError(id) {
  const el = byId(id);
  el.textContent = '';
  el.classList.add('hidden');
}

function setVisible(input, visible) {
  input.type = visible ? 'text' : 'password';
  const toggle = input.parentElement.querySelector('.pw-toggle');
  if (!toggle) return;
  toggle.setAttribute('aria-pressed', visible ? 'true' : 'false');
  toggle.textContent = visible ? LABELS.pw.hide : LABELS.pw.show;
  toggle.setAttribute('aria-label', visible ? LABELS.pw.hide_label : LABELS.pw.show_label);
}

function resetFields(ids) {
  for (const id of ids) {
    const input = byId(id);
    input.value = '';
    setVisible(input, false);
  }
}

function focusables(dialog) {
  return [...dialog.querySelectorAll('button, input, select, textarea, a[href]')]
    .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length > 0);
}

function trapFocus(dialog, event) {
  if (event.key !== 'Tab') return;
  const items = focusables(dialog);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

// ── Account menu ──

function setUserMenu(open) {
  const chip = byId('user-chip');
  byId('user-menu').classList.toggle('hidden', !open);
  chip.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) byId('user-menu').querySelector('[role="menuitem"]').focus();
}

function userMenuOpen() {
  return !byId('user-menu').classList.contains('hidden');
}

function onUserMenuKey(event) {
  const items = [...byId('user-menu').querySelectorAll('[role="menuitem"]')];
  const i = items.indexOf(document.activeElement);
  if (event.key === 'Escape') { event.preventDefault(); setUserMenu(false); byId('user-chip').focus(); }
  else if (event.key === 'ArrowDown') { event.preventDefault(); items[(i + 1) % items.length].focus(); }
  else if (event.key === 'ArrowUp') { event.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  else if (event.key === 'Tab') setUserMenu(false);
}

// ── Change password (and the forced screen) ──

function openPasswordDialog(forced) {
  const dialog = byId('pw-dialog');
  pwOpener = forced ? null : byId('user-chip');
  setUserMenu(false);
  byId('pw-title').textContent = forced ? LABELS.pw.forced_title : LABELS.pw.title;
  byId('pw-lede').textContent = forced ? LABELS.pw.forced_lede : LABELS.pw.lede;
  byId('pw-submit').textContent = forced ? LABELS.pw.forced_submit : LABELS.pw.submit;
  byId('pw-cancel').classList.toggle('hidden', forced);
  byId('pw-signout').classList.toggle('hidden', !forced);
  byId('pw-username').value = ME ? ME.email : '';
  resetFields(['pw-current', 'pw-new', 'pw-confirm']);
  clearFormError('pw-error');
  if (!dialog.open) dialog.showModal();
  byId('pw-current').focus();
}

function closePasswordDialog() {
  if (pwForced) return;
  byId('pw-dialog').close();
}

// Called from app.js: at init when ME.must_change_password, and on any
// 403 password_change_required. Nothing else is shown until it's done.
function enterForcedChange() {
  if (pwForced) return;
  pwForced = true;
  document.body.classList.add('pw-forced');
  openPasswordDialog(true);
}

function checkNewPassword(current, next, confirm) {
  if (!current || !next || !confirm) return 'required';
  if ([...next].length < PW_MIN) return 'password_too_short';  // code points, like the server
  if (next !== confirm) return 'mismatch';
  if (ME && next.trim().toLowerCase() === ME.email.toLowerCase()) return 'password_is_email';
  if (next === current) return 'password_reused';
  return '';
}

async function submitPasswordChange() {
  const current = byId('pw-current').value;
  const next = byId('pw-new').value;
  const problem = checkNewPassword(current, next, byId('pw-confirm').value);
  if (problem) { showFormError('pw-error', problem); return; }
  clearFormError('pw-error');
  const submit = byId('pw-submit');
  submit.disabled = true;
  const r = await request('/api/me/password', {method: 'POST', body: {current_password: current, new_password: next}});
  submit.disabled = false;
  if (!r.ok) {
    const code = r.status === 0 ? 'network' : (r.data && r.data.error) || 'failed';
    showFormError('pw-error', code);
    if (code === 'current_password_incorrect') { byId('pw-current').value = ''; byId('pw-current').focus(); }
    return;
  }
  ME.csrf = r.data.csrf;
  resetFields(['pw-current', 'pw-new', 'pw-confirm']);
  if (pwForced) {
    // Start the dashboard fresh now that every API is open again.
    window.location.reload();
    return;
  }
  byId('pw-dialog').close();
  showToast(LABELS.pw.done, 'success');
}

// ── Admin: reset someone else's password ──

function openResetDialog(email) {
  resetTarget = email;
  resetOpener = document.activeElement;
  byId('reset-lede').textContent = LABELS.pw.reset_lede + ' ' + email + '.';
  resetFields(['reset-password']);
  byId('reset-copy').classList.add('hidden');
  byId('reset-copied').textContent = '';
  clearFormError('reset-error');
  byId('reset-dialog').showModal();
  byId('reset-password').focus();
}

function generateForReset() {
  const input = byId('reset-password');
  input.value = generatePassword();
  setVisible(input, true);
  byId('reset-copy').classList.remove('hidden');
  byId('reset-copied').textContent = '';
  clearFormError('reset-error');
  input.select();
}

async function copyResetPassword() {
  const input = byId('reset-password');
  try {
    await navigator.clipboard.writeText(input.value);
    byId('reset-copied').textContent = LABELS.pw.copied;
  } catch {
    setVisible(input, true);
    input.select();
    byId('reset-copied').textContent = LABELS.pw.copy_failed;
  }
}

async function submitReset() {
  const password = byId('reset-password').value;
  if ([...password].length < PW_MIN) { showFormError('reset-error', password ? 'password_too_short' : 'required'); return; }
  if (password.trim().toLowerCase() === resetTarget.toLowerCase()) { showFormError('reset-error', 'password_is_email'); return; }
  clearFormError('reset-error');
  const submit = byId('reset-submit');
  submit.disabled = true;
  const r = await request('/api/users/reset-password', {method: 'POST', body: {email: resetTarget, new_password: password}});
  submit.disabled = false;
  if (!r.ok) {
    showFormError('reset-error', r.status === 0 ? 'network' : (r.data && r.data.error) || 'failed');
    return;
  }
  byId('reset-dialog').close();
  showToast(LABELS.pw.reset_done, 'success');
  loadUsers();
}

// ── Events ──

Object.assign(ACTIONS, {
  'user-menu': () => setUserMenu(!userMenuOpen()),
  'pw-open': () => openPasswordDialog(false),
  'pw-cancel': () => closePasswordDialog(),
  'pw-toggle': el => { const input = byId(el.dataset.target); setVisible(input, input.type === 'password'); input.focus(); },
  'reset-user': el => openResetDialog(el.dataset.email),
  'reset-generate': () => generateForReset(),
  'reset-copy': () => copyResetPassword(),
  'reset-cancel': () => byId('reset-dialog').close(),
});

byId('pw-form').addEventListener('submit', (event) => { event.preventDefault(); submitPasswordChange(); });
byId('reset-form').addEventListener('submit', (event) => { event.preventDefault(); submitReset(); });

const pwDialog = byId('pw-dialog');
pwDialog.addEventListener('keydown', event => trapFocus(pwDialog, event));
// Esc closes the normal dialog; the forced screen cannot be dismissed.
pwDialog.addEventListener('cancel', (event) => { if (pwForced) event.preventDefault(); });
pwDialog.addEventListener('close', () => {
  if (pwForced) {  // e.g. a browser's repeated-Esc escape hatch: put the screen straight back
    pwDialog.showModal();
    byId('pw-current').focus();
    return;
  }
  resetFields(['pw-current', 'pw-new', 'pw-confirm']);
  if (pwOpener && pwOpener.isConnected) pwOpener.focus();
});

const resetDialog = byId('reset-dialog');
resetDialog.addEventListener('keydown', event => trapFocus(resetDialog, event));
resetDialog.addEventListener('close', () => {
  // Shown once: the temporary password is wiped whenever the dialog closes.
  resetFields(['reset-password']);
  byId('reset-copy').classList.add('hidden');
  resetTarget = '';
  if (resetOpener && resetOpener.isConnected) resetOpener.focus();
});

byId('user-menu').addEventListener('keydown', onUserMenuKey);
document.addEventListener('click', (event) => {
  if (userMenuOpen() && !event.target.closest('.user-menu-wrap')) setUserMenu(false);
});
