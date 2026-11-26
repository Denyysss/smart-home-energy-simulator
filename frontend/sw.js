/**
 * sw.js — Service Worker для PWA-додатку ЕнергіяДім (Network-First з офлайн-кешем).
 * Завжди завантажує найсвіжіший код при наявності мережі та підтримує встановлення на iOS/Android.
 */

const CACHE_NAME = "energydim-shell-v1";
const PRECACHE_URLS = [
    "/",
    "/index.html",
    "/favicon.svg",
    "/manifest.webmanifest",
    "/css/base.css",
    "/css/components.css",
    "/css/themes.css",
    "/js/app.js",
    "/js/api.js",
    "/js/auth.js",
    "/js/modals.js",
    "/js/charts.js",
    "/js/optimizer.js",
    "/js/gpv.js",
    "/js/pwa.js",
];

self.addEventListener("install", event => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE_URLS)).catch(() => {})
    );
});

self.addEventListener("activate", event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", event => {
    const req = event.request;
    if (req.method !== "GET") return;
    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname.startsWith("/api/") || url.pathname.endsWith(".mp4")) return;

    event.respondWith(
        fetch(req)
            .then(resp => {
                if (resp && resp.status === 200) {
                    const clone = resp.clone();
                    caches.open(CACHE_NAME).then(c => c.put(req, clone)).catch(() => {});
                }
                return resp;
            })
            .catch(() => caches.match(req).then(cached => cached || caches.match("/index.html")))
    );
});
