(() => {
  const endpoint = (path) => window.appUrl ? window.appUrl(path) : path;
  const money = (value) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value || 0);
  const stressResult = document.getElementById("stress-result");

  document.querySelectorAll("[data-run-stress]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!stressResult) return;
      stressResult.hidden = false;
      stressResult.textContent = "Applying sourced look-through and explicit shocks…";
      const response = await fetch(endpoint("/api/portfolio/stress/run"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: button.dataset.runStress, save: false }),
      });
      const data = await response.json();
      stressResult.innerHTML = response.ok
        ? `<strong>${data.estimated_family_drawdown_pct ?? "—"}% estimated drawdown</strong><span>${money(data.estimated_family_impact)} impact · ${data.coverage_pct}% modeled coverage</span><small>${data.model_limitations.join(" ")}</small>`
        : `<strong>Scenario failed</strong><span>${data.detail || "Unknown error"}</span>`;
    });
  });

  const form = document.getElementById("what-if-form");
  const whatIfResult = document.getElementById("what-if-result");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!whatIfResult) return;
    const values = Object.fromEntries(new FormData(form).entries());
    whatIfResult.hidden = false;
    whatIfResult.textContent = "Simulating without changing holdings…";
    const response = await fetch(endpoint("/api/portfolio/what-if"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        operations: [{ type: "sell_below_weight_pct", threshold_pct: Number(values.threshold_pct) }],
        constraints: {
          max_position_pct: Number(values.max_position_pct),
          sector_cap_pct: Number(values.sector_cap_pct),
          cash_buffer_pct: Number(values.cash_buffer_pct),
        },
      }),
    });
    const data = await response.json();
    whatIfResult.innerHTML = response.ok
      ? `<strong>${data.proposals.length} proposed changes · ${data.turnover_pct}% turnover</strong><span>${data.tax_ca_review_flags} require tax/CA review · execution disabled</span><small>Source portfolio unchanged: ${data.source_portfolio_unchanged ? "yes" : "no"}</small>`
      : `<strong>Simulation failed</strong><span>${data.detail || "Unknown error"}</span>`;
  });
})();
