(function () {
  const cardsEl = document.getElementById("growth-change-cards");
  const chartEl = document.getElementById("growth-daily-chart");
  const chartEmpty = document.getElementById("growth-chart-empty");
  const breakdownBody = document.getElementById("growth-breakdown-body");
  const breakdownHint = document.getElementById("growth-breakdown-hint");
  const daysSelect = document.getElementById("growth-days-select");
  const tabs = document.querySelectorAll(".growth-tab[data-breakdown]");

  let growthChart = null;
  let dashboardData = null;
  let activeBreakdown = "by_account";

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
    renderChart(dashboardData.series || []);
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
