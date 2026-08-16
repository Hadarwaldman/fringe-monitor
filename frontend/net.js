/**
 * Weak-network JSON loading shared by every page.
 *
 * The data payloads (latest.json is megabytes even gzipped) are the thing
 * that fails on a bad connection, so every load goes cached-copy-first:
 * render whatever we saved last time immediately, then revalidate over the
 * network with retries and update in place. The last good payload lives in
 * the Cache API (localStorage is too small for latest.json).
 */
window.FringeNet = (() => {
  const CACHE_NAME = "fringe-data-v1";
  const CACHED_AT_HEADER = "x-fringe-cached-at";

  async function openCache() {
    if (!("caches" in window)) return null;
    try {
      return await caches.open(CACHE_NAME);
    } catch (_) {
      return null; // private mode / storage denied — network only
    }
  }

  async function readCache(path) {
    const cache = await openCache();
    if (!cache) return null;
    try {
      const res = await cache.match(path);
      if (!res) return null;
      const data = await res.json();
      return { data, cachedAt: res.headers.get(CACHED_AT_HEADER) || "" };
    } catch (_) {
      return null; // corrupt entry — treat as a miss
    }
  }

  async function writeCache(path, text) {
    const cache = await openCache();
    if (!cache) return;
    try {
      await cache.put(
        path,
        new Response(text, {
          headers: {
            "content-type": "application/json",
            [CACHED_AT_HEADER]: new Date().toISOString(),
          },
        }),
      );
    } catch (_) {
      /* quota exceeded — keep going without a saved copy */
    }
  }

  function fetchWithTimeout(path, { timeoutMs = 20000, ...options } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    return fetch(path, { signal: ctrl.signal, ...options }).finally(() =>
      clearTimeout(timer),
    );
  }

  /**
   * GET + parse JSON with retries. No cache-busting query string: the data
   * objects are served with Cache-Control: no-cache, so the browser
   * revalidates with If-None-Match and a 304 costs almost nothing on a slow
   * link. Timeouts are generous because latest.json legitimately takes tens
   * of seconds on 2G — aborting early would kill downloads that were
   * about to succeed.
   */
  async function fetchJson(path, { retries = 2, timeoutMs = 90000 } = {}) {
    let lastErr = null;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        const res = await fetchWithTimeout(path, { timeoutMs });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        const data = JSON.parse(text);
        writeCache(path, text); // fire-and-forget
        return data;
      } catch (err) {
        lastErr = err;
        if (attempt < retries) {
          await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
        }
      }
    }
    throw lastErr;
  }

  /**
   * Cached-first loader. `onData(data, {fromCache, cachedAt})` is called up
   * to twice: immediately with the saved copy (if any), then again with the
   * fresh network payload. Resolves to:
   *   { ok: true,  stale: false }        fresh data delivered
   *   { ok: true,  stale: true, error }  network failed, saved copy shown
   *   { ok: false, error }               nothing to show
   * Never rejects.
   */
  async function loadJson(path, onData, options = {}) {
    let cacheServed = false;
    let cachedAt = "";
    const cached = await readCache(path);
    if (cached) {
      cacheServed = true;
      cachedAt = cached.cachedAt;
      try {
        onData(cached.data, { fromCache: true, cachedAt });
      } catch (_) {
        /* render error on stale data must not block the fresh load */
      }
    }
    try {
      const data = await fetchJson(path, options);
      onData(data, { fromCache: false, cachedAt: "" });
      return { ok: true, stale: false };
    } catch (error) {
      return { ok: cacheServed, stale: cacheServed, cachedAt, error };
    }
  }

  return { loadJson, fetchJson, fetchWithTimeout, readCache };
})();
