(() => {
  const panel = document.getElementById("chart-patterns-panel");
  const body = document.getElementById("patterns-panel-body");
  const refreshBtn = document.getElementById("patterns-refresh-btn");
  if (!panel || !body) return;

  const STATUS_LABEL = {
    confirmed: "Formed / breakout",
    forming: "Near breakout",
    early: "Building",
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatInr(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    return `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  }

  function renderPatternRow(symbol, row) {
    const primary = row.primary || (row.patterns && row.patterns[0]);
    if (!primary) return "";
    const others =
      row.patterns?.length > 1
        ? `<span class="patterns-more">+${row.patterns.length - 1} more</span>`
        : "";
    const biasClass = primary.bias === "bearish" ? "patterns-badge-bear" : "patterns-badge-bull";
    return `
      <tr>
        <td><strong>${escapeHtml(symbol)}</strong></td>
        <td><span class="patterns-badge ${biasClass}">${escapeHtml(primary.label)}</span>${others}</td>
        <td><span class="patterns-status patterns-status-${primary.status}">${STATUS_LABEL[primary.status] || primary.status}</span></td>
        <td>${primary.confidence}%</td>
        <td>${formatInr(primary.target_price)} <span class="patterns-upside">(${primary.upside_to_target_pct > 0 ? "+" : ""}${primary.upside_to_target_pct}%)</span></td>
        <td>~${primary.duration_days} trading days</td>
        <td class="patterns-note">${escapeHtml(primary.note || "")}</td>
      </tr>`;
  }

  function renderTable(data) {
    const rows = (data.holdings || []).filter((h) => h.patterns?.length);
    if (!rows.length) {
      body.innerHTML = `<p class="text-muted">No clear patterns detected on ${data.scanned} symbols. Try again after more price history or expand a holding for per-stock detail.</p>`;
      return;
    }
    body.innerHTML = `
      <p class="patterns-summary">${rows.length} of ${data.scanned} holdings show at least one pattern signal.</p>
      <div class="patterns-table-wrap">
        <table class="patterns-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Pattern</th>
              <th>Status</th>
              <th>Confidence</th>
              <th>Target</th>
              <th>Horizon</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((r) => renderPatternRow(r.symbol, r)).join("")}
          </tbody>
        </table>
      </div>`;
  }

  async function loadScan() {
    body.innerHTML = `<p class="text-muted patterns-loading">Scanning price history for chart patterns…</p>`;
    if (refreshBtn) refreshBtn.disabled = true;
    try {
      const res = await fetch("/api/portfolio/patterns");
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      renderTable(await res.json());
    } catch (e) {
      body.innerHTML = `<p class="patterns-error">${escapeHtml(e.message || "Scan failed")}</p>`;
    } finally {
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }

  refreshBtn?.addEventListener("click", loadScan);
})();
