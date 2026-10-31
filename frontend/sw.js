// Kavach service worker: makes the app installable and lets the screens open offline.
// Network first, so a new build always wins; the cache is only a fallback.
// API calls (alerts, evidence, SOS) are never cached.
const CACHE = "kavach-shell-v5";
const SHELL = [
  "/", "/index.html", "/css/styles.css", "/js/app.js", "/js/conversations.js", "/js/import.js", "/js/vault.js",
  "/js/complaint.js", "/js/guardian.js", "/js/sos.js", "/js/nav.js", "/js/dialog.js", "/login.html", "/manifest.json", "/assets/icons/icon-192.png",
];
const API = /^\/(analyze|agent|evidence|complaint|legal|sos|people|responders|health|auth|reset)(\/|$)/;

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
