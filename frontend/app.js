/**
 * Shared frontend logic for the three data-driven pages, selected by
 * <body data-page="...">:
 *   "my"    — index.html, the My Fringe itinerary (schedule + bookings + wishlist)
 *   "shows" — shows.html, the full programme browser
 *   "show"  — show.html, a single show's detail page (?slug= or ?title=)
 *
 * Depends on ui.js (window.FringeUI) for nav + user/date-window storage.
 */
(() => {
  const apiBase = (window.FRINGE_CONFIG && window.FRINGE_CONFIG.apiUrl) || "";
  const UI = window.FringeUI;
  const page = document.body.dataset.page || "";

  const LEGACY_SCHEDULE_KEY = "fringe-monitor.scheduleCsv";

  const state = {
    shows: [],
    config: null,
    details: null,
    scheduleRows: [],
    scheduleFileName: "",
    bookings: [],
    userId: UI.activeUserId(),
    planner: null,
    view: { start: "", end: "" },
    itinFilter: "all",
    showsLimit: 100,
    loaded: false,
    latestPayload: null,
    latestStale: false,
  };

  const COMMON_DEALS = [
    { code: "FRIENDS_TWO_FOR_ONE", label: "Fringe Friends (1+1)", slug: "fringe-friends" },
    { code: "LoveTheFringe", label: "Love the Fringe", slug: "love-the-fringe" },
    { code: "EdFest241", label: "2 for 1", slug: "2for1" },
    { code: "20OFF", label: "20% OFF", slug: "20-OFF" },
    { code: "EdFest5", label: "£5 Ticket", slug: "5-ticket" },
    { code: "EdFest8", label: "£8 Ticket", slug: "8-ticket" },
    { code: "EdFest50off", label: "50% OFF", slug: "50-off" },
    { code: "PAY_WHAT_YOU_WANT", label: "Pay what you want", slug: "pay-what-you-want" },
    { code: "CONCESSION", label: "Concession", slug: "concession" },
    { code: "TRAVERSE", label: "Traverse", slug: "traverse" },
    { code: "NONE", label: "No deal / full price", slug: "none" },
  ];

  const DEAL_BY_STRATEGY = {
    concesion: { code: "CONCESSION", label: "Concession", slug: "concession" },
    concession: { code: "CONCESSION", label: "Concession", slug: "concession" },
    love: { code: "LoveTheFringe", label: "Love the Fringe", slug: "love-the-fringe" },
    traverse: { code: "TRAVERSE", label: "Traverse", slug: "traverse" },
    normal: null,
  };

  /** Initial purchases (Hadar). Merged once per show+date if missing in localStorage. */
  const SEED_BOOKINGS = {
    hadar: [
      { date: "2026-08-15", showTitle: "One Man Musical", strategy: "Concesion", price: 24 },
      { date: "2026-08-17", showTitle: "Bliss", strategy: "Love", price: 0 },
      { date: "2026-08-16", showTitle: "Remember, Remember!", strategy: "Concesion", price: 16 },
      { date: "2026-08-14", showTitle: "The Shocking Truth About Flat Earth", strategy: "Concesion", price: 19 },
      { date: "2026-08-14", showTitle: "Penelope", strategy: "Love", price: 0 },
      { date: "2026-08-17", showTitle: "Gianmarco Soresi: Theater Adult", strategy: "Concesion", price: 15 },
      { date: "2026-08-14", showTitle: "Bigfoot Ripped My Dog In Half I Saw It", strategy: "Normal", price: 16 },
      { date: "2026-08-18", showTitle: "Supposing:", strategy: "Traverse", price: 20.5 },
      { date: "2026-08-18", showTitle: "For Dolores", strategy: "Traverse", price: 20.5 },
      { date: "2026-08-18", showTitle: "Badgers", strategy: "Traverse", price: 20.5 },
      { date: "2026-08-18", showTitle: "The Singer", strategy: "Traverse", price: 22.5 },
      { date: "2026-08-15", showTitle: "alone", strategy: "Normal", price: 14 },
      { date: "2026-08-16", showTitle: "Amanda Knox: Cartwheel", strategy: "Normal", price: 14 },
      { date: "2026-08-14", showTitle: "EVITA TOO", strategy: "Normal", price: 15 },
    ],
  };

  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------- storage

  function scheduleKey(userId) {
    return `fringe-monitor.scheduleCsv.${userId}`;
  }

  function bookingsKey(userId) {
    return `fringe-monitor.bookings.${userId}`;
  }

  function migrateLegacyScheduleIfNeeded(userId) {
    if (userId !== "hadar") return;
    try {
      if (localStorage.getItem(scheduleKey("hadar"))) return;
      const legacy = localStorage.getItem(LEGACY_SCHEDULE_KEY);
      if (!legacy) return;
      localStorage.setItem(scheduleKey("hadar"), legacy);
      localStorage.removeItem(LEGACY_SCHEDULE_KEY);
    } catch (_) {
      /* ignore */
    }
  }

  function saveSchedule() {
    try {
      localStorage.setItem(
        scheduleKey(state.userId),
        JSON.stringify({
          fileName: state.scheduleFileName || "",
          rows: state.scheduleRows,
        }),
      );
    } catch (_) {
      /* quota / private mode — keep in-memory only */
    }
  }

  function loadSavedSchedule() {
    state.scheduleRows = [];
    state.scheduleFileName = "";
    migrateLegacyScheduleIfNeeded(state.userId);
    try {
      const raw = localStorage.getItem(scheduleKey(state.userId));
      if (!raw) return;
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.rows) || !data.rows.length) return;
      state.scheduleRows = data.rows;
      state.scheduleFileName = data.fileName || "";
    } catch (_) {
      /* ignore corrupt storage */
    }
  }

  function saveBookings() {
    try {
      localStorage.setItem(bookingsKey(state.userId), JSON.stringify(state.bookings));
    } catch (_) {
      /* quota / private mode — keep in-memory only */
    }
  }

  function loadSavedBookings() {
    state.bookings = [];
    try {
      const raw = localStorage.getItem(bookingsKey(state.userId));
      if (!raw) return;
      const data = JSON.parse(raw);
      if (!Array.isArray(data)) return;
      state.bookings = data
        .filter((b) => b && b.showTitle && b.date)
        .map((b) => ({
          id: String(b.id || `${b.date}-${b.showTitle}`),
          showTitle: String(b.showTitle),
          slug: b.slug || null,
          date: String(b.date),
          price: Number(b.price),
          deals: Array.isArray(b.deals) ? b.deals : [],
          notes: b.notes ? String(b.notes) : "",
          url: b.url || null,
        }));
    } catch (_) {
      /* ignore corrupt storage */
    }
  }

  // ------------------------------------------------------------- bookings

  function dealsFromStrategy(strategy) {
    const key = String(strategy || "")
      .trim()
      .toLowerCase();
    const deal = DEAL_BY_STRATEGY[key];
    return deal ? [deal] : [];
  }

  function bookingMatchKey(showTitle, date) {
    return `${normalizeTitle(showTitle)}|${date}`;
  }

  function mergeSeedBookings() {
    const seeds = SEED_BOOKINGS[state.userId] || [];
    if (!seeds.length) return;
    const existing = new Set(
      state.bookings.map((b) => bookingMatchKey(b.showTitle, b.date)),
    );
    let added = 0;
    for (const seed of seeds) {
      const key = bookingMatchKey(seed.showTitle, seed.date);
      if (existing.has(key)) continue;
      const show = findShow(seed.showTitle);
      state.bookings.push({
        id: newBookingId(),
        showTitle: show?.show_title || seed.showTitle,
        slug: show?.slug || null,
        date: seed.date,
        price: Number(seed.price),
        deals: dealsFromStrategy(seed.strategy),
        notes: seed.comment || "",
        url: show?.url || null,
      });
      existing.add(key);
      added += 1;
    }
    if (!added) return;
    state.bookings.sort(
      (a, b) =>
        String(a.date).localeCompare(String(b.date)) ||
        String(a.showTitle).localeCompare(String(b.showTitle)),
    );
    saveBookings();
  }

  function newBookingId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return `b-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function formatPrice(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `£${n.toFixed(n % 1 === 0 ? 0 : 2)}`;
  }

  function bookingsForShow(show) {
    if (!show) return [];
    const key = normalizeTitle(show.show_title);
    const slug = show.slug || "";
    return state.bookings.filter(
      (b) =>
        (slug && b.slug === slug) || normalizeTitle(b.showTitle) === key,
    );
  }

  function isShowBooked(show) {
    return bookingsForShow(show).length > 0;
  }

  function bookingFor(title, show, date) {
    if (!date) return null;
    const key = normalizeTitle(title);
    const slug = show?.slug || "";
    return (
      state.bookings.find(
        (b) =>
          b.date === date &&
          ((slug && b.slug === slug) || normalizeTitle(b.showTitle) === key),
      ) || null
    );
  }

  // ------------------------------------------------------------- offers

  function offerKey(offer) {
    return offer?.code || offer?.slug || offer?.label || "";
  }

  function mergeOffers(...lists) {
    const seen = new Map();
    for (const list of lists) {
      for (const offer of list || []) {
        const key = offerKey(offer);
        if (!key || seen.has(key)) continue;
        seen.set(key, {
          code: offer.code || key,
          label: offer.label || offer.code || key,
          slug: offer.slug || "",
        });
      }
    }
    return [...seen.values()];
  }

  // ------------------------------------------------------------- text helpers

  function normalizeTitle(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[“”"']/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function parsePlanDate(raw) {
    // Examples: "2026-08-14" (synced), "Fri 14/08", "Sat 15/08" (uploads)
    const iso = String(raw || "").match(/\d{4}-\d{2}-\d{2}/);
    if (iso) return iso[0];
    const m = String(raw || "").match(/(\d{1,2})\/(\d{1,2})/);
    if (!m) return null;
    const day = m[1].padStart(2, "0");
    const month = m[2].padStart(2, "0");
    return `2026-${month}-${day}`;
  }

  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function shortDate(dateStr) {
    // 2026-08-13 → 13 Aug
    const m = String(dateStr || "").match(/^\d{4}-(\d{2})-(\d{2})$/);
    if (!m) return dateStr || "";
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${Number(m[2])} ${months[Number(m[1]) - 1]}`;
  }

  function dayHeading(dateStr) {
    const m = String(dateStr || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return dateStr || "No date";
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    return `${days[d.getDay()]} ${shortDate(dateStr)}`;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("'", "&#39;");
  }

  // ------------------------------------------------------------- planner (PlanMyFringe)

  function wishlistEntries() {
    return (state.planner && state.planner.wishlist) || [];
  }

  function wishlistScoreFor(title) {
    const key = normalizeTitle(title);
    if (!key) return null;
    const entry = wishlistEntries().find(
      (w) =>
        normalizeTitle(w.matched_show_title || w.title) === key ||
        normalizeTitle(w.title) === key,
    );
    return entry && entry.score != null ? entry.score : null;
  }

  function scoreChip(title) {
    const score = wishlistScoreFor(title);
    if (score == null) return "";
    return ` <span class="pill score" title="Your PlanMyFringe score">★ ${escapeHtml(score)}</span>`;
  }

  function plannerScheduleRows(planner) {
    return (planner.schedule || []).map((e) => ({
      Date: e.date,
      Name: e.title,
      Venue: e.venue || "",
      Time: e.time || "",
      __confirmed: !!e.confirmed,
      __past: !!e.past,
    }));
  }

  function isPastRow(row) {
    if (row.__past != null) return !!row.__past;
    const date = parsePlanDate(row.Date || row.date);
    if (!date) return false;
    const today = todayStr();
    if (date !== today) return date < today;
    const time = row.Time || row.time || "";
    const d = new Date();
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return !!time && time <= hm;
  }

  function adoptPlannerSchedule() {
    if (!state.planner || !(state.planner.schedule || []).length) return;
    state.scheduleRows = plannerScheduleRows(state.planner);
    state.scheduleFileName = `PlanMyFringe sync (${(state.planner.synced_at || "").slice(0, 16)})`;
    saveSchedule();
  }

  async function loadPlanner() {
    await FringeNet.loadJson("/data/planner.json", (data) => {
      state.planner = data;
      if (!state.scheduleRows.length) adoptPlannerSchedule();
      renderAll();
    });
    /* failure is fine — no planner synced yet, or offline */
  }

  function setSyncStatus(message) {
    const el = $("sync-status");
    if (el) el.textContent = message || "";
  }

  async function syncPlanner() {
    if (!apiBase) {
      setSyncStatus("API URL missing — cannot sync.");
      return;
    }
    const btn = $("sync-planner-btn");
    if (btn) btn.disabled = true;
    setSyncStatus("Syncing from PlanMyFringe…");
    try {
      const res = await fetch(`${apiBase}/planner/sync`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      state.planner = data.planner || null;
      adoptPlannerSchedule();
      renderAll();
      const s = data.summary || {};
      setSyncStatus(
        `Synced: ${s.schedule_entries || 0} scheduled (${s.confirmed_booked || 0} already booked), ` +
          `${s.watchlist_imported || 0} added to watchlist, ${s.wishlist_entries || 0} wishlist shows.`,
      );
    } catch (err) {
      setSyncStatus(`Sync failed: ${err.message || err}`);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ------------------------------------------------------------- view window

  function inView(dateStr) {
    if (state.view.start && dateStr < state.view.start) return false;
    if (state.view.end && dateStr > state.view.end) return false;
    return true;
  }

  function filterDates(dates) {
    return (dates || []).filter(inView);
  }

  // ------------------------------------------------------------- shows

  function pill(status) {
    const label =
      status === "sold_out"
        ? "sold out"
        : status === "nearly_sold_out"
          ? "nearly"
          : status === "available"
            ? "available"
            : status || "unknown";
    return `<span class="pill ${status || "unknown"}">${label}</span>`;
  }

  function findShow(name) {
    const key = normalizeTitle(name);
    if (!key) return null;
    return (
      state.shows.find((s) => normalizeTitle(s.show_title) === key) ||
      state.shows.find((s) => {
        const t = normalizeTitle(s.show_title);
        return t.includes(key) || key.includes(t);
      }) ||
      null
    );
  }

  function findShowBySlug(slug) {
    if (!slug) return null;
    return state.shows.find((s) => s.slug === slug) || null;
  }

  function showHref(show, title) {
    if (show?.slug) return `./show.html?slug=${encodeURIComponent(show.slug)}`;
    return `./show.html?title=${encodeURIComponent(title || show?.show_title || "")}`;
  }

  function titleHtml(show, title) {
    const text = escapeHtml(title || show?.show_title || "");
    if (!show) return `<strong>${text}</strong>`;
    return `<a class="show-title-link" href="${escapeAttr(showHref(show, title))}">${text}</a>`;
  }

  function statusForDay(show, dateStr) {
    if (!show) return "unknown";
    if ((show.sold_out_dates || []).includes(dateStr)) return "sold_out";
    if ((show.nearly_sold_out_dates || []).includes(dateStr)) return "nearly_sold_out";
    if ((show.available_dates || []).includes(dateStr)) return "available";
    const perf = (show.performances || []).find((p) => p.date === dateStr);
    return perf ? perf.availability : "unknown";
  }

  // ------------------------------------------------------------- offers per day

  const OFFER_SHORT = {
    LoveTheFringe: "LTF",
    FRIENDS_TWO_FOR_ONE: "Friends",
    EdFest241: "2f1",
    "20OFF": "20%",
    EdFest5: "£5",
    EdFest8: "£8",
    EdFest50off: "50%",
    TIMESGIVEAWAY: "Times",
    TWO_FOR_ONE: "2f1",
    PAY_WHAT_YOU_WANT: "PWYW",
    GROUP_DISCOUNTS: "Group",
    CONCESSION: "Conc",
    TRAVERSE: "Trav",
  };

  function offerShort(offer) {
    if (!offer) return "";
    const code = offer.code || "";
    if (OFFER_SHORT[code]) return OFFER_SHORT[code];
    const label = offer.label || code || "";
    if (/fringe\s*friends/i.test(label)) return "Friends";
    if (/love\s*the\s*fringe/i.test(label)) return "LTF";
    if (/2\s*for\s*1/i.test(label)) return "2f1";
    return label.length > 8 ? `${label.slice(0, 7)}…` : label;
  }

  function offersForPerf(perf) {
    return Array.isArray(perf?.offers) ? perf.offers : [];
  }

  function offersForShowDay(show, dateStr) {
    if (!show || !dateStr) return [];
    const fromMap = show.offer_dates && show.offer_dates[dateStr];
    if (Array.isArray(fromMap) && fromMap.length) return fromMap;
    const seen = new Map();
    for (const p of show.performances || []) {
      if (p.date !== dateStr) continue;
      for (const offer of offersForPerf(p)) {
        const key = offer.code || offer.label;
        if (key && !seen.has(key)) seen.set(key, offer);
      }
    }
    return [...seen.values()];
  }

  function showHasOfferFilter(show, offerFilter) {
    if (!offerFilter || offerFilter === "all") return true;
    const offerDates = show.offer_dates || {};
    const days = Object.keys(offerDates).filter((d) => inView(d));
    const offers = [];
    for (const day of days) {
      for (const offer of offerDates[day] || []) offers.push(offer);
    }
    // Also check performances in case offer_dates missing on older payloads.
    if (!offers.length) {
      for (const p of show.performances || []) {
        if (!inView(p.date)) continue;
        offers.push(...offersForPerf(p));
      }
    }
    if (offerFilter === "any") return offers.length > 0;
    return offers.some(
      (o) =>
        o.slug === offerFilter ||
        (offerFilter === "fringe-friends" &&
          (o.code === "FRIENDS_TWO_FOR_ONE" ||
            o.slug === "fringe-friends" ||
            /fringe\s*friends/i.test(o.label || ""))) ||
        (offerFilter === "love-the-fringe" &&
          (o.code === "LoveTheFringe" || /love\s*the\s*fringe/i.test(o.label || ""))) ||
        (offerFilter === "2for1" &&
          (o.code === "EdFest241" ||
            o.code === "TWO_FOR_ONE" ||
            (/2\s*for\s*1/i.test(o.label || "") && !/friends/i.test(o.label || "")))),
    );
  }

  // ------------------------------------------------------------- availability chips

  const REM_STATUS_COLORS = {
    sold_out: { bg: [245, 215, 219], fg: [139, 36, 48] },
    nearly_sold_out: { bg: [243, 226, 200], fg: [138, 75, 18] },
    available: { bg: [215, 239, 233], fg: [31, 107, 58] },
    unknown: { bg: [236, 231, 220], fg: [92, 86, 76] },
  };

  function formatRemaining(perf) {
    if (!perf) return "—";
    if (perf.availability === "sold_out") return "0%";
    if (perf.percent_remaining == null || perf.percent_remaining === "") return "—";
    return `${perf.percent_remaining}%`;
  }

  function remainingPercent(perf) {
    if (!perf) return null;
    if (perf.availability === "sold_out") return 0;
    if (perf.percent_remaining == null || perf.percent_remaining === "") return null;
    const n = Number(perf.percent_remaining);
    return Number.isFinite(n) ? n : null;
  }

  /** Over 75% sold ⇒ remaining capacity ≤ 25% (and not fully sold out). */
  const NEARLY_SOLD_REMAINING_MAX = 25;

  function showHasNearlySoldOut(show) {
    return (show.performances || []).some((p) => {
      if (!inView(p.date)) return false;
      const rem = remainingPercent(p);
      return rem != null && rem > 0 && rem <= NEARLY_SOLD_REMAINING_MAX;
    });
  }

  function averageRemaining(perfs) {
    const values = (perfs || []).map(remainingPercent).filter((n) => n != null);
    if (!values.length) return null;
    return values.reduce((sum, n) => sum + n, 0) / values.length;
  }

  function formatAvgRemaining(avg) {
    if (avg == null) return "—";
    return `${Math.round(avg)}%`;
  }

  function rgbCss(rgb) {
    return `rgb(${rgb.map((n) => Math.round(n)).join(", ")})`;
  }

  function averageRemColors(perfs) {
    const colors = (perfs || []).map(
      (p) => REM_STATUS_COLORS[p.availability] || REM_STATUS_COLORS.unknown,
    );
    if (!colors.length) return REM_STATUS_COLORS.unknown;
    const avg = (key) => {
      const sum = [0, 0, 0];
      for (const c of colors) {
        sum[0] += c[key][0];
        sum[1] += c[key][1];
        sum[2] += c[key][2];
      }
      return sum.map((n) => n / colors.length);
    };
    return { bg: avg("bg"), fg: avg("fg") };
  }

  function remChipHtml({ status, label, tip, style }) {
    const cls = ["rem", status || ""].filter(Boolean).join(" ");
    const styleAttr = style ? ` style="${escapeAttr(style)}"` : "";
    return `<span class="${cls}" title="${escapeAttr(tip || "")}"${styleAttr}>${label}</span>`;
  }

  function formatPerfTime(time) {
    const t = String(time || "").trim();
    if (!t) return "";
    // 19:30:00 → 19:30
    const m = t.match(/^(\d{1,2}:\d{2})/);
    return m ? m[1] : t;
  }

  function perfRemChipHtml(show, p, { showDate = false } = {}) {
    const status = p.availability || "unknown";
    const rem = formatRemaining(p);
    const time = formatPerfTime(p.time);
    const tip = [p.date, time || p.time || ""].filter(Boolean).join(" · ");
    const head = showDate
      ? `${escapeHtml(shortDate(p.date))}${time ? ` ${escapeHtml(time)}` : ""} ${escapeHtml(rem)}`
      : `${escapeHtml(time || "—")} ${escapeHtml(rem)}`;
    return remChipHtml({ status, label: head, tip });
  }

  function remainingForDay(show, dateStr) {
    if (!show || !dateStr) return "—";
    const perfs = (show.performances || []).filter((p) => p.date === dateStr);
    if (!perfs.length) return "—";
    if (perfs.length === 1) return formatRemaining(perfs[0]);
    return formatAvgRemaining(averageRemaining(perfs));
  }

  function remainingByDayHtml(show) {
    const perfs = (show.performances || [])
      .filter((p) => inView(p.date))
      .slice()
      .sort(
        (a, b) =>
          String(a.date).localeCompare(String(b.date)) ||
          String(a.time).localeCompare(String(b.time)),
      );
    if (!perfs.length) return "—";

    const byDate = new Map();
    for (const p of perfs) {
      if (!byDate.has(p.date)) byDate.set(p.date, []);
      byDate.get(p.date).push(p);
    }

    return [...byDate.entries()]
      .map(([date, dayPerfs]) => {
        if (dayPerfs.length === 1) {
          return perfRemChipHtml(show, dayPerfs[0], { showDate: true });
        }

        const avg = averageRemaining(dayPerfs);
        const rem = formatAvgRemaining(avg);
        const colors = averageRemColors(dayPerfs);
        const tip = [date, `${dayPerfs.length} performances`, `avg ${rem}`]
          .filter(Boolean)
          .join(" · ");
        const uniqueTimes = [
          ...new Set(dayPerfs.map((p) => formatPerfTime(p.time)).filter(Boolean)),
        ];
        const timeLabel =
          uniqueTimes.length === 1 ? uniqueTimes[0] : "multiple";
        const style = `background:${rgbCss(colors.bg)};color:${rgbCss(colors.fg)}`;
        const kids = dayPerfs
          .map((p) => perfRemChipHtml(show, p))
          .join("");
        return `<details class="rem-group">
          <summary class="rem rem-avg" title="${escapeAttr(tip)}" style="${escapeAttr(style)}">${escapeHtml(shortDate(date))} ${escapeHtml(timeLabel)} ${escapeHtml(rem)}<span class="rem-count">×${dayPerfs.length}</span></summary>
          <div class="rem-perfs">${kids}</div>
        </details>`;
      })
      .join(" ");
  }

  function dealsByDayHtml(show) {
    const dates = new Set();
    for (const d of Object.keys(show.offer_dates || {})) {
      if (inView(d)) dates.add(d);
    }
    for (const p of show.performances || []) {
      if (inView(p.date) && offersForPerf(p).length) dates.add(p.date);
    }
    const sorted = [...dates].sort();
    if (!sorted.length) return "—";
    return sorted
      .map((date) => {
        const offers = offersForShowDay(show, date);
        const bits = offers.map(offerShort).filter(Boolean);
        if (!bits.length) return "";
        const tip = offers
          .map((o) => o.label || o.code)
          .filter(Boolean)
          .join(", ");
        return `<span class="deal" title="${escapeAttr(`${date} · ${tip}`)}"><span class="deal-date">${escapeHtml(shortDate(date))}</span> ${bits
          .map((b) => `<span class="deal-tag">${escapeHtml(b)}</span>`)
          .join("")}</span>`;
      })
      .filter(Boolean)
      .join(" ");
  }

  function dealsForDayHtml(show, dateStr) {
    const offers = offersForShowDay(show, dateStr);
    const bits = offers.map(offerShort).filter(Boolean);
    if (!bits.length) return "";
    const tip = offers
      .map((o) => o.label || o.code)
      .filter(Boolean)
      .join(", ");
    return `<span class="deal" title="${escapeAttr(tip)}">${bits
      .map((b) => `<span class="deal-tag">${escapeHtml(b)}</span>`)
      .join("")}</span>`;
  }

  function dealsListHtml(deals) {
    const bits = (deals || []).map(offerShort).filter(Boolean);
    if (!bits.length) return "";
    const tip = (deals || [])
      .map((o) => o.label || o.code)
      .filter(Boolean)
      .join(", ");
    return `<span class="deal" title="${escapeAttr(tip)}">${bits
      .map((b) => `<span class="deal-tag">${escapeHtml(b)}</span>`)
      .join("")}</span>`;
  }

  // ------------------------------------------------------------- trend

  function sparklineSvg(series) {
    const values = (series || []).map(Number).filter((n) => Number.isFinite(n));
    if (values.length < 2) return "";
    const w = 56;
    const h = 18;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const pts = values
      .map((v, i) => {
        const x = (i / (values.length - 1)) * w;
        const y = h - ((v - min) / span) * (h - 2) - 1;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return `<svg class="trend-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><polyline fill="none" stroke="currentColor" stroke-width="1.5" points="${pts}" /></svg>`;
  }

  function trendHtml(show) {
    const trend = show.trend || {};
    const avg = trend.avg_daily_sold_pct;
    if (avg == null || avg === "") {
      return `<span class="trend muted" title="Need at least two daily scans">—</span>`;
    }
    const n = Number(avg);
    const sign = n > 0 ? "+" : "";
    const cls = n > 0.05 ? "up" : n < -0.05 ? "down" : "flat";
    const intervals = trend.sample_intervals || 0;
    const series = trend.sold_pct_series || [];
    const tipParts = [
      `Avg ${sign}${n.toFixed(1)}% sold per day`,
      intervals ? `over ${intervals} day${intervals === 1 ? "" : "s"}` : "",
      series.length ? `sold%: ${series.map((v) => `${Number(v).toFixed(0)}%`).join(" → ")}` : "",
    ].filter(Boolean);
    return `<span class="trend ${cls}" title="${escapeAttr(tipParts.join(" · "))}">${sparklineSvg(series)}<span class="trend-val">${sign}${n.toFixed(1)}%/day</span></span>`;
  }

  // ------------------------------------------------------------- dialogs (injected)

  function ensureDialogs() {
    if ($("booking-dialog")) return;
    const host = document.createElement("div");
    host.innerHTML = `
      <dialog id="monitor-dialog" class="confirm-dialog booking-dialog">
        <form method="dialog" id="quick-monitor-form" class="confirm-dialog-form">
          <h3 id="monitor-dialog-title">Monitor a show</h3>
          <p class="field-hint" id="monitor-dialog-show"></p>
          <input type="hidden" id="quick-monitor-slug" value="" />
          <input type="hidden" id="quick-monitor-title" value="" />
          <input type="hidden" id="quick-monitor-url" value="" />
          <label>
            From
            <input type="date" id="quick-monitor-start" required />
          </label>
          <label>
            To
            <input type="date" id="quick-monitor-end" required />
          </label>
          <p class="field-hint">
            You’ll get an email when any performance in this range becomes buyable.
            Manage monitors on the <a href="./monitors.html">Monitors</a> page.
          </p>
          <div class="confirm-actions">
            <button type="submit" value="cancel" formnovalidate class="btn-secondary">Cancel</button>
            <button type="submit" value="save" id="quick-monitor-save">Start monitoring</button>
          </div>
          <span id="quick-monitor-status" class="status" role="status"></span>
        </form>
      </dialog>
      <dialog id="booking-dialog" class="confirm-dialog booking-dialog">
        <form method="dialog" id="booking-form" class="confirm-dialog-form">
          <h3 id="booking-dialog-title">Book a show</h3>
          <input type="hidden" id="booking-id" value="" />
          <label>
            Show
            <input type="text" id="booking-show" list="booking-show-list" required placeholder="Show title" autocomplete="off" />
            <datalist id="booking-show-list"></datalist>
          </label>
          <label>
            Date
            <input type="date" id="booking-date" required />
          </label>
          <label>
            Price paid (£)
            <input type="number" id="booking-price" min="0" step="0.01" required placeholder="0.00" />
          </label>
          <fieldset class="deal-fieldset">
            <legend>Deals used</legend>
            <div id="booking-deals" class="deal-checks"></div>
            <p class="field-hint">Tick any deals you used for this booking. Options update when you pick a show and date.</p>
          </fieldset>
          <label>
            Notes <span class="optional">(optional)</span>
            <input type="text" id="booking-notes" placeholder="e.g. 2 tickets, stalls" />
          </label>
          <div class="confirm-actions">
            <button type="submit" value="cancel" formnovalidate class="btn-secondary">Cancel</button>
            <button type="submit" value="save" id="booking-save">Save booking</button>
          </div>
        </form>
      </dialog>`;
    while (host.firstElementChild) {
      document.body.appendChild(host.firstElementChild);
    }

    $("booking-form").addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (submitter && submitter.value === "cancel") return;
      // Validate + save before the dialog closes via method="dialog".
      if (!saveBookingFromForm()) {
        event.preventDefault();
      }
    });

    ["booking-show", "booking-date"].forEach((id) => {
      $(id).addEventListener("change", () => {
        renderBookingDealChecks(selectedBookingDeals().map((d) => d.code));
      });
    });

    $("quick-monitor-form").addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (submitter && submitter.value === "cancel") return;
      // Keep the dialog open until the async POST resolves, then close it.
      event.preventDefault();
      saveQuickMonitor().then((ok) => {
        if (ok) setTimeout(() => $("monitor-dialog").close(), 700);
      });
    });
  }

  function openMonitorDialog(show, presetDate) {
    if (!show) return;
    ensureDialogs();
    const win = UI.readDateWindow(state.userId, state.config);
    $("quick-monitor-slug").value = show.slug || "";
    $("quick-monitor-title").value = show.show_title || "";
    $("quick-monitor-url").value = show.url || "";
    $("monitor-dialog-show").textContent = show.show_title || "";
    // Default range: the scheduled/selected day (if any) → the view window end.
    $("quick-monitor-start").value = presetDate || state.view.start || win.start_date;
    $("quick-monitor-end").value =
      state.view.end || win.end_date || presetDate || win.start_date;
    $("quick-monitor-status").textContent = "";
    $("monitor-dialog").showModal();
  }

  async function saveQuickMonitor() {
    const status = $("quick-monitor-status");
    const slug = $("quick-monitor-slug").value;
    const start = $("quick-monitor-start").value;
    const end = $("quick-monitor-end").value;
    if (!slug) {
      status.textContent = "This show isn’t in the latest scan yet.";
      return false;
    }
    if (!start || !end || end < start) {
      status.textContent = "Enter a valid date range.";
      return false;
    }
    if (!apiBase) {
      status.textContent = "API URL missing — cannot create a monitor.";
      return false;
    }
    status.textContent = "Creating…";
    const show = state.shows.find((s) => s.slug === slug) || findShow(slug);
    const performances = show
      ? (show.performances || [])
          .filter((p) => p.date >= start && p.date <= end)
          .map((p) => ({
            performance_id: p.performance_id,
            box_office_id: p.box_office_id || "",
            date: p.date,
            time: p.time,
          }))
      : [];
    try {
      const res = await fetch(`${apiBase}/monitors`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug,
          show_title: $("quick-monitor-title").value,
          url: $("quick-monitor-url").value,
          start_date: start,
          end_date: end,
          performances,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      status.textContent = "Monitoring started. See the Monitors page.";
      return true;
    } catch (err) {
      status.textContent = `Could not create monitor: ${err.message || err}`;
      return false;
    }
  }

  function populateBookingShowList() {
    const list = $("booking-show-list");
    if (!list) return;
    const titles = [...new Set(state.shows.map((s) => s.show_title).filter(Boolean))].sort((a, b) =>
      a.localeCompare(b),
    );
    list.innerHTML = titles
      .map((title) => `<option value="${escapeAttr(title)}"></option>`)
      .join("");
  }

  function dealOptionsForBooking(showTitle, dateStr, selectedDeals) {
    const show = findShow(showTitle);
    const dayOffers = dateStr ? offersForShowDay(show, dateStr) : [];
    const allShowOffers = [];
    if (show) {
      for (const day of Object.keys(show.offer_dates || {})) {
        allShowOffers.push(...(show.offer_dates[day] || []));
      }
      for (const p of show.performances || []) {
        allShowOffers.push(...offersForPerf(p));
      }
    }
    return mergeOffers(dayOffers, allShowOffers, selectedDeals, COMMON_DEALS);
  }

  function renderBookingDealChecks(selectedCodes) {
    const selected = new Set(selectedCodes || []);
    const showTitle = $("booking-show").value;
    const dateStr = $("booking-date").value;
    const options = dealOptionsForBooking(showTitle, dateStr, state._bookingDraftDeals || []);
    const host = $("booking-deals");
    if (!host) return;
    host.innerHTML = options
      .map((offer) => {
        const code = offer.code;
        const checked = selected.has(code) ? " checked" : "";
        return `<label class="deal-check"><input type="checkbox" name="booking-deal" value="${escapeAttr(code)}" data-label="${escapeAttr(offer.label || code)}" data-slug="${escapeAttr(offer.slug || "")}"${checked} /> ${escapeHtml(offer.label || code)}</label>`;
      })
      .join("");
  }

  function selectedBookingDeals() {
    return [...document.querySelectorAll('#booking-deals input[name="booking-deal"]:checked')].map(
      (input) => ({
        code: input.value,
        label: input.dataset.label || input.value,
        slug: input.dataset.slug || "",
      }),
    );
  }

  function openBookingDialog(draft = {}) {
    ensureDialogs();
    populateBookingShowList();
    $("booking-id").value = draft.id || "";
    $("booking-show").value = draft.showTitle || "";
    $("booking-date").value = draft.date || "";
    $("booking-price").value =
      draft.price != null && Number.isFinite(Number(draft.price)) ? Number(draft.price) : "";
    $("booking-notes").value = draft.notes || "";
    state._bookingDraftDeals = draft.deals || [];
    $("booking-dialog-title").textContent = draft.id ? "Edit booking" : "Book a show";
    $("booking-save").textContent = draft.id ? "Save changes" : "Save booking";
    renderBookingDealChecks((draft.deals || []).map((d) => d.code));
    $("booking-dialog").showModal();
    $("booking-show").focus();
  }

  function saveBookingFromForm() {
    const id = $("booking-id").value || newBookingId();
    const showTitle = $("booking-show").value.trim();
    const date = $("booking-date").value;
    const price = Number($("booking-price").value);
    const notes = $("booking-notes").value.trim();
    if (!showTitle || !date || !Number.isFinite(price) || price < 0) return false;

    const show = findShow(showTitle);
    const booking = {
      id,
      showTitle: show?.show_title || showTitle,
      slug: show?.slug || null,
      date,
      price,
      deals: selectedBookingDeals().filter((d) => d.code !== "NONE"),
      notes,
      url: show?.url || null,
    };

    const idx = state.bookings.findIndex((b) => b.id === id);
    if (idx >= 0) state.bookings[idx] = booking;
    else state.bookings.push(booking);
    state.bookings.sort(
      (a, b) =>
        String(a.date).localeCompare(String(b.date)) ||
        String(a.showTitle).localeCompare(String(b.showTitle)),
    );
    saveBookings();
    renderAll();
    return true;
  }

  function deleteBooking(id) {
    state.bookings = state.bookings.filter((b) => b.id !== id);
    saveBookings();
    renderAll();
  }

  // ------------------------------------------------------------- data loading

  function renderMeta() {
    const meta = $("meta");
    if (!meta) return;
    const data = state.latestPayload;
    if (!data) return;
    const counts = data.counts || {};
    const offerShows = counts.shows_with_offers;
    meta.textContent = [
      state.latestStale ? "⚠ offline — showing saved data" : null,
      data.fetched_at ? `Scanned ${data.fetched_at.slice(0, 16).replace("T", " ")}` : "No scan yet",
      `${data.show_count || 0} shows`,
      `${counts.sold_out || 0} sold-out perfs`,
      offerShows != null ? `${offerShows} with offers` : null,
      `window ${data.start_date || "?"} → ${data.end_date || "?"}`,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  function applyLatest(data, { fromCache } = {}) {
    state.loaded = true;
    state.latestPayload = data;
    state.latestStale = !!fromCache;
    state.shows = data.shows || [];
    state.scanWindow = { start: data.start_date || "", end: data.end_date || "" };
    renderMeta();
    populateGenreFilter();
    mergeSeedBookings();
    renderAll();
  }

  function renderLoadFailure(error) {
    const message = `Couldn’t load show data (${(error && error.message) || error || "network error"}).`;
    const meta = $("meta");
    if (meta) {
      meta.innerHTML = `${escapeHtml(message)} <button type="button" class="btn-link" data-retry-load>Retry</button>`;
    }
    if (page === "show" && !state.shows.length) {
      const root = $("show-root");
      if (root) {
        root.innerHTML = `<p class="status">${escapeHtml(message)} <button type="button" class="btn-link" data-retry-load>Retry</button></p>`;
      }
    }
    if (page === "shows") {
      const summary = $("shows-summary");
      if (summary && !state.shows.length) summary.textContent = message;
    }
  }

  async function loadLatest() {
    const result = await FringeNet.loadJson("/data/latest.json", applyLatest, {
      retries: 2,
      timeoutMs: 90000,
    });
    if (!result.ok) renderLoadFailure(result.error);
    return result;
  }

  async function loadDetails() {
    await FringeNet.loadJson("/data/details.json", (data) => {
      state.details = data.shows || null;
      renderAll();
    });
    /* details are optional — the page degrades gracefully without them */
  }

  function populateGenreFilter() {
    const select = $("genre-filter");
    if (!select) return;
    const previous = select.value || "all";
    const genres = [
      ...new Set(
        state.shows
          .map((show) => (show.genre || "").trim())
          .filter(Boolean)
      ),
    ].sort((a, b) => a.localeCompare(b));
    select.innerHTML =
      `<option value="all">All</option>` +
      genres.map((genre) => `<option value="${escapeAttr(genre)}">${escapeHtml(genre)}</option>`).join("");
    select.value = genres.includes(previous) ? previous : "all";
  }

  async function loadConfig() {
    let config = null;
    if (apiBase) {
      try {
        // Short timeout: the static /data/config.json fallback is fine, so
        // don't hold the page hostage to a slow API round-trip.
        const res = await FringeNet.fetchWithTimeout(`${apiBase}/config`, {
          cache: "no-store",
          timeoutMs: 8000,
        });
        if (res.ok) config = await res.json();
      } catch (_) {
        /* fall through to static file */
      }
    }
    if (!config) {
      await FringeNet.loadJson("/data/config.json", (data) => {
        config = data;
      });
    }
    if (config) state.config = config;
  }

  // ------------------------------------------------------------- render: shared entry

  function renderAll() {
    if (page === "my") renderItinerary();
    if (page === "shows") renderShows();
    if (page === "show") renderShowDetail();
  }

  // ------------------------------------------------------------- render: My Fringe

  function itineraryEntries() {
    const entries = [];
    const seen = new Set();
    for (const row of state.scheduleRows) {
      const date = parsePlanDate(row.Date || row.date);
      const title = row.Name || row.name || row.Show || "";
      if (!title) continue;
      const show = findShow(title);
      const booking = bookingFor(title, show, date);
      entries.push({
        date,
        time: formatPerfTime(row.Time || row.time || ""),
        title,
        venue: row.Venue || show?.venue || "",
        show,
        booking,
        booked: !!row.__confirmed || !!booking,
        confirmed: !!row.__confirmed,
        past: isPastRow(row),
        score: wishlistScoreFor(title),
      });
      if (date) seen.add(bookingMatchKey(title, date));
    }
    for (const b of state.bookings) {
      if (seen.has(bookingMatchKey(b.showTitle, b.date))) continue;
      const show = findShow(b.showTitle) || (b.slug ? findShowBySlug(b.slug) : null);
      entries.push({
        date: b.date,
        time: "",
        title: b.showTitle,
        venue: show?.venue || "",
        show,
        booking: b,
        booked: true,
        confirmed: false,
        past: b.date < todayStr(),
        score: wishlistScoreFor(b.showTitle),
      });
    }
    for (const e of entries) {
      e.status = e.date ? statusForDay(e.show, e.date) : "unknown";
      e.atRisk = !e.booked && (e.status === "sold_out" || e.status === "nearly_sold_out");
    }
    entries.sort(
      (a, b) =>
        String(a.date || "9999").localeCompare(String(b.date || "9999")) ||
        String(a.time).localeCompare(String(b.time)) ||
        String(a.title).localeCompare(String(b.title)),
    );
    return entries;
  }

  function entryMatchesFilter(entry, filter) {
    if (filter === "booked") return entry.booked;
    if (filter === "risk") return entry.atRisk;
    if (filter === "wishlist") return entry.score != null;
    return true;
  }

  function entryStatusHtml(entry) {
    const bits = [];
    if (entry.booked) {
      const tip = entry.confirmed
        ? "Confirmed on PlanMyFringe — tickets already booked"
        : "In your booked list";
      const price =
        entry.booking && Number.isFinite(Number(entry.booking.price))
          ? ` <span class="booked-price">${escapeHtml(formatPrice(entry.booking.price))}</span>`
          : "";
      bits.push(`<span class="pill booked" title="${escapeAttr(tip)}">✓ booked</span>${price}`);
    }
    if (entry.date && entry.show) {
      const rem = remainingForDay(entry.show, entry.date);
      const statusLabel =
        entry.status === "sold_out"
          ? "sold out"
          : entry.status === "nearly_sold_out"
            ? `${rem} left`
            : entry.status === "available"
              ? rem !== "—"
                ? `${rem} left`
                : "on sale"
              : "no data";
      bits.push(
        `<span class="rem ${entry.status}" title="${escapeAttr(`${entry.date} availability`)}">${escapeHtml(statusLabel)}</span>`,
      );
    } else if (!entry.show) {
      bits.push(`<span class="pill unknown" title="No match in the latest scan">no match</span>`);
    }
    return bits.join(" ");
  }

  function entryActionsHtml(entry) {
    const actions = [];
    if (entry.show?.url) {
      actions.push(
        `<a class="btn-link" href="${escapeAttr(entry.show.url)}" target="_blank" rel="noopener">Tickets</a>`,
      );
    }
    if (entry.booking) {
      actions.push(
        `<button type="button" class="btn-link" data-booking-edit="${escapeAttr(entry.booking.id)}">Edit</button>`,
        `<button type="button" class="btn-link danger" data-booking-delete="${escapeAttr(entry.booking.id)}">Remove</button>`,
      );
    } else if (!entry.booked) {
      actions.push(
        `<button type="button" class="btn-link" data-book-schedule="${escapeAttr(entry.title)}" data-book-date="${escapeAttr(entry.date || "")}">Book</button>`,
      );
      if (entry.show) {
        actions.push(
          `<button type="button" class="btn-link" data-monitor-schedule="${escapeAttr(entry.title)}" data-monitor-date="${escapeAttr(entry.date || "")}">Monitor</button>`,
        );
      }
    }
    return actions.length ? `<div class="btn-row">${actions.join("")}</div>` : "";
  }

  function itinCardHtml(entry) {
    const tags = [entryStatusHtml(entry)];
    if (entry.date && entry.show) {
      const deals = dealsForDayHtml(entry.show, entry.date);
      if (deals) tags.push(deals);
    }
    if (entry.score != null) {
      tags.push(`<span class="pill score" title="Your PlanMyFringe score">★ ${escapeHtml(entry.score)}</span>`);
    }
    if (entry.booking?.notes) {
      tags.push(`<span class="dates">${escapeHtml(entry.booking.notes)}</span>`);
    }
    const actions = entryActionsHtml(entry);

    return `<article class="itin-card${entry.past ? " past" : ""}">
      <div class="itin-time">${escapeHtml(entry.time || "—")}</div>
      <div class="itin-title">${titleHtml(entry.show, entry.title)}</div>
      <div class="itin-venue">${escapeHtml(entry.venue || "")}</div>
      <div class="itin-tags">${tags.filter(Boolean).join(" ")}</div>
      ${actions ? `<div class="itin-actions">${actions}</div>` : ""}
    </article>`;
  }

  function itinRowHtml(entry) {
    const deals =
      entry.date && entry.show ? dealsForDayHtml(entry.show, entry.date) : "";
    const notes = entry.booking?.notes
      ? ` · ${escapeHtml(entry.booking.notes)}`
      : "";
    return `<tr${entry.past ? ' class="past-row"' : ""}>
      <td class="dates">${escapeHtml(entry.time || "—")}</td>
      <td>${titleHtml(entry.show, entry.title)}${scoreChip(entry.title)}<div class="dates">${escapeHtml(entry.venue || "")}${notes}</div></td>
      <td>${entryStatusHtml(entry) || "—"}</td>
      <td class="deals">${deals || "—"}</td>
      <td>${entryActionsHtml(entry)}</td>
    </tr>`;
  }

  function renderItinerary() {
    const host = $("itinerary");
    if (!host) return;
    const showPast = !!($("show-past-toggle") && $("show-past-toggle").checked);
    const all = itineraryEntries();
    const filtered = all.filter((e) => entryMatchesFilter(e, state.itinFilter));
    const pastCount = filtered.filter((e) => e.past).length;
    const visible = showPast ? filtered : filtered.filter((e) => !e.past);

    if (!all.length) {
      host.innerHTML = `<p class="itin-empty">Nothing here yet for ${escapeHtml(UI.userName(state.userId))}. Use <strong>Sync calendar</strong> to pull your PlanMyFringe schedule, import a CSV/PDF export, or add a booking.</p>`;
    } else if (!visible.length) {
      host.innerHTML = `<p class="itin-empty">No entries match this filter${pastCount ? ` (${pastCount} past hidden)` : ""}.</p>`;
    } else {
      const byDate = new Map();
      for (const e of visible) {
        const key = e.date || "";
        if (!byDate.has(key)) byDate.set(key, []);
        byDate.get(key).push(e);
      }
      host.innerHTML = [...byDate.entries()]
        .map(
          ([date, entries]) => `<section class="day-group">
            <h3>${escapeHtml(date ? dayHeading(date) : "No date")}</h3>
            <div class="mobile-cards">${entries.map(itinCardHtml).join("")}</div>
            <div class="table-wrap desktop-table">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Show</th>
                    <th>Status that day</th>
                    <th>Deals</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>${entries.map(itinRowHtml).join("")}</tbody>
              </table>
            </div>
          </section>`,
        )
        .join("");
    }

    const booked = all.filter((e) => e.booked);
    const spent = state.bookings.reduce((sum, b) => sum + (Number(b.price) || 0), 0);
    const risk = all.filter((e) => e.atRisk).length;
    const summary = $("itin-summary");
    if (summary) {
      summary.textContent = [
        `${all.length} shows`,
        `${booked.length} booked${spent ? ` · ${formatPrice(spent)} spent` : ""}`,
        risk ? `${risk} at risk` : null,
        pastCount && !showPast ? `${pastCount} past hidden` : null,
        state.scheduleFileName ? `source: ${state.scheduleFileName}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
    }

    renderWishlistExtras();
  }

  function renderWishlistExtras() {
    const section = $("wishlist-extra");
    const host = $("wishlist-cards");
    if (!section || !host) return;
    if (state.itinFilter === "booked" || state.itinFilter === "risk") {
      section.hidden = true;
      return;
    }
    const scheduled = new Set(
      state.scheduleRows.map((r) => normalizeTitle(r.Name || r.name || r.Show || "")),
    );
    const extras = wishlistEntries().filter(
      (w) =>
        !scheduled.has(normalizeTitle(w.matched_show_title || w.title)) &&
        !scheduled.has(normalizeTitle(w.title)),
    );
    if (!extras.length) {
      section.hidden = true;
      return;
    }
    const sorted = [...extras].sort((a, b) => (b.score || 0) - (a.score || 0));
    const items = sorted.map((w) => {
      const show = findShow(w.matched_show_title || w.title);
      const scorePill =
        w.score != null ? `<span class="pill score">★ ${escapeHtml(w.score)}</span>` : "";
      const availability = show
        ? remainingByDayHtml(show) === "—"
          ? pill("unknown")
          : remainingByDayHtml(show)
        : `<span class="pill unknown">no match</span>`;
      const actions = [];
      const url = show?.url || w.url || "";
      if (url) {
        actions.push(`<a class="btn-link" href="${escapeAttr(url)}" target="_blank" rel="noopener">Tickets</a>`);
      }
      if (show) {
        actions.push(
          `<button type="button" class="btn-link" data-book-show="${escapeAttr(show.slug || show.show_title)}">Book</button>`,
          `<button type="button" class="btn-link" data-monitor-show="${escapeAttr(show.slug || show.show_title)}">Monitor</button>`,
        );
      }
      const actionsHtml = actions.length ? `<div class="btn-row">${actions.join("")}</div>` : "";
      return { w, show, scorePill, availability, actionsHtml };
    });

    host.innerHTML = `
      <div class="mobile-cards">${items
        .map(
          ({ w, show, scorePill, availability, actionsHtml }) => `<article class="itin-card">
            <div class="itin-time">★</div>
            <div class="itin-title">${titleHtml(show, w.title)}</div>
            <div class="itin-venue">${escapeHtml(w.venue || show?.venue || "")}</div>
            <div class="itin-tags">${[scorePill, availability].filter(Boolean).join(" ")}</div>
            ${actionsHtml ? `<div class="itin-actions">${actionsHtml}</div>` : ""}
          </article>`,
        )
        .join("")}</div>
      <div class="table-wrap desktop-table">
        <table>
          <thead>
            <tr>
              <th>Score</th>
              <th>Show</th>
              <th>Availability</th>
              <th></th>
            </tr>
          </thead>
          <tbody>${items
            .map(
              ({ w, show, scorePill, availability, actionsHtml }) => `<tr>
                <td>${scorePill || "—"}</td>
                <td>${titleHtml(show, w.title)}<div class="dates">${escapeHtml(w.venue || show?.venue || "")}</div></td>
                <td class="remaining">${availability}</td>
                <td>${actionsHtml}</td>
              </tr>`,
            )
            .join("")}</tbody>
        </table>
      </div>`;
    section.hidden = false;
  }

  // ------------------------------------------- live availability (My Fringe)

  // The scan's percentages are up to a day old, and a performance the daily
  // scan failed to price lands in latest.json as "available" with no number
  // (it renders as a bare "on sale"). The itinerary is small — one date per
  // show — so on load we re-price exactly those performances through the same
  // endpoint the show page uses. Bounded so a page load can't become a big
  // proxy bill: upcoming entries only, soonest first, hard cap.
  const MAX_LIVE_ITIN_PERFS = 60;
  let itinLiveDone = false;

  /** Performances to re-price: every performance on each upcoming entry's date.
   *
   * Usually exactly one per entry. When a show has two performances that day
   * we take both rather than guessing by time — `remainingForDay` averages
   * across the day, so refreshing only one would blend fresh and stale numbers.
   */
  function itineraryLivePerfs() {
    const today = todayStr();
    const seen = new Set();
    const picked = [];
    const entries = itineraryEntries()
      .filter((e) => e.date && e.show && !e.past && e.date >= today)
      .sort((a, b) => String(a.date).localeCompare(String(b.date)));

    for (const entry of entries) {
      for (const perf of entry.show.performances || []) {
        if (perf.date !== entry.date || !perf.box_office_id) continue;
        if (seen.has(perf.box_office_id)) continue;
        seen.add(perf.box_office_id);
        picked.push({ perf, show: entry.show });
      }
      if (picked.length >= MAX_LIVE_ITIN_PERFS) break;
    }
    return picked.slice(0, MAX_LIVE_ITIN_PERFS);
  }

  function setItinLiveNote(message) {
    const el = $("itin-live");
    if (el) el.textContent = message || "";
  }

  async function refreshItineraryAvailability() {
    if (itinLiveDone || page !== "my" || !apiBase) return;
    const targets = itineraryLivePerfs();
    if (!targets.length) return;
    itinLiveDone = true;

    setItinLiveNote("Checking live availability…");
    try {
      const res = await FringeNet.fetchWithTimeout(`${apiBase}/availability`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          box_office_ids: targets.map((t) => t.perf.box_office_id),
        }),
        cache: "no-store",
        timeoutMs: 20000,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const fresh = data.performances || {};

      const touched = new Set();
      for (const { perf, show } of targets) {
        const update = fresh[perf.box_office_id];
        if (!update) continue;
        perf.availability = update.availability;
        perf.percent_remaining = update.percent_remaining;
        touched.add(show);
      }
      if (!touched.size) {
        setItinLiveNote("Showing last scan (live check unavailable)");
        return;
      }
      for (const show of touched) recomputeShowDates(show);
      renderItinerary();
      const at = data.checked_at ? new Date(data.checked_at) : new Date();
      setItinLiveNote(
        `Live as of ${at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`,
      );
    } catch (_) {
      // Weak signal / API down: the scan numbers already on screen stand.
      setItinLiveNote("Showing last scan (live check unavailable)");
    }
  }

  // ------------------------------------------------------------- render: Shows

  function filteredShows() {
    const q = normalizeTitle($("show-search").value);
    const status = $("status-filter").value;
    const genreFilter = $("genre-filter") ? $("genre-filter").value : "all";
    const offerFilter = $("offer-filter") ? $("offer-filter").value : "all";
    return state.shows.filter((show) => {
      if (q) {
        const hay = normalizeTitle(`${show.show_title} ${show.venue} ${show.genre}`);
        if (!hay.includes(q)) return false;
      }
      if (genreFilter !== "all" && (show.genre || "").trim() !== genreFilter) return false;
      if (!showHasOfferFilter(show, offerFilter)) return false;
      const sold = filterDates(show.sold_out_dates);
      const nearly = showHasNearlySoldOut(show);
      const avail = filterDates(show.available_dates);
      if (status === "sold_out") return sold.length > 0;
      if (status === "nearly_sold_out") return nearly;
      if (status === "available") return sold.length === 0 && !nearly && avail.length > 0;
      return sold.length > 0 || nearly || avail.length > 0 || !status;
    });
  }

  function renderShows() {
    const table = $("shows-table");
    if (!table) return;
    const tbody = table.querySelector("tbody");
    const rows = filteredShows();
    const visible = rows.slice(0, state.showsLimit);

    tbody.innerHTML = visible
      .map((show) => {
        const booked = isShowBooked(show);
        const bookedBadge = booked
          ? ` <span class="pill booked" title="Already in ${escapeAttr(UI.userName(state.userId))}'s booked list">booked</span>`
          : "";
        return `<tr>
          <td>${titleHtml(show)}${bookedBadge}${scoreChip(show.show_title)}<div class="dates">${escapeHtml(show.genre || "")}</div></td>
          <td data-th="Venue" class="dates">${escapeHtml(show.venue || "")}</td>
          <td data-th="Availability" class="remaining">${remainingByDayHtml(show)}</td>
          <td data-th="Deals" class="deals">${dealsByDayHtml(show)}</td>
          <td data-th="7d trend" class="trend-cell">${trendHtml(show)}</td>
          <td><div class="btn-row">
            ${show.url ? `<a class="btn-link" href="${escapeAttr(show.url)}" target="_blank" rel="noopener">Tickets</a>` : ""}
            <button type="button" class="btn-link" data-book-show="${escapeAttr(show.slug || show.show_title)}">Book</button>
            <button type="button" class="btn-link" data-monitor-show="${escapeAttr(show.slug || show.show_title)}">Monitor</button>
          </div></td>
        </tr>`;
      })
      .join("");

    const summary = $("shows-summary");
    if (summary) {
      summary.textContent = !state.loaded
        ? "Loading shows…"
        : rows.length > visible.length
          ? `Showing ${visible.length} of ${rows.length} shows`
          : `${rows.length} show${rows.length === 1 ? "" : "s"}`;
    }
    const moreBtn = $("show-more-btn");
    if (moreBtn) moreBtn.hidden = rows.length <= state.showsLimit;
  }

  // ------------------------------------------------------------- render: show detail

  const AGE_LABELS = {
    THREE: "3+",
    FIVE: "5+",
    SIX: "6+",
    EIGHT: "8+",
    TEN: "10+",
    TWELVE: "12+",
    FOURTEEN: "14+",
    SIXTEEN: "16+",
    EIGHTEEN: "18+",
  };

  function ageLabel(value) {
    const v = String(value || "").trim();
    if (!v) return "";
    return AGE_LABELS[v] || v.toLowerCase().replaceAll("_", " ");
  }

  function mapsUrl(venue) {
    const q = [venue.name, venue.address, "Edinburgh", venue.post_code]
      .filter(Boolean)
      .join(", ");
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}`;
  }

  function detailForShow(show, title) {
    if (!state.details) return null;
    if (show?.slug && state.details[show.slug]) return state.details[show.slug];
    const key = normalizeTitle(title || show?.show_title);
    for (const det of Object.values(state.details)) {
      if (normalizeTitle(det.title) === key) return det;
    }
    return null;
  }

  function renderShowDetail() {
    const root = $("show-root");
    if (!root) return;
    const params = new URLSearchParams(window.location.search);
    const slug = params.get("slug") || "";
    const titleParam = params.get("title") || "";
    const show = findShowBySlug(slug) || findShow(titleParam);
    const det = detailForShow(show, titleParam);
    const title = show?.show_title || det?.title || titleParam || slug;

    if (!show && !det) {
      root.innerHTML = `<div class="show-head"><h1 class="page-head">${escapeHtml(title || "Show not found")}</h1></div>
        <p class="status">This show isn’t in the latest scan. It may be outside the scanned date window — check the <a href="./settings.html">date window</a> or wait for the next daily scan.</p>`;
      document.title = `Fringe Monitor — ${title || "Show"}`;
      return;
    }

    document.title = `Fringe Monitor — ${title}`;
    // Show everything the scan covered on the detail page, not just the
    // user's personal window.
    state.view = {
      start: state.scanWindow?.start || "",
      end: state.scanWindow?.end || "",
    };

    const booked = show ? isShowBooked(show) : false;
    const facts = [];
    if (det?.duration) facts.push(`<span class="fact">${escapeHtml(det.duration)} min</span>`);
    if (det?.age_restriction) facts.push(`<span class="fact">Age ${escapeHtml(ageLabel(det.age_restriction))}</span>`);
    if (show?.genre) facts.push(`<span class="fact">${escapeHtml(show.genre)}</span>`);

    const venues = det?.venues?.length
      ? det.venues
      : show?.venue
        ? [{ name: show.venue, address: "", post_code: "" }]
        : [];
    const venueBlocks = venues
      .map(
        (v) => `<div class="venue-block">
          <strong>${escapeHtml(v.name)}</strong>
          <p class="addr">${escapeHtml([v.address, v.post_code ? `Edinburgh ${v.post_code}` : "Edinburgh"].filter(Boolean).join(", "))}</p>
          ${v.description ? `<p class="field-hint">${escapeHtml(v.description)}</p>` : ""}
          <a class="map-link" href="${escapeAttr(mapsUrl(v))}" target="_blank" rel="noopener">📍 Open in Google Maps</a>
        </div>`,
      )
      .join("");

    const edfringeUrl = show?.url || "";
    const edfestUrl =
      det?.edfest_url || `https://edfest.com/whats-on/${UI.slugifyTitle(title)}`;
    const edfestKnown = !!det?.edfest_url;

    const descriptionHtml = det?.description
      ? `<p class="show-desc">${escapeHtml(det.description)}</p>`
      : `<p class="status">No description yet — it will appear after the next daily scan.</p>`;

    const imageHtml = det?.image_url
      ? `<img class="show-image" src="${escapeAttr(det.image_url.replace(/^http:\/\//, "https://"))}" alt="" loading="lazy" onerror="this.remove()" />`
      : "";

    const availabilityHtml = show
      ? `<section class="panel" aria-label="Availability">
          <div class="panel-head"><h2>Availability</h2>
          <p>Each chip is a scanned date — % is capacity remaining. Tap a stacked chip for individual performances.</p></div>
          <div class="remaining">${remainingByDayHtml(show)}</div>
          ${dealsByDayHtml(show) !== "—" ? `<div class="deals" style="margin-top:0.75rem">${dealsByDayHtml(show)}</div>` : ""}
          <div style="margin-top:0.75rem">${trendHtml(show)}</div>
          <div class="btn-row" style="margin-top:1rem">
            <button type="button" class="btn-link" data-book-show="${escapeAttr(show.slug || show.show_title)}">Book</button>
            <button type="button" class="btn-link" data-monitor-show="${escapeAttr(show.slug || show.show_title)}">Monitor</button>
          </div>
        </section>`
      : "";

    root.innerHTML = `
      <header class="show-head">
        <h1>${escapeHtml(title)}</h1>
        <p class="genre-line">${escapeHtml([show?.genre, show?.venue].filter(Boolean).join(" · "))}</p>
        <div class="itin-tags">
          ${booked ? `<span class="pill booked">✓ booked</span>` : ""}
          ${scoreChip(title)}
        </div>
        <div class="fact-row">${facts.join("")}</div>
      </header>
      ${imageHtml}
      ${descriptionHtml}
      <div class="info-grid">
        <section class="panel" aria-label="Location">
          <div class="panel-head"><h2>Location</h2></div>
          ${venueBlocks || `<p class="status">Venue details will appear after the next daily scan.</p>`}
        </section>
        <section class="panel" aria-label="Tickets">
          <div class="panel-head"><h2>Tickets</h2></div>
          <div class="ticket-links">
            ${edfringeUrl ? `<a class="ticket-btn" href="${escapeAttr(edfringeUrl)}" target="_blank" rel="noopener">Buy on edfringe.com</a>` : ""}
            <a class="ticket-btn alt" href="${escapeAttr(edfestUrl)}" target="_blank" rel="noopener">Buy on EdFest.com</a>
          </div>
          ${!edfestKnown ? `<p class="field-hint">The EdFest link is a best guess — if it doesn’t land on the show, search for “${escapeHtml(title)}” on edfest.com.</p>` : ""}
          <p class="field-hint">EdFest carries the Love the Fringe / 2-for-1 offers; edfringe.com is the official box office.</p>
        </section>
      </div>
      ${availabilityHtml}
    `;

    // Live availability: on a real show, refresh this show's performances via
    // the API (a direct per-performance lookup, no full scan) and re-render the
    // Availability panel so the page shows current sold-out status.
    if (show?.slug && apiBase) {
      refreshLiveAvailability(show);
    }
  }

  async function refreshLiveAvailability(show) {
    const panel = document.querySelector('[aria-label="Availability"] .remaining');
    if (panel) {
      const note = document.createElement("span");
      note.className = "status live-checking";
      note.textContent = "Checking live availability…";
      panel.parentElement.insertBefore(note, panel);
    }
    try {
      const res = await FringeNet.fetchWithTimeout(
        `${apiBase}/shows/${encodeURIComponent(show.slug)}/availability`,
        { cache: "no-store", timeoutMs: 20000 },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.found || !Array.isArray(data.performances)) return;
      const byId = new Map(
        data.performances.map((p) => [String(p.performance_id), p]),
      );
      for (const perf of show.performances || []) {
        const fresh = byId.get(String(perf.performance_id));
        if (fresh) {
          perf.availability = fresh.availability;
          perf.percent_remaining = fresh.percent_remaining;
        }
      }
      // Recompute the show's date buckets from refreshed performances.
      recomputeShowDates(show);
      const remaining = document.querySelector('[aria-label="Availability"] .remaining');
      if (remaining) remaining.innerHTML = remainingByDayHtml(show);
      const checking = document.querySelector(".live-checking");
      if (checking) {
        checking.classList.remove("live-checking");
        checking.textContent = data.checked_at
          ? `Live as of ${new Date(data.checked_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
          : "Live availability";
      }
    } catch (err) {
      const checking = document.querySelector(".live-checking");
      if (checking) checking.textContent = "Showing last scan (live check unavailable)";
    }
  }

  function recomputeShowDates(show) {
    const sold = new Set();
    const nearly = new Set();
    const avail = new Set();
    for (const p of show.performances || []) {
      if (!p.date) continue;
      if (p.availability === "sold_out") sold.add(p.date);
      else if (p.availability === "nearly_sold_out") nearly.add(p.date);
      else avail.add(p.date);
    }
    show.sold_out_dates = [...sold].sort();
    show.nearly_sold_out_dates = [...nearly].sort();
    show.available_dates = [...avail].sort();
  }

  // ------------------------------------------------------------- imports (My Fringe)

  function setScheduleStatus(message) {
    const el = $("csv-status");
    if (el) el.textContent = message || "";
  }

  const PDFJS_VERSION = "3.11.174";
  const PDFJS_SRC = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VERSION}/pdf.min.js`;
  const PDFJS_WORKER_SRC = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VERSION}/pdf.worker.min.js`;
  let pdfjsLoadPromise = null;

  function loadPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (!pdfjsLoadPromise) {
      pdfjsLoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = PDFJS_SRC;
        script.onload = () => {
          window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_SRC;
          resolve(window.pdfjsLib);
        };
        script.onerror = () => {
          pdfjsLoadPromise = null;
          reject(new Error("could not load the PDF reader (offline?)"));
        };
        document.head.appendChild(script);
      });
    }
    return pdfjsLoadPromise;
  }

  async function extractPdfLines(file) {
    const pdfjs = await loadPdfJs();
    const data = new Uint8Array(await file.arrayBuffer());
    const doc = await pdfjs.getDocument({ data }).promise;
    const lines = [];
    for (let p = 1; p <= doc.numPages; p += 1) {
      const page_ = await doc.getPage(p);
      const content = await page_.getTextContent();
      // Group text items into visual lines by y position, then order by x.
      const rows = new Map();
      for (const item of content.items) {
        const str = (item.str || "").trim();
        if (!str) continue;
        const y = item.transform[5];
        let key = null;
        for (const k of rows.keys()) {
          if (Math.abs(k - y) <= 2) {
            key = k;
            break;
          }
        }
        if (key == null) {
          key = y;
          rows.set(key, []);
        }
        rows.get(key).push({ x: item.transform[4], w: item.width || 0, str });
      }
      const sorted = [...rows.entries()].sort((a, b) => b[0] - a[0]);
      for (const [, items] of sorted) {
        items.sort((a, b) => a.x - b.x);
        let line = "";
        let prevEnd = null;
        for (const i of items) {
          if (prevEnd != null) line += i.x - prevEnd > 1 ? " " : "";
          line += i.str;
          prevEnd = i.x + i.w;
        }
        lines.push(line.replace(/\s+/g, " ").trim());
      }
    }
    return lines;
  }

  const PDF_MONTHS = {
    jan: "01", feb: "02", mar: "03", apr: "04", may: "05", jun: "06",
    jul: "07", aug: "08", sep: "09", oct: "10", nov: "11", dec: "12",
  };

  /**
   * Parse a PlanMyFringe PDF export into the same row shape the CSV path
   * produces ({Date, Name, Venue, ...}). Layout per show:
   *   day header  → "Thu 13 Aug"
   *   show line   → "<name> <rating> [<walk> mins] <price> <start> <end> <duration>"
   *   name wrap   → optional extra line(s) when the title is long
   *   venue line  → "…, EH8 9TJ" (always ends with a postcode)
   */
  function parsePdfSchedule(lines) {
    const dayRe = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+([A-Z][a-z]{2})$/;
    const showRe = /^(.*?)\s+(\d{1,2})\s+(?:(\d+)\s+mins?\s+)?(\d+\.\d{2})\s+(\d{1,2}:\d{2})\s+(\d{1,2}:\d{2})\s+(\d{1,2}:\d{2})$/;
    const venueRe = /[A-Z]{1,2}\d{1,2}\s*\d[A-Z]{2}$/;
    const skipRe = /^(Schedule for\b|Name Rating\b|Created by\b|Total Shows:)/i;

    const rows = [];
    let currentDay = null;
    let pending = null;

    const commit = () => {
      if (pending) rows.push(pending);
      pending = null;
    };

    for (const line of lines) {
      if (!line || skipRe.test(line)) continue;

      const dayM = line.match(dayRe);
      if (dayM) {
        commit();
        const month = PDF_MONTHS[dayM[3].toLowerCase()];
        currentDay = month ? `${dayM[1]} ${dayM[2].padStart(2, "0")}/${month}` : null;
        continue;
      }

      if (venueRe.test(line)) {
        if (pending) pending.Venue = line;
        commit();
        continue;
      }

      const m = line.match(showRe);
      if (m && currentDay) {
        commit();
        pending = {
          Date: currentDay,
          Name: m[1].trim(),
          Venue: "",
          Time: m[5],
          "End Time": m[6],
          "Price (£)": m[4],
        };
        continue;
      }

      // Long titles wrap: the numbers sit on the first visual line, the
      // rest of the name follows before the venue line.
      if (pending) pending.Name = `${pending.Name} ${line}`.trim();
    }
    commit();
    return rows;
  }

  function parseCsv(text) {
    const rows = [];
    let row = [];
    let cell = "";
    let inQuotes = false;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      const next = text[i + 1];
      if (inQuotes) {
        if (ch === '"' && next === '"') {
          cell += '"';
          i += 1;
        } else if (ch === '"') {
          inQuotes = false;
        } else {
          cell += ch;
        }
      } else if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        row.push(cell);
        cell = "";
      } else if (ch === "\n") {
        row.push(cell);
        rows.push(row);
        row = [];
        cell = "";
      } else if (ch !== "\r") {
        cell += ch;
      }
    }
    if (cell.length || row.length) {
      row.push(cell);
      rows.push(row);
    }
    if (!rows.length) return [];
    const headers = rows[0].map((h) => h.trim());
    return rows.slice(1)
      .filter((r) => r.some((c) => String(c).trim()))
      .map((r) => {
        const obj = {};
        headers.forEach((h, idx) => {
          obj[h] = (r[idx] || "").trim();
        });
        return obj;
      });
  }

  // ------------------------------------------------------------- events

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const retryLoad = target.closest("[data-retry-load]");
    if (retryLoad) {
      const meta = $("meta");
      if (meta) meta.textContent = "Retrying…";
      loadLatest();
      if (page === "show") loadDetails();
      return;
    }

    const bookShow = target.closest("[data-book-show]");
    if (bookShow) {
      const key = bookShow.getAttribute("data-book-show");
      const show =
        state.shows.find((s) => s.slug === key) ||
        state.shows.find((s) => s.show_title === key) ||
        findShow(key);
      openBookingDialog({
        showTitle: show?.show_title || key,
        date: state.view.start || UI.readDateWindow(state.userId, state.config).start_date,
        deals: [],
      });
      return;
    }

    const bookSchedule = target.closest("[data-book-schedule]");
    if (bookSchedule) {
      const name = bookSchedule.getAttribute("data-book-schedule") || "";
      const date = bookSchedule.getAttribute("data-book-date") || "";
      const show = findShow(name);
      openBookingDialog({
        showTitle: show?.show_title || name,
        date,
        deals: date ? offersForShowDay(show, date) : [],
      });
      return;
    }

    const monitorShow = target.closest("[data-monitor-show]");
    if (monitorShow) {
      const key = monitorShow.getAttribute("data-monitor-show");
      const show =
        state.shows.find((s) => s.slug === key) ||
        state.shows.find((s) => s.show_title === key) ||
        findShow(key);
      openMonitorDialog(show, "");
      return;
    }

    const monitorSchedule = target.closest("[data-monitor-schedule]");
    if (monitorSchedule) {
      const name = monitorSchedule.getAttribute("data-monitor-schedule") || "";
      const date = monitorSchedule.getAttribute("data-monitor-date") || "";
      openMonitorDialog(findShow(name), date);
      return;
    }

    const editBtn = target.closest("[data-booking-edit]");
    if (editBtn) {
      const id = editBtn.getAttribute("data-booking-edit");
      const booking = state.bookings.find((b) => b.id === id);
      if (booking) openBookingDialog(booking);
      return;
    }

    const deleteBtn = target.closest("[data-booking-delete]");
    if (deleteBtn) {
      const id = deleteBtn.getAttribute("data-booking-delete");
      const booking = state.bookings.find((b) => b.id === id);
      if (!booking) return;
      if (window.confirm(`Remove booking for ${booking.showTitle} on ${shortDate(booking.date)}?`)) {
        deleteBooking(id);
      }
      return;
    }

    const chip = target.closest("[data-itin-filter]");
    if (chip) {
      state.itinFilter = chip.getAttribute("data-itin-filter") || "all";
      document.querySelectorAll("[data-itin-filter]").forEach((c) => {
        c.setAttribute(
          "aria-pressed",
          c.getAttribute("data-itin-filter") === state.itinFilter ? "true" : "false",
        );
      });
      renderItinerary();
    }
  });

  if (page === "my") {
    $("sync-planner-btn").addEventListener("click", syncPlanner);
    $("show-past-toggle").addEventListener("change", renderItinerary);
    $("add-booking-btn").addEventListener("click", () => openBookingDialog());

    $("csv-input").addEventListener("change", async (event) => {
      const input = event.target;
      const file = input.files && input.files[0];
      if (!file) return;
      const isPdf =
        /\.pdf$/i.test(file.name || "") || file.type === "application/pdf";
      try {
        setScheduleStatus(isPdf ? "Reading PDF…" : "Reading CSV…");
        const rows = isPdf
          ? parsePdfSchedule(await extractPdfLines(file))
          : parseCsv(await file.text());
        if (!rows.length) {
          setScheduleStatus(
            `No schedule rows found in ${file.name} — is it a PlanMyFringe export?`,
          );
          input.value = "";
          return;
        }
        state.scheduleRows = rows;
        state.scheduleFileName = file.name || "";
        saveSchedule();
        setScheduleStatus(`Imported ${rows.length} rows from ${file.name}.`);
        renderItinerary();
      } catch (err) {
        setScheduleStatus(`Could not read ${file.name}: ${err.message || err}`);
      }
      input.value = "";
    });
  }

  if (page === "shows") {
    ["show-search", "status-filter", "genre-filter", "offer-filter"].forEach((id) => {
      $(id).addEventListener("input", () => {
        state.showsLimit = 100;
        renderShows();
      });
      $(id).addEventListener("change", () => {
        state.showsLimit = 100;
        renderShows();
      });
    });
    ["view-start", "view-end"].forEach((id) => {
      $(id).addEventListener("change", () => {
        state.view = { start: $("view-start").value, end: $("view-end").value };
        state.showsLimit = 100;
        renderShows();
      });
    });
    $("show-more-btn").addEventListener("click", () => {
      state.showsLimit += 200;
      renderShows();
    });
  }

  // ------------------------------------------------------------- init

  loadSavedSchedule();
  loadSavedBookings();

  const initialWindow = UI.readDateWindow(state.userId, state.config);
  state.view = { start: initialWindow.start_date, end: initialWindow.end_date };
  if (page === "shows") {
    $("view-start").value = state.view.start;
    $("view-end").value = state.view.end;
  }

  // Paint what we already have (schedule + bookings live in localStorage)
  // before waiting on the network, so the page is never blank while
  // latest.json is in flight.
  renderAll();

  const loads = [loadConfig(), loadLatest()];
  if (page === "show") loads.push(loadDetails());
  Promise.all(loads).then(() => {
    // Config may refine the fallback window (only when the user has no
    // saved personal window).
    if (!UI.readSavedDateWindow(state.userId)) {
      const win = UI.readDateWindow(state.userId, state.config);
      state.view = { start: win.start_date, end: win.end_date };
      if (page === "shows") {
        $("view-start").value = state.view.start;
        $("view-end").value = state.view.end;
      }
      // The window just moved, so the render loadLatest() did is stale.
      renderAll();
    }
    return loadPlanner();
  })
    .catch(() => {
      /* offline / no planner — whatever loaded is already on screen */
    })
    .then(() => {
      // Itinerary entries are few and date-specific, so re-price them live
      // rather than trusting numbers that are up to a day old.
      if (page === "my") refreshItineraryAvailability();
    });
})();
