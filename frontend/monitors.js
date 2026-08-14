(() => {
  const apiBase = (window.FRINGE_CONFIG && window.FRINGE_CONFIG.apiUrl) || "";

  const state = {
    shows: [],
    monitors: [],
    credsConfigured: true,
    scanWindow: { start: "2026-08-01", end: "2026-08-31" },
  };

  const $ = (id) => document.getElementById(id);

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

  function shortDate(dateStr) {
    const m = String(dateStr || "").match(/^\d{4}-(\d{2})-(\d{2})$/);
    if (!m) return dateStr || "";
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${Number(m[2])} ${months[Number(m[1]) - 1]}`;
  }

  function shortDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function normalizeTitle(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[“”"']/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function findShow(name) {
    const key = normalizeTitle(name);
    return (
      state.shows.find((s) => normalizeTitle(s.show_title) === key) ||
      state.shows.find((s) => s.slug === name) ||
      null
    );
  }

  async function api(path, options = {}) {
    if (!apiBase) throw new Error("API URL missing (config.js)");
    const res = await fetch(`${apiBase}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  async function loadShows() {
    const res = await fetch(`/data/latest.json?ts=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    state.shows = data.shows || [];
    if (data.start_date && data.end_date) {
      state.scanWindow = { start: data.start_date, end: data.end_date };
    }
    const list = $("monitor-show-list");
    list.innerHTML = [...new Set(state.shows.map((s) => s.show_title).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b))
      .map((t) => `<option value="${escapeAttr(t)}"></option>`)
      .join("");
    if (!$("monitor-start").value) $("monitor-start").value = data.start_date || "";
    if (!$("monitor-end").value) $("monitor-end").value = data.end_date || "";
  }

  async function loadMonitors() {
    const data = await api("/monitors");
    state.monitors = data.items || [];
    state.credsConfigured = Boolean(data.hold_credentials_configured);
    render();
  }

  function statusChips(monitor) {
    const statuses = monitor.last_result || [];
    if (statuses.length) {
      return statuses
        .map((s) => {
          const cls =
            s.availability === "sold_out"
              ? "sold_out"
              : s.availability === "nearly_sold_out"
                ? "nearly_sold_out"
                : "available";
          const rem = s.percent_remaining != null ? ` ${s.percent_remaining}%` : "";
          const tip = `${s.date} ${s.time} · ${s.availability}${rem}`;
          return `<span class="rem ${cls}" title="${escapeAttr(tip)}">${escapeHtml(shortDate(s.date))} ${escapeHtml(s.time)}${escapeHtml(rem)}</span>`;
        })
        .join(" ");
    }
    // No 15-min check yet — approximate from the last full scan.
    const show = findShow(monitor.slug) || findShow(monitor.show_title);
    if (!show) return `<span class="status">Waiting for first check…</span>`;
    const inRange = (d) => d >= monitor.start_date && d <= monitor.end_date;
    const bits = [];
    for (const [cls, dates] of [
      ["sold_out", show.sold_out_dates],
      ["nearly_sold_out", show.nearly_sold_out_dates],
      ["available", show.available_dates],
    ]) {
      for (const d of (dates || []).filter(inRange)) {
        bits.push(`<span class="rem ${cls}" title="${escapeAttr(`${d} (from last full scan)`)}">${escapeHtml(shortDate(d))}</span>`);
      }
    }
    return bits.join(" ") || `<span class="status">No performances in range</span>`;
  }

  function holdInfo(monitor) {
    let holds = {};
    try {
      holds = JSON.parse(monitor.holds_json || "{}");
    } catch (_) {
      /* ignore */
    }
    const entries = Object.values(holds).sort((a, b) =>
      String(b.at || "").localeCompare(String(a.at || "")),
    );
    const parts = [];
    if (monitor.last_alert_at) {
      parts.push(`Alerted ${shortDateTime(monitor.last_alert_at)}`);
    }
    if (entries.length) {
      const h = entries[0];
      parts.push(
        h.success
          ? `Held ${h.quantity}× ${shortDate(h.date)} ${h.time || ""} (${shortDateTime(h.at)})`
          : `Hold failed: ${h.error || "unknown"}`,
      );
    }
    return parts.length ? parts.join("<br/>") : "—";
  }

  function render() {
    const table = $("monitors-table");
    const tbody = table.querySelector("tbody");
    const meta = $("monitor-meta");
    const active = state.monitors.filter((m) => m.active !== false).length;
    meta.textContent = `${state.monitors.length} monitor${state.monitors.length === 1 ? "" : "s"} · ${active} active · checks every 15 minutes`;

    const holdWanted = state.monitors.some((m) => m.hold_tickets);
    $("creds-banner").hidden = !holdWanted || state.credsConfigured;

    if (!state.monitors.length) {
      table.hidden = true;
      $("monitors-status").textContent = "No monitors yet — add one above.";
      return;
    }
    $("monitors-status").textContent = "";
    tbody.innerHTML = state.monitors
      .map((m) => {
        const paused = m.active === false;
        const holdBadge = m.hold_tickets
          ? `<span class="pill available" title="Will hold tickets in your edfringe basket">hold</span>`
          : `<span class="pill unknown">email only</span>`;
        const show = findShow(m.slug) || findShow(m.show_title);
        const titleHtml = show?.slug
          ? `<a class="show-title-link" href="./show.html?slug=${encodeURIComponent(show.slug)}">${escapeHtml(m.show_title)}</a>`
          : `<strong>${escapeHtml(m.show_title)}</strong>`;
        return `<tr class="${paused ? "monitor-paused" : ""}">
          <td>
            ${titleHtml}
            ${paused ? ` <span class="pill unknown">paused</span>` : ""}
            <div class="dates">${m.url ? `<a href="${escapeAttr(m.url)}" target="_blank" rel="noopener">Ticket page</a>` : ""}</div>
          </td>
          <td data-th="Date range" class="dates">${escapeHtml(shortDate(m.start_date))} → ${escapeHtml(shortDate(m.end_date))} · ${escapeHtml(String(m.quantity || 1))} ticket${(m.quantity || 1) === 1 ? "" : "s"}</td>
          <td data-th="Hold">${holdBadge}</td>
          <td data-th="Availability" class="remaining">${statusChips(m)}</td>
          <td data-th="Last check" class="dates">${escapeHtml(shortDateTime(m.last_checked_at))}</td>
          <td data-th="Last alert / hold" class="dates">${holdInfo(m)}</td>
          <td><div class="btn-row">
            <button type="button" class="btn-link" data-monitor-toggle="${escapeAttr(m.monitor_id)}">${paused ? "Resume" : "Pause"}</button>
            <button type="button" class="btn-link danger" data-monitor-delete="${escapeAttr(m.monitor_id)}">Remove</button>
          </div></td>
        </tr>`;
      })
      .join("");
    table.hidden = false;
  }

  $("monitor-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = $("monitor-form-status");
    const title = $("monitor-show").value.trim();
    const show = findShow(title);
    if (!show) {
      status.textContent = "Pick a show from the list (it must exist in the latest scan).";
      return;
    }
    const start = $("monitor-start").value;
    const end = $("monitor-end").value;
    if (!start || !end || end < start) {
      status.textContent = "Enter a valid date range.";
      return;
    }
    status.textContent = "Creating…";
    try {
      await api("/monitors", {
        method: "POST",
        body: JSON.stringify({
          slug: show.slug,
          show_title: show.show_title,
          url: show.url || "",
          start_date: start,
          end_date: end,
          quantity: Number($("monitor-qty").value || 1),
          hold_tickets: $("monitor-hold").checked,
        }),
      });
      status.textContent = `Monitoring ${show.show_title}. First check within 15 minutes — use “Check now” to run one immediately.`;
      $("monitor-show").value = "";
      await loadMonitors();
    } catch (err) {
      status.textContent = `Could not create monitor: ${err.message || err}`;
    }
  });

  $("check-now-btn").addEventListener("click", async () => {
    const btn = $("check-now-btn");
    btn.disabled = true;
    $("monitors-status").textContent = "Starting a check…";
    try {
      const data = await api("/monitors/check", { method: "POST" });
      $("monitors-status").textContent = data.message || "Check started.";
      setTimeout(() => loadMonitors().catch(() => {}), 90 * 1000);
    } catch (err) {
      $("monitors-status").textContent = `Could not start check: ${err.message || err}`;
    } finally {
      btn.disabled = false;
    }
  });

  document.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const toggle = target.closest("[data-monitor-toggle]");
    if (toggle) {
      const id = toggle.getAttribute("data-monitor-toggle");
      const monitor = state.monitors.find((m) => m.monitor_id === id);
      if (!monitor) return;
      try {
        await api(`/monitors/${id}`, {
          method: "PUT",
          body: JSON.stringify({ active: monitor.active === false }),
        });
        await loadMonitors();
      } catch (err) {
        $("monitors-status").textContent = `Update failed: ${err.message || err}`;
      }
      return;
    }

    const del = target.closest("[data-monitor-delete]");
    if (del) {
      const id = del.getAttribute("data-monitor-delete");
      const monitor = state.monitors.find((m) => m.monitor_id === id);
      if (!monitor) return;
      if (!window.confirm(`Stop monitoring ${monitor.show_title}?`)) return;
      try {
        await api(`/monitors/${id}`, { method: "DELETE" });
        await loadMonitors();
      } catch (err) {
        $("monitors-status").textContent = `Delete failed: ${err.message || err}`;
      }
    }
  });

  Promise.all([loadShows(), loadMonitors()]).catch((err) => {
    $("monitor-meta").textContent = `Failed to load: ${err.message || err}`;
  });
})();
