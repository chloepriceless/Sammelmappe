// Minimal service worker — just enables PWA install on iOS.
// We deliberately don't cache API or HTML to avoid stale data.

const STATIC_CACHE = 'brs-static-v6';
const STATIC_FILES = [
  '/static/style.css',
  '/static/app.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache => cache.addAll(STATIC_FILES)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== STATIC_CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  // Only cache same-origin static assets
  if (url.origin !== location.origin || !url.pathname.startsWith('/static/')) return;
  event.respondWith(
    caches.match(event.request).then(cached => {
      return cached || fetch(event.request).then(r => {
        if (r.ok) {
          const clone = r.clone();
          caches.open(STATIC_CACHE).then(c => c.put(event.request, clone)).catch(() => {});
        }
        return r;
      });
    })
  );
});
