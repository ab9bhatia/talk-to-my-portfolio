(() => {
  const dialog = document.getElementById("reconciliation-resolution-dialog");
  const form = document.getElementById("reconciliation-resolution-form");
  const title = document.getElementById("resolution-title");
  const status = document.getElementById("resolution-status");
  if (!dialog || !form) return;

  const endpoint = window.appUrl
    ? window.appUrl("/api/portfolio/reconciliation/overrides")
    : "/api/portfolio/reconciliation/overrides";

  document.querySelectorAll("[data-open-resolution]").forEach((button) => {
    button.addEventListener("click", () => {
      form.reset();
      form.elements.instrument_id.value = button.dataset.instrumentId || "";
      form.elements.as_of_date.value = new Date().toISOString().slice(0, 10);
      title.textContent = `Resolve ${button.dataset.securityName || "discrepancy"}`;
      status.textContent = "";
      dialog.showModal();
    });
  });

  document.querySelector("[data-close-resolution]")?.addEventListener("click", () => dialog.close());

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    const payload = Object.fromEntries(new FormData(form).entries());
    submit.disabled = true;
    status.textContent = "Saving audited resolution…";
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Resolution could not be saved.");
      status.textContent = "Saved. Refreshing evidence…";
      window.location.reload();
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("text-negative");
      submit.disabled = false;
    }
  });
})();
