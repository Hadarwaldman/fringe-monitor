(() => {
  const apiBase = (window.FRINGE_CONFIG && window.FRINGE_CONFIG.apiUrl) || "";
  const USERS = [
    { id: "hadar", name: "Hadar" },
    { id: "adi", name: "Adi" },
  ];
  const ACTIVE_USER_KEY = "fringe-monitor.activeUser";
  const LEGACY_SCHEDULE_KEY = "fringe-monitor.scheduleCsv";
  const DEFAULT_WINDOW = {
    start_date: "2026-08-12",
    end_date: "2026-08-20",
    nearly_threshold: 20,
  };

  const state = {
    shows: [],
    config: null,
    scheduleRows: [],
    scheduleFileName: "",
    bookings: [],
    userId: "hadar",
    planner: null,
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

  function scheduleKey(userId) {
    return `fringe-monitor.scheduleCsv.${userId}`;
  }

  function bookingsKey(userId) {
    return `fringe-monitor.bookings.${userId}`;
  }

  function windowKey(userId) {
    return `fringe-monitor.dateWindow.${userId}`;
  }

  function currentUser() {
    return USERS.find((u) => u.id === state.userId) || USERS[0];
  }

  function readActiveUserId() {
    try {
      const saved = localStorage.getItem(ACTIVE_USER_KEY);
      if (USERS.some((u) => u.id === saved)) return saved;
    } catch (_) {
      /* ignore */
    }
    return "hadar";
  }

  function persistActiveUser() {
    try {
      localStorage.setItem(ACTIVE_USER_KEY, state.userId);
    } catch (_) {
      /* ignore */
    }
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

  function readDateWindow(userId) {
    const saved = readSavedDateWindow(userId);
    if (saved) return saved;
    return {
      start_date: (state.config && state.config.start_date) || DEFAULT_WINDOW.start_date,
      end_date: (state.config && state.config.end_date) || DEFAULT_WINDOW.end_date,
      nearly_threshold:
        (state.config && state.config.nearly_threshold) ?? DEFAULT_WINDOW.nearly_threshold,
    };
  }

  function saveDateWindow(userId, window) {
    try {
      localStorage.setItem(windowKey(userId), JSON.stringify(window));
    } catch (_) {
      /* ignore */
    }
  }

  function applyDateWindowToForm(window) {
    $("start-date").value = window.start_date;
    $("end-date").value = window.end_date;
    $("nearly-threshold").value = window.nearly_threshold;
    $("view-start").value = window.start_date;
    $("view-end").value = window.end_date;
  }

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

  function updateUserUi() {
    const user = currentUser();
    document.querySelectorAll(".user-btn").forEach((btn) => {
      const active = btn.dataset.user === state.userId;
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const datesHeading = $("dates-heading");
    if (datesHeading) datesHeading.textContent = `${user.name}'s date window`;
    const datesLede = $("dates-lede");
    if (datesLede) {
      datesLede.textContent =
        `${user.name}'s trip dates for filtering the tables. The daily scan and watchlist use both people’s windows combined.`;
    }
    const heading = $("compare-heading");
    if (heading) heading.textContent = `${user.name}'s schedule`;
    const lede = $("compare-lede");
    if (lede) {
      lede.textContent =
        `Upload ${user.name}'s PlanMyFringe export (CSV or PDF). Schedules are stored separately per person on this device. Matching is by show name (case-insensitive). Status that day is only for the scheduled date — other open dates are listed separately.`;
    }
    const bookedHeading = $("booked-heading");
    if (bookedHeading) bookedHeading.textContent = `${user.name}'s booked tickets`;
    const bookedLede = $("booked-lede");
    if (bookedLede) {
      bookedLede.textContent =
        `Shows ${user.name} has already bought. Stored on this device — date, price paid, and which deals were used.`;
    }
  }

  function setActiveUser(userId) {
    if (!USERS.some((u) => u.id === userId) || userId === state.userId) {
      updateUserUi();
      return;
    }
    state.userId = userId;
    persistActiveUser();
    loadSavedSchedule();
    loadSavedBookings();
    mergeSeedBookings();
    applyDateWindowToForm(readDateWindow(state.userId));
    updateUserUi();
    renderShows();
    renderCompare();
    renderBooked();
  }

  function setScheduleStatus(message) {
    const el = $("csv-status");
    if (el) el.textContent = message || "";
  }

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

  // --- PlanMyFringe account sync (schedule + wishlist + scores) ---

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
    const d = new Date();
    const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    if (date !== today) return date < today;
    const time = row.Time || row.time || "";
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return !!time && time <= hm;
  }

  function adoptPlannerSchedule() {
    if (!state.planner || !(state.planner.schedule || []).length) return;
    state.scheduleRows = plannerScheduleRows(state.planner);
    state.scheduleFileName = `PlanMyFringe sync (${(state.planner.synced_at || "").slice(0, 16)})`;
    saveSchedule();
  }

  function renderWishlist() {
    const table = $("wishlist-table");
    if (!table) return;
    const tbody = table.querySelector("tbody");
    const entries = wishlistEntries();
    if (!entries.length) {
      table.hidden = true;
      $("wishlist-summary").textContent = state.planner
        ? "No wishlist entries in the last sync."
        : "Not synced yet — use the Sync calendar button above.";
      return;
    }
    const sorted = [...entries].sort((a, b) => (b.score || 0) - (a.score || 0));
    tbody.innerHTML = sorted
      .map((w) => {
        const show = findShow(w.matched_show_title || w.title);
        const sold = show ? (show.sold_out_dates || []).join(", ") || "—" : "—";
        const avail = show ? (show.available_dates || []).join(", ") || "—" : "—";
        const url = (show && show.url) || w.url || "";
        return `<tr>
          <td>${w.score != null ? `<span class="pill score">★ ${escapeHtml(w.score)}</span>` : "—"}</td>
          <td><strong>${escapeHtml(w.title)}</strong>${show ? "" : ` <span class="pill unknown">no match</span>`}</td>
          <td>${escapeHtml(w.venue || (show && show.venue) || "")}</td>
          <td class="dates">${escapeHtml(sold)}</td>
          <td class="dates">${escapeHtml(avail)}</td>
          <td>${url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">Tickets</a>` : "—"}</td>
        </tr>`;
      })
      .join("");
    table.hidden = false;
    $("wishlist-summary").textContent =
      `${entries.length} wishlist shows · synced ${(state.planner.synced_at || "").slice(0, 16) || "?"}`;
  }

  async function loadPlanner() {
    try {
      const res = await fetch(`/data/planner.json?ts=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) return;
      state.planner = await res.json();
      if (!state.scheduleRows.length) adoptPlannerSchedule();
      renderWishlist();
      renderCompare();
      renderShows();
    } catch (_) {
      /* no planner synced yet */
    }
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
      renderWishlist();
      renderCompare();
      renderShows();
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

  function inView(dateStr) {
    const start = $("view-start").value;
    const end = $("view-end").value;
    if (start && dateStr < start) return false;
    if (end && dateStr > end) return false;
    return true;
  }

  function filterDates(dates) {
    return (dates || []).filter(inView);
  }

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
    return (
      state.shows.find((s) => normalizeTitle(s.show_title) === key) ||
      state.shows.find((s) => {
        const t = normalizeTitle(s.show_title);
        return t.includes(key) || key.includes(t);
      }) ||
      null
    );
  }

  function openMonitorDialog(show, presetDate) {
    if (!show) return;
    const win = readDateWindow(state.userId);
    $("quick-monitor-slug").value = show.slug || "";
    $("quick-monitor-title").value = show.show_title || "";
    $("quick-monitor-url").value = show.url || "";
    $("monitor-dialog-show").textContent = show.show_title || "";
    // Default range: the scheduled/selected day (if any) → the view window end.
    $("quick-monitor-start").value = presetDate || $("view-start").value || win.start_date;
    $("quick-monitor-end").value =
      $("view-end").value || win.end_date || presetDate || win.start_date;
    $("quick-monitor-qty").value = 2;
    $("quick-monitor-hold").checked = false;
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
          quantity: Number($("quick-monitor-qty").value || 1),
          hold_tickets: $("quick-monitor-hold").checked,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      status.textContent = "Monitoring started. See the Ticket monitors page.";
      return true;
    } catch (err) {
      status.textContent = `Could not create monitor: ${err.message || err}`;
      return false;
    }
  }

  function statusForDay(show, dateStr) {
    if (!show) return "unknown";
    if ((show.sold_out_dates || []).includes(dateStr)) return "sold_out";
    if ((show.nearly_sold_out_dates || []).includes(dateStr)) return "nearly_sold_out";
    if ((show.available_dates || []).includes(dateStr)) return "available";
    const perf = (show.performances || []).find((p) => p.date === dateStr);
    return perf ? perf.availability : "unknown";
  }

  function shortDate(dateStr) {
    // 2026-08-13 → 13 Aug
    const m = String(dateStr || "").match(/^\d{4}-(\d{2})-(\d{2})$/);
    if (!m) return dateStr || "";
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${Number(m[2])} ${months[Number(m[1]) - 1]}`;
  }

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
    if (!bits.length) return "—";
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
    if (!bits.length) return "—";
    const tip = (deals || [])
      .map((o) => o.label || o.code)
      .filter(Boolean)
      .join(", ");
    return `<span class="deal" title="${escapeAttr(tip)}">${bits
      .map((b) => `<span class="deal-tag">${escapeHtml(b)}</span>`)
      .join("")}</span>`;
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
    renderBooked();
    renderShows();
    renderCompare();
    return true;
  }

  function deleteBooking(id) {
    state.bookings = state.bookings.filter((b) => b.id !== id);
    saveBookings();
    renderBooked();
    renderShows();
    renderCompare();
  }

  function renderBooked() {
    const table = $("booked-table");
    const tbody = table.querySelector("tbody");
    const summary = $("booked-summary");
    if (!state.bookings.length) {
      table.hidden = true;
      summary.textContent = `No bookings yet for ${currentUser().name}.`;
      return;
    }
    const total = state.bookings.reduce((sum, b) => sum + (Number(b.price) || 0), 0);
    tbody.innerHTML = state.bookings
      .map((b) => {
        const show = findShow(b.showTitle) || (b.slug && state.shows.find((s) => s.slug === b.slug));
        const url = b.url || show?.url || "";
        const notes = b.notes
          ? `<div class="dates">${escapeHtml(b.notes)}</div>`
          : "";
        return `<tr data-booking-id="${escapeAttr(b.id)}">
          <td>${escapeHtml(shortDate(b.date))}<div class="dates">${escapeHtml(b.date)}</div></td>
          <td><strong>${escapeHtml(b.showTitle)}</strong>${notes}</td>
          <td class="booked-price">${escapeHtml(formatPrice(b.price))}</td>
          <td class="deals">${dealsListHtml(b.deals)}</td>
          <td><div class="btn-row">
            ${url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">Tickets</a>` : ""}
            <button type="button" class="btn-link" data-booking-edit="${escapeAttr(b.id)}">Edit</button>
            <button type="button" class="btn-link danger" data-booking-delete="${escapeAttr(b.id)}">Remove</button>
          </div></td>
        </tr>`;
      })
      .join("");
    table.hidden = false;
    summary.textContent = `${state.bookings.length} booking${state.bookings.length === 1 ? "" : "s"} · ${formatPrice(total)} total`;
  }

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

  async function loadLatest() {
    const res = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`latest.json HTTP ${res.status}`);
    const data = await res.json();
    state.shows = data.shows || [];
    const counts = data.counts || {};
    const offerShows = counts.shows_with_offers;
    $("meta").textContent = [
      data.fetched_at ? `Scanned ${data.fetched_at}` : "No scan yet",
      `${data.show_count || 0} shows`,
      `${counts.sold_out || 0} sold-out perfs`,
      offerShows != null ? `${offerShows} with offers` : null,
      `scan window ${data.start_date || "?"} → ${data.end_date || "?"}`,
    ]
      .filter(Boolean)
      .join(" · ");
    populateGenreFilter();
    populateBookingShowList();
    mergeSeedBookings();
    renderShows();
    renderCompare();
    renderBooked();
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
        const res = await fetch(`${apiBase}/config`, { cache: "no-store" });
        if (res.ok) config = await res.json();
      } catch (_) {
        /* fall through to static file */
      }
    }
    if (!config) {
      const res = await fetch(`/data/config.json?ts=${Date.now()}`, { cache: "no-store" });
      if (res.ok) config = await res.json();
    }
    if (config) state.config = config;
    applyDateWindowToForm(readDateWindow(state.userId));
  }

  function renderShows() {
    const q = normalizeTitle($("show-search").value);
    const status = $("status-filter").value;
    const genreFilter = $("genre-filter") ? $("genre-filter").value : "all";
    const offerFilter = $("offer-filter") ? $("offer-filter").value : "all";
    const tbody = $("shows-table").querySelector("tbody");
    const rows = state.shows.filter((show) => {
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

    tbody.innerHTML = rows
      .map((show) => {
        const sold = filterDates(show.sold_out_dates).join(", ") || "—";
        const nearly = filterDates(show.nearly_sold_out_dates).join(", ") || "—";
        const avail = filterDates(show.available_dates).join(", ") || "—";
        const booked = isShowBooked(show);
        const bookedBadge = booked
          ? ` <span class="pill booked" title="Already in ${escapeAttr(currentUser().name)}'s booked list">booked</span>`
          : "";
        return `<tr>
          <td><strong>${escapeHtml(show.show_title)}</strong>${bookedBadge}${scoreChip(show.show_title)}<div class="dates">${escapeHtml(show.genre || "")}</div></td>
          <td>${escapeHtml(show.venue || "")}</td>
          <td class="remaining">${remainingByDayHtml(show)}</td>
          <td class="deals">${dealsByDayHtml(show)}</td>
          <td class="trend-cell">${trendHtml(show)}</td>
          <td class="dates">${escapeHtml(sold)}</td>
          <td class="dates">${escapeHtml(nearly)}</td>
          <td class="dates">${escapeHtml(avail)}</td>
          <td>${show.url ? `<a href="${escapeAttr(show.url)}" target="_blank" rel="noopener">Tickets</a>` : ""}</td>
          <td><div class="btn-row">
            <button type="button" class="btn-link" data-book-show="${escapeAttr(show.slug || show.show_title)}">Book</button>
            <button type="button" class="btn-link" data-monitor-show="${escapeAttr(show.slug || show.show_title)}">Monitor</button>
          </div></td>
        </tr>`;
      })
      .join("");
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

  // --- PlanMyFringe PDF schedule parsing (pdf.js loaded on demand) ---

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
      const page = await doc.getPage(p);
      const content = await page.getTextContent();
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

  function renderCompare() {
    const table = $("compare-table");
    const tbody = table.querySelector("tbody");
    if (!state.scheduleRows.length) {
      table.hidden = true;
      $("compare-summary").textContent = "";
      setScheduleStatus(`No schedule uploaded for ${currentUser().name} yet`);
      return;
    }
    let matched = 0;
    let soldHits = 0;
    const rowTime = (row) => row.Time || row.time || "";
    const sortedRows = [...state.scheduleRows].sort((a, b) => {
      const da = parsePlanDate(a.Date || a.date) || "";
      const db = parsePlanDate(b.Date || b.date) || "";
      return da === db ? rowTime(a).localeCompare(rowTime(b)) : da.localeCompare(db);
    });
    const showPast = !!($("show-past-toggle") && $("show-past-toggle").checked);
    const pastCount = sortedRows.filter(isPastRow).length;
    const visibleRows = showPast ? sortedRows : sortedRows.filter((r) => !isPastRow(r));
    tbody.innerHTML = visibleRows
      .map((row) => {
        const date = parsePlanDate(row.Date || row.date);
        const show = findShow(row.Name || row.name || row.Show);
        if (show) matched += 1;
        const status = date ? statusForDay(show, date) : "unknown";
        if (status === "sold_out") soldHits += 1;
        const soldDays = show ? (show.sold_out_dates || []).join(", ") || "—" : "—";
        const nearlyDays = show ? (show.nearly_sold_out_dates || []).join(", ") || "—" : "—";
        const availDays = show ? (show.available_dates || []).join(", ") || "—" : "—";
        const rem = remainingForDay(show, date);
        const bookedForDay =
          !!row.__confirmed ||
          (show ? bookingsForShow(show).some((b) => b.date === date) : false);
        const bookedTip = row.__confirmed ? ` title="Confirmed on PlanMyFringe — tickets already booked"` : "";
        return `<tr${isPastRow(row) ? ' class="past-row"' : ""}>
          <td>${escapeHtml(row.Date || date || "")}</td>
          <td>${escapeHtml(rowTime(row) || "—")}</td>
          <td><strong>${escapeHtml(row.Name || "")}</strong>${bookedForDay ? ` <span class="pill booked"${bookedTip}>booked</span>` : ""}${scoreChip(row.Name)}<div class="dates">${escapeHtml(row.Venue || "")}</div></td>
          <td>${pill(status)}</td>
          <td class="remaining"><span class="rem ${status}">${escapeHtml(rem)}</span></td>
          <td class="deals">${dealsForDayHtml(show, date)}</td>
          <td class="dates">${escapeHtml(soldDays)}</td>
          <td class="dates">${escapeHtml(nearlyDays)}</td>
          <td class="dates">${escapeHtml(availDays)}</td>
          <td>${show?.url ? `<a href="${escapeAttr(show.url)}" target="_blank" rel="noopener">Tickets</a>` : "—"}</td>
          <td><div class="btn-row">
            <button type="button" class="btn-link" data-book-schedule="${escapeAttr(row.Name || "")}" data-book-date="${escapeAttr(date || "")}">Book</button>
            <button type="button" class="btn-link" data-monitor-schedule="${escapeAttr(row.Name || "")}" data-monitor-date="${escapeAttr(date || "")}">Monitor</button>
          </div></td>
        </tr>`;
      })
      .join("");
    table.hidden = false;
    const pastNote = pastCount
      ? showPast
        ? ` · ${pastCount} past shown`
        : ` · ${pastCount} past hidden`
      : "";
    $("compare-summary").textContent =
      `${visibleRows.length} schedule rows · ${matched} matched · ${soldHits} sold out on scheduled day${pastNote}`;
    const user = currentUser();
    setScheduleStatus(
      state.scheduleFileName
        ? `${user.name}: showing ${state.scheduleFileName}`
        : `${user.name}: showing last uploaded schedule`,
    );
  }

  function setScanStatus(message) {
    const el = $("scan-status");
    if (el) el.textContent = message || "";
  }

  function resetScanConfirm() {
    const ack = $("scan-cost-ack");
    const ok = $("scan-confirm-ok");
    if (ack) ack.checked = false;
    if (ok) ok.disabled = true;
  }

  async function triggerScan() {
    if (!apiBase) {
      setScanStatus("API URL missing — cannot start a scan.");
      return;
    }
    const btn = $("run-scan-btn");
    if (btn) btn.disabled = true;
    setScanStatus("Starting scan…");
    try {
      const res = await fetch(`${apiBase}/scan`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      setScanStatus(
        data.message ||
          "Scan started. Refresh in a few minutes for updated data.",
      );
    } catch (err) {
      setScanStatus(`Could not start scan: ${err.message || err}`);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  $("sync-planner-btn").addEventListener("click", syncPlanner);

  $("show-past-toggle").addEventListener("change", renderCompare);

  $("run-scan-btn").addEventListener("click", () => {
    resetScanConfirm();
    $("scan-confirm").showModal();
  });

  $("scan-cost-ack").addEventListener("change", (event) => {
    $("scan-confirm-ok").disabled = !event.target.checked;
  });

  $("scan-confirm").addEventListener("close", () => {
    const confirmed = $("scan-confirm").returnValue === "confirm";
    resetScanConfirm();
    if (confirmed) triggerScan();
  });

  $("config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = $("config-status");
    const personal = {
      start_date: $("start-date").value,
      end_date: $("end-date").value,
      nearly_threshold: Number($("nearly-threshold").value || 20),
    };
    if (personal.end_date < personal.start_date) {
      status.textContent = "End date must be on or after start date.";
      return;
    }
    saveDateWindow(state.userId, personal);
    applyDateWindowToForm(personal);
    renderShows();
    renderCompare();

    if (!apiBase) {
      status.textContent = "Saved for this user (API URL missing — scan window not updated).";
      return;
    }
    status.textContent = "Saving…";
    const scanWindow = unionScanWindow();
    try {
      const res = await fetch(`${apiBase}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scanWindow),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      state.config = data;
      const user = currentUser();
      const same =
        scanWindow.start_date === personal.start_date &&
        scanWindow.end_date === personal.end_date;
      status.textContent = same
        ? `Saved ${user.name}'s dates. Next daily scan will use this window.`
        : `Saved ${user.name}'s dates. Scan covers combined window ${scanWindow.start_date} → ${scanWindow.end_date}.`;
    } catch (err) {
      status.textContent = `Saved locally for ${currentUser().name}; scan update failed: ${err.message || err}`;
    }
  });

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
      renderCompare();
    } catch (err) {
      setScheduleStatus(`Could not read ${file.name}: ${err.message || err}`);
    }
    input.value = "";
  });

  document.querySelectorAll(".user-btn").forEach((btn) => {
    btn.addEventListener("click", () => setActiveUser(btn.dataset.user));
  });

  $("add-booking-btn").addEventListener("click", () => openBookingDialog());

  ["booking-show", "booking-date"].forEach((id) => {
    $(id).addEventListener("change", () => {
      renderBookingDealChecks(selectedBookingDeals().map((d) => d.code));
    });
  });

  $("booking-form").addEventListener("submit", (event) => {
    const submitter = event.submitter;
    if (submitter && submitter.value === "cancel") return;
    // Validate + save before the dialog closes via method="dialog".
    if (!saveBookingFromForm()) {
      event.preventDefault();
    }
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

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const bookShow = target.closest("[data-book-show]");
    if (bookShow) {
      const key = bookShow.getAttribute("data-book-show");
      const show =
        state.shows.find((s) => s.slug === key) ||
        state.shows.find((s) => s.show_title === key) ||
        findShow(key);
      openBookingDialog({
        showTitle: show?.show_title || key,
        date: $("view-start").value || readDateWindow(state.userId).start_date,
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
    }
  });

  ["show-search", "status-filter", "genre-filter", "offer-filter", "view-start", "view-end"].forEach((id) => {
    $(id).addEventListener("input", () => {
      renderShows();
      renderCompare();
    });
    $(id).addEventListener("change", () => {
      renderShows();
      renderCompare();
    });
  });

  state.userId = readActiveUserId();
  persistActiveUser();
  loadSavedSchedule();
  loadSavedBookings();
  updateUserUi();
  renderBooked();
  Promise.all([loadConfig(), loadLatest()])
    .then(() => loadPlanner())
    .catch((err) => {
      $("meta").textContent = `Failed to load data: ${err.message || err}`;
    });
})();
