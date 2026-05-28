(() => {
  const form = document.getElementById("portfolio-goals-form");
  const status = document.getElementById("goals-save-status");
  if (!form) return;

  function setStatus(text, ok = true) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("is-bad", !ok);
    status.classList.toggle("setup-goals-status", true);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const payload = Object.fromEntries(fd.entries());
    payload.target_return_pct = Number(payload.target_return_pct || 0);
    payload.max_position_pct = Number(payload.max_position_pct || 0);
    payload.max_sector_pct = Number(payload.max_sector_pct || 0);
    payload.cash_buffer_pct = Number(payload.cash_buffer_pct || 0);
    setStatus("Saving…");
    try {
      const res = await fetch("/api/portfolio/profile/goals", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setStatus(body.detail || "Save failed", false);
        return;
      }
      setStatus("Saved");
    } catch {
      setStatus("Network error", false);
    }
  });
})();
