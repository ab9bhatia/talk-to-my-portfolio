(function () {
  const cardsEl = document.getElementById("growth-change-cards");
  const chartEl = document.getElementById("growth-daily-chart");
  const perfChartEl = document.getElementById("growth-performance-chart");
  const mixChartEl = document.getElementById("growth-account-mix-chart");
  const benchmarkChartEl = document.getElementById("growth-benchmark-chart");
  const attributionChartEl = document.getElementById("growth-attribution-chart");
  const chartEmpty = document.getElementById("growth-chart-empty");
  const breakdownBody = document.getElementById("growth-breakdown-body");
  const breakdownHint = document.getElementById("growth-breakdown-hint");
  const insightsCards = document.getElementById("growth-insights-cards");
  const timelineHead = document.getElementById("growth-timeline-head");
  const timelineBody = document.getElementById("growth-timeline-body");
  const daysSelect = document.getElementById("growth-days-select");
  const tabs = document.querySelectorAll(".growth-tab[data-breakdown]");

  let growthChart = null;
  let performanceChart = null;
  let accountMixChart = null;
  let benchmarkChart = null;
  let attributionChart = null;
  let dashboardData = null;
  let activeBreakdown = "by_account";
  const PALETTE = ["#38bdf8", "#818cf8", "#34d399", "#f59e0b", "#f472b6", "#fb7185", "#22d3ee"];

  function formatInr(n) {
    if (n == null || Number.isNaN(n)) return "—";
    const sign = n < 0 ? "−" : "";
    return sign + "₹" + Math.round(Math.abs(n)).toLocaleString("en-IN");
  }

  function formatPct(n) {
    if (n == null || Number.isNaN(n)) return "—";
    const sign = n > 0 ? "+" : "";
    return sign + n.toFixed(2) + "%";
  }

  function changeClass(n) {
    if (n == null) return "";
    if (n > 0) return "growth-positive";
    if (n < 0) return "growth-negative";
    return "";
  }

  function renderChangeCards(dc) {
    if (!cardsEl) return;
    if (!dc) {
      cardsEl.innerHTML =
        '<p class="text-muted">Record at least two days (refresh portfolio on consecutive days) to see day-over-day movement.</p>';
      return;
    }
    const changeHtml =
      dc.change != null
        ? `<span class="${changeClass(dc.change)}">${formatInr(dc.change)} (${formatPct(dc.change_pct)})</span>`
        : "<span class=\"text-muted\">First day recorded</span>";
    cardsEl.innerHTML = `
      <article class="growth-stat-card">
        <p class="growth-stat-label">Portfolio value · ${dc.latest_day || "—"}</p>
        <p class="growth-stat-value">${formatInr(dc.value)}</p>
      </article>
      <article class="growth-stat-card">
        <p class="growth-stat-label">vs ${dc.previous_day || "prior day"}</p>
        <p class="growth-stat-value">${changeHtml}</p>
      </article>
      <article class="growth-stat-card">
        <p class="growth-stat-label">Invested change</p>
        <p class="growth-stat-value ${changeClass(dc.invested_change)}">${dc.invested_change != null ? formatInr(dc.invested_change) : "—"}</p>
      </article>
    `;
  }

  function renderChart(series) {
    if (!chartEl || typeof Chart === "undefined") return;
    if (!series || series.length < 1) {
      if (chartEmpty) chartEmpty.hidden = false;
      if (growthChart) {
        growthChart.destroy();
        growthChart = null;
      }
      return;
    }
    if (chartEmpty) chartEmpty.hidden = series.length >= 1;
    const labels = series.map((p) => p.day_date);
    const values = series.map((p) => p.total_current);
    const invested = series.map((p) => p.total_invested);
    if (growthChart) growthChart.destroy();
    growthChart = new Chart(chartEl, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Value (₹)",
            data: values,
            borderColor: "#38bdf8",
            backgroundColor: "rgba(56, 189, 248, 0.12)",
            fill: true,
            tension: 0.25,
            pointRadius: series.length > 30 ? 0 : 3,
          },
          {
            label: "Invested (₹)",
            data: invested,
            borderColor: "#94a3b8",
            borderDash: [4, 4],
            fill: false,
            tension: 0.25,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { ticks: { maxRotation: 45, minRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
          y: {
            ticks: {
              callback: (v) => "₹" + Math.round(v).toLocaleString("en-IN"),
            },
          },
        },
      },
    });
  }

  function sourceBadge(source) {
    if (source === "sheet_distribution") return "sheet";
    if (source === "live") return "live";
    return source || "—";
  }

  function renderInsights(series) {
    if (!insightsCards) return;
    if (!series || !series.length) {
      insightsCards.innerHTML = '<p class="text-muted">No timeline data yet.</p>';
      return;
    }
    const first = series[0];
    const last = series[series.length - 1];
    const absGain = (last.total_current || 0) - (first.total_current || 0);
    const pctGain = first.total_current ? (absGain / first.total_current) * 100 : null;

    let peak = Number.NEGATIVE_INFINITY;
    let maxDrawdown = 0;
    for (const p of series) {
      const v = Number(p.total_current || 0);
      peak = Math.max(peak, v);
      if (peak > 0) {
        const dd = ((v - peak) / peak) * 100;
        maxDrawdown = Math.min(maxDrawdown, dd);
      }
    }

    let bestMove = { date: null, pct: -Infinity };
    for (let i = 1; i < series.length; i++) {
      const prev = Number(series[i - 1].total_current || 0);
      const cur = Number(series[i].total_current || 0);
      if (!prev) continue;
      const p = ((cur - prev) / prev) * 100;
      if (p > bestMove.pct) bestMove = { date: series[i].day_date, pct: p };
    }

    insightsCards.innerHTML = `
      <article class="growth-stat-card">
        <p class="growth-stat-label">Period return</p>
        <p class="growth-stat-value ${changeClass(absGain)}">${formatInr(absGain)} (${formatPct(pctGain)})</p>
      </article>
      <article class="growth-stat-card">
        <p class="growth-stat-label">Max drawdown</p>
        <p class="growth-stat-value ${changeClass(maxDrawdown)}">${formatPct(maxDrawdown)}</p>
      </article>
      <article class="growth-stat-card">
        <p class="growth-stat-label">Best recorded day</p>
        <p class="growth-stat-value ${changeClass(bestMove.pct)}">${bestMove.date || "—"} · ${formatPct(bestMove.pct)}</p>
      </article>
      <article class="growth-stat-card">
        <p class="growth-stat-label">Latest source</p>
        <p class="growth-stat-value">${sourceBadge(last.source)}</p>
      </article>
    `;
  }

  function renderPerformanceChart(series) {
    if (!perfChartEl || typeof Chart === "undefined") return;
    if (!series || !series.length) return;
    const base = Number(series[0].total_current || 0) || 1;
    const labels = series.map((p) => p.day_date);
    const idx = series.map((p) => ((Number(p.total_current || 0) / base) * 100).toFixed(2));
    const pnlPct = series.map((p) => Number(p.total_pnl_pct || 0));
    if (performanceChart) performanceChart.destroy();
    performanceChart = new Chart(perfChartEl, {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "Indexed value", data: idx, borderColor: "#a78bfa", fill: false, tension: 0.25, pointRadius: 0 },
          { label: "P&L %", data: pnlPct, borderColor: "#22d3ee", fill: false, tension: 0.25, pointRadius: 0, yAxisID: "y1" },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { ticks: { autoSkip: true, maxTicksLimit: 10 } },
          y: { ticks: { callback: (v) => Number(v).toFixed(0) } },
          y1: { position: "right", grid: { drawOnChartArea: false }, ticks: { callback: (v) => `${Number(v).toFixed(0)}%` } },
        },
      },
    });
  }

  function renderAccountMixChart(accountSeries) {
    if (!mixChartEl || typeof Chart === "undefined") return;
    if (!accountSeries || !accountSeries.length) return;
    const labels = accountSeries[0].series.map((p) => p.day_date);
    const datasets = accountSeries.map((acc, idx) => ({
      label: acc.code || acc.account_id,
      data: acc.series.map((p) => Number(p.total_current || 0)),
      borderColor: PALETTE[idx % PALETTE.length],
      backgroundColor: `${PALETTE[idx % PALETTE.length]}33`,
      fill: true,
      tension: 0.2,
      pointRadius: 0,
      stack: "mix",
    }));
    if (accountMixChart) accountMixChart.destroy();
    accountMixChart = new Chart(mixChartEl, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { ticks: { autoSkip: true, maxTicksLimit: 10 } },
          y: { stacked: true, ticks: { callback: (v) => "₹" + Math.round(v).toLocaleString("en-IN") } },
        },
      },
    });
  }

  function renderBenchmarkChart(series, benchmarkSeries) {
    if (!benchmarkChartEl || typeof Chart === "undefined") return;
    if (!series || !series.length) return;
    const labels = series.map((p) => p.day_date);
    const base = Number(series[0].total_current || 0) || 1;
    const portfolioIndexed = series.map((p) => Number(((Number(p.total_current || 0) / base) * 100).toFixed(2)));
    const datasets = [
      { label: "Portfolio", data: portfolioIndexed, borderColor: "#38bdf8", fill: false, pointRadius: 0, tension: 0.2 },
    ];
    Object.entries(benchmarkSeries || {}).forEach(([label, rows], idx) => {
      datasets.push({
        label,
        data: (rows || []).map((r) => r.index),
        borderColor: PALETTE[(idx + 2) % PALETTE.length],
        borderDash: [6, 4],
        fill: false,
        pointRadius: 0,
        tension: 0.2,
      });
    });
    if (benchmarkChart) benchmarkChart.destroy();
    benchmarkChart = new Chart(benchmarkChartEl, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { ticks: { autoSkip: true, maxTicksLimit: 10 } },
          y: { ticks: { callback: (v) => Number(v).toFixed(0) } },
        },
      },
    });
  }

  function renderAttributionChart(byAccount) {
    if (!attributionChartEl || typeof Chart === "undefined") return;
    const rows = byAccount || [];
    if (!rows.length) return;
    const labels = rows.map((r) => r.label);
    const values = rows.map((r) => Number(r.change || 0));
    if (attributionChart) attributionChart.destroy();
    attributionChart = new Chart(attributionChartEl, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Change (₹)",
            data: values,
            backgroundColor: values.map((v) => (v >= 0 ? "rgba(74,222,128,0.55)" : "rgba(248,113,113,0.55)")),
            borderColor: values.map((v) => (v >= 0 ? "#4ade80" : "#f87171")),
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { autoSkip: false, maxRotation: 35, minRotation: 0 } },
          y: { ticks: { callback: (v) => "₹" + Math.round(v).toLocaleString("en-IN") } },
        },
      },
    });
  }

  function renderTimelineTable(timelineRows) {
    if (!timelineHead || !timelineBody) return;
    if (!timelineRows || !timelineRows.length) {
      timelineBody.innerHTML = '<tr><td colspan="4" class="text-muted">No date-wise rows.</td></tr>';
      return;
    }
    const accountCodes = Object.keys(timelineRows[0].accounts || {});
    const accountHeaders = accountCodes
      .map((c) => `<th class="text-right">${escapeHtml(c)} invested</th><th class="text-right">${escapeHtml(c)} value</th>`)
      .join("");
    timelineHead.innerHTML = `
      <tr>
        <th>Date</th>
        <th class="text-right">Family invested</th>
        <th class="text-right">Family value</th>
        <th class="text-right">Family P&L %</th>
        ${accountHeaders}
      </tr>
    `;
    timelineBody.innerHTML = timelineRows
      .map((r) => {
        const accountCols = accountCodes
          .map((code) => {
            const cell = r.accounts?.[code] || {};
            return `<td class="text-right">${formatInr(cell.invested)}</td><td class="text-right">${formatInr(cell.value)}</td>`;
          })
          .join("");
        return `
          <tr>
            <td>${escapeHtml(r.day_date || "")}</td>
            <td class="text-right">${formatInr(r.family_invested)}</td>
            <td class="text-right">${formatInr(r.family_value)}</td>
            <td class="text-right ${changeClass(r.family_pnl_pct)}">${formatPct(r.family_pnl_pct)}</td>
            ${accountCols}
          </tr>
        `;
      })
      .join("");
  }

  function renderBreakdown() {
    if (!breakdownBody || !dashboardData) return;
    const rows = (dashboardData.breakdown || {})[activeBreakdown] || [];
    const dc = dashboardData.day_change;
    if (breakdownHint && dc?.latest_day && dc?.previous_day) {
      breakdownHint.textContent = `Comparing ${dc.latest_day} vs ${dc.previous_day} (market movement + any buys/sells).`;
    } else if (breakdownHint) {
      breakdownHint.textContent = "";
    }
    if (!rows.length) {
      breakdownBody.innerHTML =
        '<tr><td colspan="5" class="text-muted">Need two days of snapshots for this breakdown.</td></tr>';
      return;
    }
    breakdownBody.innerHTML = rows
      .map(
        (r) => `
      <tr>
        <td>${escapeHtml(r.label)}</td>
        <td class="text-right">${formatInr(r.value)}</td>
        <td class="text-right">${formatInr(r.prev_value)}</td>
        <td class="text-right ${changeClass(r.change)}">${formatInr(r.change)}</td>
        <td class="text-right ${changeClass(r.change_pct)}">${formatPct(r.change_pct)}</td>
      </tr>`
      )
      .join("");
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  async function loadDashboard() {
    const days = daysSelect ? parseInt(daysSelect.value, 10) || 90 : 90;
    const res = await fetch(`/api/portfolio/daily/dashboard?days=${days}`);
    if (!res.ok) return;
    dashboardData = await res.json();
    renderChangeCards(dashboardData.day_change);
    const series = dashboardData.series || [];
    renderChart(series);
    renderInsights(series);
    renderPerformanceChart(series);
    renderAccountMixChart(dashboardData.account_series || []);
    renderBenchmarkChart(series, dashboardData.benchmark_series || {});
    renderAttributionChart((dashboardData.breakdown || {}).by_account || []);
    renderTimelineTable(dashboardData.timeline_table || []);
    renderBreakdown();
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => {
        t.classList.remove("is-active");
        t.setAttribute("aria-selected", "false");
      });
      tab.classList.add("is-active");
      tab.setAttribute("aria-selected", "true");
      activeBreakdown = tab.getAttribute("data-breakdown") || "by_account";
      renderBreakdown();
    });
  });

  if (daysSelect) {
    daysSelect.addEventListener("change", () => {
      const days = daysSelect.value;
      const url = new URL(window.location.href);
      url.searchParams.set("days", days);
      window.history.replaceState({}, "", url);
      loadDashboard();
    });
  }

  function init() {
    if (typeof Chart !== "undefined") {
      loadDashboard();
    } else {
      window.addEventListener("load", loadDashboard);
    }
  }

  init();
})();
