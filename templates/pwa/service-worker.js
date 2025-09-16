/* templates/pwa/service-worker.js */
const VERSION = "v1.0.0";                 // ← 更新時は上げる
const PRECACHE = `precache-${VERSION}`;
const RUNTIME  = `runtime-${VERSION}`;

const PRECACHE_URLS = [
  "/",                     // ホーム
  "/holdings/",            // 保有一覧（オフライン対応の主役）
  "/manifest.webmanifest",
  "/static/pwa/icon-192.png",
  "/static/pwa/icon-512.png",
  // 必要ならCSS/JSをここに追加
];

// インストール：必須資産を保存
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

// 有効化：古いキャッシュを掃除
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((k) => {
        if (![PRECACHE, RUNTIME].includes(k)) return caches.delete(k);
      }))
    )
  );
  self.clients.claim();
});

// 取ってくる：戦略を分ける
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 同一オリジンのみ制御
  if (url.origin !== self.location.origin) return;

  // 1) ナビゲーション（HTML遷移） → Network-first, fallback offline.html
  if (req.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const net = await fetch(req);
          // 成功ならランタイムキャッシュへ（履歴維持）
          const cache = await caches.open(RUNTIME);
          cache.put(req, net.clone());
          return net;
        } catch {
          // ネット失敗：キャッシュ or オフラインHTML
          const cache = await caches.open(PRECACHE);
          const cached = await caches.match(req);
          return cached || cache.match("/pwa/offline.html") || Response.error();
        }
      })()
    );
    return;
  }

  // 2) 静的ファイル → Cache-first
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(RUNTIME).then((c) => c.put(req, copy));
          return res;
        });
      })
    );
    return;
  }

  // 3) API → Stale-While-Revalidate（最後の結果を即返し、裏で更新）
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      caches.open(RUNTIME).then(async (cache) => {
        const cached = await cache.match(req);
        const networkFetch = fetch(req)
          .then((res) => {
            cache.put(req, res.clone());
            return res;
          })
          .catch(() => null);
        // まずキャッシュがあればそれ、無ければネット
        return cached || networkFetch || Response.error();
      })
    );
    return;
  }

  // 4) その他 → まずキャッシュ、無ければネット
  event.respondWith(
    caches.match(req).then((c) => c || fetch(req))
  );
});

// オフラインHTMLをプリキャッシュに追加（install前でも参照される可能性に備える）
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((c) => c.add("/pwa/offline.html").catch(()=>{}))
  );
});