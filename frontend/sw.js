// Kavach service worker: makes the app installable and lets the screens open offline.
// Network first, so a new build always wins; the cache is only a fallback.
// API calls (alerts, evidence, SOS) are never cached.
const CACHE = "kavach-shell-v2";
const SHELL = [
  "/", "/index.html", "/styles.css", "/app.js", "/conversations.js", "/import.js", "/vault.js",
  "/complaint.js", "/guardian.js", "/sos.js", "/nav.js", "/manifest.json", "/icons/icon-192.png",
];
const API = /^\/(analyze|agent|evidence|complaint|legal|sos|people|responders|health)(\/|$)/;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || API.test(url.pathname)) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) caches.open(CACHE).then((c) => c.put(e.request, res.clone()));
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/index.html")))
  );
});
