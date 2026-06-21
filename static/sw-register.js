// Service-worker registration (externalised from index.html so the page can run
// under a strict `script-src 'self'` CSP — no inline scripts).
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
