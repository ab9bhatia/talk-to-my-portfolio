(function () {
  const uploadBtn = document.getElementById("sarwa-screenshot-btn");
  const metricsBtn = document.getElementById("sarwa-refresh-metrics-btn");
  const fileInput = document.getElementById("sarwa-screenshot-file");
  const importStatus = document.getElementById("sarwa-import-status");
  const growthToggle = document.getElementById("weekly-growth-toggle");
  const growthPanel = document.getElementById("weekly-growth-panel");
  const chartEl = document.getElementById("weekly-growth-chart");
  const emptyEl = document.getElementById("weekly-growth-empty");
  let growthChart = null;

  async function loadGrowth() {
    if (!chartEl || typeof Chart === "undefined") return;
    const res = await fetch("/api/portfolio/weekly/history?scope=family&weeks=52");
    if (!res.ok) return;
    const data = await res.json();
    const series = data.series || [];
    if (series.length < 2) {
      if (emptyEl) {
        emptyEl.hidden = false;
        emptyEl.textContent =
          series.length === 1
            ? "Only one week recorded so far — import weekly to build a 52-week trend."
            : "No weekly snapshots yet.";
      }
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    const labels = series.map((p) => p.week_start);
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
            backgroundColor: "rgba(56, 189, 248, 0.1)",
            fill: true,
            tension: 0.2,
            pointRadius: 2,
          },
          {
            label: "Invested (₹)",
            data: invested,
            borderColor: "#94a3b8",
            borderDash: [4, 4],
            fill: false,
            tension: 0.2,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { position: "bottom" } },
        scales: {
          y: {
            ticks: {
              callback: (v) => "₹" + Math.round(v).toLocaleString("en-IN"),
            },
          },
        },
      },
    });
  }

  if (growthToggle && growthPanel) {
    growthToggle.addEventListener("click", async () => {
      const open = growthPanel.hidden;
      growthPanel.hidden = !open;
      growthToggle.setAttribute("aria-expanded", open ? "true" : "false");
      growthToggle.textContent = open ? "Hide 52-week growth chart" : "Show 52-week growth chart";
      if (open && !growthChart) {
        if (typeof Chart === "undefined") {
          const script = document.createElement("script");
          script.src = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js";
          script.onload = () => loadGrowth();
          document.head.appendChild(script);
        } else {
          await loadGrowth();
        }
      }
    });
  }

  if (uploadBtn && fileInput) {
    uploadBtn.addEventListener("click", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        importStatus.textContent = "Choose a screenshot first";
        return;
      }
      importStatus.textContent = "Reading screenshot…";
      const form = new FormData();
      form.append("file", file);
      try {
        const res = await fetch("/api/portfolio/sarwa/import-screenshot", {
          method: "POST",
          body: form,
        });
        const data = await res.json();
        if (!res.ok) {
          importStatus.textContent =
            typeof data.detail === "string" ? data.detail : "Import failed";
          return;
        }
        importStatus.textContent = `Imported ${data.parsed_count || 0} positions`;
        window.location.href = "/portfolio?refresh=1";
      } catch (_e) {
        importStatus.textContent = "Network error";
      }
    });
  }

  if (metricsBtn) {
    metricsBtn.addEventListener("click", async () => {
      importStatus.textContent = "Fetching Yahoo metrics for SW…";
      try {
        const res = await fetch("/api/portfolio/sarwa/refresh-metrics", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          importStatus.textContent = data.detail || "Failed";
          return;
        }
        importStatus.textContent = `Updated ${data.updated || 0} positions`;
        window.location.href = "/portfolio?refresh=1";
      } catch (_e) {
        importStatus.textContent = "Network error";
      }
    });
  }
})();
