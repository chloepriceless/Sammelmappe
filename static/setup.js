// Setup form handler (externalised from setup.html for a strict `script-src 'self'` CSP).
document.getElementById('setup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const err = document.getElementById('err');
  err.textContent = '';
  if (fd.get('password') !== fd.get('password_confirm')) {
    err.textContent = 'Passwörter stimmen nicht überein';
    return;
  }
  try {
    const r = await fetch('/api/auth/setup', { method: 'POST', body: fd, redirect: 'manual' });
    if (r.status === 303 || r.type === 'opaqueredirect' || r.ok) { location.href = '/'; return; }
    const data = await r.json().catch(() => ({}));
    err.textContent = data.detail || 'Einrichtung fehlgeschlagen';
  } catch (ex) {
    err.textContent = 'Verbindungsfehler';
  }
});
