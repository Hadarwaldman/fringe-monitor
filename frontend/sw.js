/**
 * Service worker: keeps the app shell (HTML/JS/CSS) usable offline and
 * instant on weak connections via stale-while-revalidate. /data/* is
 * deliberately NOT handled here — net.js owns data caching at page level so
 * the pages can tell the user when they're looking at a saved copy.
 *
 * Bump VERSION when the shell list changes shape; individual file updates
 * flow through revalidation on their own.
 */
const VERSION = "v1";
const SHELL_CACHE = `fringe-shell-${VERSION}`;
const SHELL = [
  "./",
  "./index.html",
  "./shows.html",
  "./show.html",
  "./monitors.html",
  "./settings.html",
  "./styles.css",
  "./config.js",
  "./net.js",
  "./ui.js",
  "./app.js",
  "./monitors.js",
  "./settings.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith("fringe-shell-") && k !== SHELL_CACHE)
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request, { ignoreSearch: true });
  const refresh = fetch(request)
    .then((res) => {
      if (res && res.ok) cache.put(request, res.clone());
      return res;
    })
    .catch(() => null);
  if (cached) return cached;
  const fresh = await refresh;
  if (fresh) return fresh;
  // Offline navigation to a page we never cached — fall back to the index.
  if (request.mode === "navigate") {
    const index = await cache.match("./index.html");
    if (index) return index;
  }
  return Response.error();
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/data/")) return; // net.js territory
  event.respondWith(staleWhileRevalidate(request));
});
