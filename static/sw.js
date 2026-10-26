// Service Worker Tông Môn Tu Luyện (phục vụ tại /sw.js để có phạm vi toàn trang)
// - Nhạc nền (.mp3): cache-first để phát mượt, đỡ tắc và nghe lại không tốn tải.
// - Tài nguyên tĩnh khác: stale-while-revalidate.
// - Trang HTML & API: luôn lấy mạng (dữ liệu game phải tươi), lỗi mạng mới báo offline.
const CACHE = 'tongmon-v4';
const CORE = ['/static/icon-192.png', '/static/icon-512.png', '/static/manifest.json'];

self.addEventListener('install', (e) => {
    e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (e) => {
    const url = new URL(e.request.url);
    if (e.request.method !== 'GET') return;

    // Nhạc nền: cache-first
    if (url.pathname.startsWith('/static/music/') && url.pathname.endsWith('.mp3')) {
        e.respondWith(
            caches.match(e.request).then((hit) => {
                if (hit) return hit;
                return fetch(e.request).then((res) => {
                    const copy = res.clone();
                    caches.open(CACHE).then((c) => c.put(e.request, copy));
                    return res;
                });
            })
        );
        return;
    }

    // Tĩnh (ảnh/css/js/font): stale-while-revalidate
    if (url.pathname.startsWith('/static/')) {
        e.respondWith(
            caches.match(e.request).then((hit) => {
                const net = fetch(e.request).then((res) => {
                    const copy = res.clone();
                    caches.open(CACHE).then((c) => c.put(e.request, copy));
                    return res;
                }).catch(() => hit);
                return hit || net;
            })
        );
        return;
    }

    // Còn lại (trang, API): network-first thuần
    e.respondWith(fetch(e.request));
});
