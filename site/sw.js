// Минимальный service worker — только чтобы сайт считался PWA и был устанавливаемым.
// API-запросы (другой домен) не кешируются и всегда идут в сеть.
const CACHE = "almau-shell-v1";
const SHELL = [
  "./",
  "./index.html",
  "./manifest.json",
  "./img/logo.png",
  "./img/icon-192.png",
  "./img/icon-512.png",
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;  // не трогаем API на другом домене
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
