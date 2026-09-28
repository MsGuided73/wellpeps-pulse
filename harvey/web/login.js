'use strict';

// Sign-in page. No inline script (the CSP forbids it); the session cookie is
// HttpOnly, so this page never sees the token.

function showError(msg) {
  const el = document.getElementById('login-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const btn = document.getElementById('login-btn');
  btn.disabled = true;
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        email: document.getElementById('email').value,
        password: document.getElementById('password').value,
      }),
    });
    if (r.ok) { window.location.assign('/'); return; }
    if (r.status === 429) showError('Too many failed attempts. Try again in 15 minutes.');
    else showError('Invalid email or password.');
  } catch {
    showError('Cannot reach the dashboard server.');
  } finally {
    btn.disabled = false;
  }
});
