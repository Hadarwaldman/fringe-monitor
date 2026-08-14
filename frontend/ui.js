/**
 * Shared UI + storage helpers used by every page.
 *
 * Renders the site header (desktop nav) and the fixed bottom tab bar
 * (mobile) into #site-nav, based on <body data-nav="...">, and owns the
 * localStorage conventions (active user, per-user date windows) so app.js
 * and settings.js stay in sync.
 */
window.FringeUI = (() => {
  const USERS = [
    { id: "hadar", name: "Hadar" },
    { id: "adi", name: "Adi" },
  ];
  const ACTIVE_USER_KEY = "fringe-monitor.activeUser";
  const DEFAULT_WINDOW = {
    start_date: "2026-08-12",
    end_date: "2026-08-20",
    nearly_threshold: 20,
  };

  function activeUserId() {
    try {
      const saved = localStorage.getItem(ACTIVE_USER_KEY);
      if (USERS.some((u) => u.id === saved)) return saved;
    } catch (_) {
      /* ignore */
    }
    return USERS[0].id;
  }

  function setActiveUserId(userId) {
    if (!USERS.some((u) => u.id === userId)) return;
    try {
      localStorage.setItem(ACTIVE_USER_KEY, userId);
    } catch (_) {
      /* ignore */
    }
  }

  function userName(userId) {
    const user = USERS.find((u) => u.id === userId);
    return user ? user.name : userId;
  }

  function windowKey(userId) {
    return `fringe-monitor.dateWindow.${userId}`;
  }

  function readSavedDateWindow(userId) {
    try {
      const raw = localStorage.getItem(windowKey(userId));
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || !data.start_date || !data.end_date) return null;
      return {
        start_date: data.start_date,
        end_date: data.end_date,
        nearly_threshold: Number(
          data.nearly_threshold ?? DEFAULT_WINDOW.nearly_threshold,
        ),
      };
    } catch (_) {
      return null;
    }
  }

  /** The user's window, falling back to the server config, then defaults. */
  function readDateWindow(userId, config) {
    const saved = readSavedDateWindow(userId);
    if (saved) return saved;
    return {
      start_date: (config && config.start_date) || DEFAULT_WINDOW.start_date,
      end_date: (config && config.end_date) || DEFAULT_WINDOW.end_date,
      nearly_threshold:
        (config && config.nearly_threshold) ?? DEFAULT_WINDOW.nearly_threshold,
    };
  }

  function saveDateWindow(userId, window_) {
    try {
      localStorage.setItem(windowKey(userId), JSON.stringify(window_));
    } catch (_) {
      /* ignore */
    }
  }

  /** Union of both users' windows — what the daily scan should cover. */
  function unionScanWindow() {
    const windows = USERS.map((u) => readSavedDateWindow(u.id)).filter(Boolean);
    if (!windows.length) return { ...DEFAULT_WINDOW };
    const starts = windows.map((w) => w.start_date).sort();
    const ends = windows.map((w) => w.end_date).sort();
    const thresholds = windows
      .map((w) => Number(w.nearly_threshold))
      .filter((n) => Number.isFinite(n));
    return {
      start_date: starts[0],
      end_date: ends[ends.length - 1],
      nearly_threshold: thresholds.length
        ? Math.min(...thresholds)
        : DEFAULT_WINDOW.nearly_threshold,
    };
  }

  /** EdFest-style slug from a show title (best-effort fallback for links). */
  function slugifyTitle(title) {
    return String(title || "")
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  const NAV_ITEMS = [
    {
      key: "my",
      label: "My Fringe",
      href: "./index.html",
      icon:
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>',
    },
    {
      key: "shows",
      label: "Shows",
      href: "./shows.html",
      icon:
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
    },
    {
      key: "monitors",
      label: "Monitors",
      href: "./monitors.html",
      icon:
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 19a2 2 0 0 0 4 0"/></svg>',
    },
    {
      key: "settings",
      label: "Settings",
      href: "./settings.html",
      icon:
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.09a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.09a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z"/></svg>',
    },
  ];

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderNav(activeKey) {
    const host = document.getElementById("site-nav");
    if (!host) return;
    const user = userName(activeUserId());
    const links = NAV_ITEMS.map(
      (item) =>
        `<a class="nav-link${item.key === activeKey ? " active" : ""}" href="${item.href}"${
          item.key === activeKey ? ' aria-current="page"' : ""
        }>${item.label}</a>`,
    ).join("");
    const tabs = NAV_ITEMS.map(
      (item) =>
        `<a class="tab${item.key === activeKey ? " active" : ""}" href="${item.href}"${
          item.key === activeKey ? ' aria-current="page"' : ""
        }>${item.icon}<span>${item.label}</span></a>`,
    ).join("");
    host.innerHTML = `
      <header class="site-header">
        <a class="site-brand" href="./index.html">Fringe Monitor</a>
        <nav class="site-links" aria-label="Main">${links}</nav>
        <a class="user-chip" href="./settings.html" title="Active user — change in Settings">${escapeHtml(user)}</a>
      </header>
      <nav class="tabbar" aria-label="Main">${tabs}</nav>`;
    document.body.classList.add("has-tabbar");
  }

  document.addEventListener("DOMContentLoaded", () => {
    renderNav(document.body.dataset.nav || "");
  });

  return {
    USERS,
    DEFAULT_WINDOW,
    activeUserId,
    setActiveUserId,
    userName,
    readSavedDateWindow,
    readDateWindow,
    saveDateWindow,
    unionScanWindow,
    slugifyTitle,
    renderNav,
  };
})();
