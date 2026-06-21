// Login form handler (externalised from login.html for a strict `script-src 'self'` CSP).
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const err = document.getElementById('err');
  err.textContent = '';
  try {
    const r = await fetch('/api/auth/login', { method: 'POST', body: fd, redirect: 'manual' });
    if (r.status === 303 || r.type === 'opaqueredirect' || r.ok) { location.href = '/'; return; }
    const data = await r.json().catch(() => ({}));
    err.textContent = data.detail || 'Anmeldung fehlgeschlagen';
  } catch (ex) {
    err.textContent = 'Verbindungsfehler';
  }
});
