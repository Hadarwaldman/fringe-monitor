(() => {
  const apiBase = (window.FRINGE_CONFIG && window.FRINGE_CONFIG.apiUrl) || "";
  const UI = window.FringeUI;
  const $ = (id) => document.getElementById(id);

  let userId = UI.activeUserId();
  let config = null;

  // ------------------------------------------------------------- active user

  function renderUserSwitcher() {
    const host = $("user-switcher");
    if (!host) return;
    host.innerHTML = UI.USERS.map(
      (u) =>
        `<button type="button" class="chip" data-user="${u.id}" aria-pressed="${u.id === userId ? "true" : "false"}">${u.name}</button>`,
    ).join("");
    const lede = $("dates-lede");
    if (lede) {
      lede.textContent = `${UI.userName(userId)}'s trip dates, used to filter every page. The daily scan covers both people's windows combined.`;
    }
  }

  document.addEventListener("click", (event) => {
    const btn = event.target instanceof Element ? event.target.closest("[data-user]") : null;
    if (!btn) return;
    userId = btn.getAttribute("data-user");
    UI.setActiveUserId(userId);
    renderUserSwitcher();
    applyWindowToForm();
    UI.renderNav(document.body.dataset.nav || "");
  });

  // ------------------------------------------------------------- date window

  function applyWindowToForm() {
    const win = UI.readDateWindow(userId, config);
    $("start-date").value = win.start_date;
    $("end-date").value = win.end_date;
    $("nearly-threshold").value = win.nearly_threshold;
  }

  async function loadConfig() {
    if (apiBase) {
      try {
        const res = await fetch(`${apiBase}/config`, { cache: "no-store" });
        if (res.ok) config = await res.json();
      } catch (_) {
        /* fall through to static file */
      }
    }
    if (!config) {
      try {
        const res = await fetch(`/data/config.json?ts=${Date.now()}`, { cache: "no-store" });
        if (res.ok) config = await res.json();
      } catch (_) {
        /* offline */
      }
    }
    applyWindowToForm();
  }

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
    UI.saveDateWindow(userId, personal);

    if (!apiBase) {
      status.textContent = "Saved for this user (API URL missing — scan window not updated).";
      return;
    }
    status.textContent = "Saving…";
    const scanWindow = UI.unionScanWindow();
    try {
      const res = await fetch(`${apiBase}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scanWindow),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      config = data;
      const name = UI.userName(userId);
      const same =
        scanWindow.start_date === personal.start_date &&
        scanWindow.end_date === personal.end_date;
      status.textContent = same
        ? `Saved ${name}'s dates. Next daily scan will use this window.`
        : `Saved ${name}'s dates. Scan covers combined window ${scanWindow.start_date} → ${scanWindow.end_date}.`;
    } catch (err) {
      status.textContent = `Saved locally for ${UI.userName(userId)}; scan update failed: ${err.message || err}`;
    }
  });

  // ------------------------------------------------------------- manual scan

  function setScanStatus(message) {
    $("scan-status").textContent = message || "";
  }

  function resetScanConfirm() {
    $("scan-cost-ack").checked = false;
    $("scan-confirm-ok").disabled = true;
  }

  async function triggerScan() {
    if (!apiBase) {
      setScanStatus("API URL missing — cannot start a scan.");
      return;
    }
    const btn = $("run-scan-btn");
    btn.disabled = true;
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
      btn.disabled = false;
    }
  }

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

  // ------------------------------------------------------------- PlanMyFringe

  function setCurrent(message) {
    $("pmf-current").textContent = message || "";
  }

  async function loadStatus() {
    if (!apiBase) {
      setCurrent("API URL missing — settings cannot be loaded.");
      return;
    }
    try {
      const res = await fetch(`${apiBase}/settings/planmyfringe`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      setCurrent(
        data.configured
          ? `Connected as ${data.user_id}. Saving below replaces the stored credentials.`
          : "Not connected yet — enter your PlanMyFringe login below.",
      );
    } catch (err) {
      setCurrent(`Could not load status: ${err.message || err}`);
    }
  }

  $("pmf-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = $("pmf-status");
    if (!apiBase) {
      status.textContent = "API URL missing — cannot save.";
      return;
    }
    const btn = $("pmf-save");
    btn.disabled = true;
    status.textContent = "Verifying login with PlanMyFringe…";
    try {
      const res = await fetch(`${apiBase}/settings/planmyfringe`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: $("pmf-user").value.trim(),
          password: $("pmf-password").value,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      status.textContent = `Saved — connected as ${data.user_id}.`;
      $("pmf-password").value = "";
      loadStatus();
    } catch (err) {
      status.textContent = `Not saved: ${err.message || err}`;
    } finally {
      btn.disabled = false;
    }
  });

  // ------------------------------------------------------------- init

  renderUserSwitcher();
  loadConfig();
  loadStatus();
})();
