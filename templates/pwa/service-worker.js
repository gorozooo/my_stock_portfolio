// templates/pwa/service-worker.js
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open('mystock-v1').then(cache => {
    return cache.addAll([
      '/', '/manifest.webmanifest',
      // 必要なら主要CSS/JSやロゴを追加
    ]);
  }));
});

self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then(x => x || fetch(event.request))
  );
});