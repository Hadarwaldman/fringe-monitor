(() => {
  const apiBase = (window.FRINGE_CONFIG && window.FRINGE_CONFIG.apiUrl) || "";
  const $ = (id) => document.getElementById(id);

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

  loadStatus();
})();
