(() => {
  const cards = document.getElementById("true-performance-cards");
  const note = document.getElementById("performance-coverage-note");
  const bridge = document.getElementById("return-reconciliation-bridge");
  if (!cards || !note || !bridge) return;

  const money = (value) => value == null
    ? "—"
    : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value);
  const pct = (value) => value == null ? "Unavailable" : `${Number(value).toFixed(2)}%`;
  const endpoint = window.appUrl
    ? window.appUrl("/api/portfolio/performance/summary")
    : "/api/portfolio/performance/summary";

  fetch(endpoint)
    .then(async (response) => {
      if (!response.ok) throw new Error("Performance evidence could not be loaded.");
      return response.json();
    })
    .then((data) => {
      cards.innerHTML = [
        ["XIRR / MWRR", pct(data.xirr_pct), data.xirr_status],
        ["TWRR", pct(data.twrr_pct), "External flows neutralized"],
        ["Income", money(data.income_contribution), "Dividends + interest"],
        ["Fees & tax drag", money((data.fee_drag || 0) + (data.tax_drag || 0)), "Planning view"],
      ].map(([label, value, detail]) => `
        <article class="growth-change-card"><p class="growth-stat-label">${label}</p><p class="growth-stat-value">${value}</p><p class="growth-stat-sub">${detail}</p></article>
      `).join("");
      note.textContent = `Cash-flow ${data.cashflow_coverage_pct}% · lot ${data.lot_coverage_pct}% · valuation ${data.valuation_coverage_pct}% coverage${data.data_quality_flags.length ? ` · ${data.data_quality_flags.join(", ")}` : ""}.`;
      const entries = [
        ["Starting value", data.return_bridge.starting_value],
        ["+ Contributions", data.return_bridge.contributions],
        ["− Withdrawals", -Math.abs(data.return_bridge.withdrawals || 0)],
        ["+ Investment return", data.return_bridge.investment_gain_loss],
        ["+ FX impact", data.return_bridge.fx_impact],
        ["− Fees / taxes", -Math.abs(data.return_bridge.fees_taxes || 0)],
        ["= Ending value", data.return_bridge.ending_value],
      ];
      bridge.innerHTML = entries.map(([label, value]) => `<article><span>${label}</span><strong>${money(value)}</strong></article>`).join("");
      bridge.hidden = false;
    })
    .catch((error) => {
      note.textContent = error.message;
      cards.innerHTML = "";
    });
})();
