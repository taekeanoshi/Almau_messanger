// Service worker — нужен только чтобы сайт считался устанавливаемым приложением (PWA).
// Стратегия "сначала сеть": страница всегда свежая, кэш — только резерв при оффлайне.
// Версию кэша меняем при каждом значимом обновлении, чтобы у всех, кто уже поставил
// приложение, старый кэш (со старым HTML) гарантированно вычистился.
const CACHE = "almau-shell-v2";
const SHELL = ["img/icon-192.png", "img/icon-512.png"];  // только иконки — HTML не кешируем вообще

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
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
