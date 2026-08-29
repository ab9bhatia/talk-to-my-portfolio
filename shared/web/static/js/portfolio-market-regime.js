(() => {
  const canvas = document.getElementById("mrmi-history-chart");
  const dataNode = document.getElementById("mrmi-history-data");
  if (!canvas || !dataNode || typeof Chart === "undefined") return;
  const history = JSON.parse(dataNode.textContent || "[]");
  if (!history.length) return;
  new Chart(canvas, {
    type: "line",
    data: {
      labels: history.map((row) => row.as_of),
      datasets: [{
        label: "MRMI",
        data: history.map((row) => row.score),
        borderColor: "#22d3ee",
        backgroundColor: "rgba(34, 211, 238, .12)",
        fill: true,
        tension: 0.25,
        pointRadius: history.length > 60 ? 0 : 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { y: { min: 0, max: 100, grid: { color: "rgba(148,163,184,.08)" } } },
      plugins: { legend: { display: false } },
    },
  });
})();
