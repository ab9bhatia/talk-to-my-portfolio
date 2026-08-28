(() => {
  const panel = document.getElementById("chart-patterns-panel");
  const body = document.getElementById("patterns-panel-body");
  const refreshBtn = document.getElementById("patterns-refresh-btn");
  if (!panel || !body) return;

  const LIFECYCLE_LABEL = {
    BUILDING: "Building",
    NEAR_BREAKOUT: "Near breakout",
    CONFIRMED: "Confirmed",
    RETESTING: "Retesting",
    FAILED_BREAKOUT: "Failed breakout",
    TARGET_ACHIEVED: "Target achieved",
    TARGET_OVERSHOT: "Target overshot",
    EXPIRED: "Expired",
    INVALIDATED: "Invalidated",
  };
  const LEGACY_LIFECYCLE = {
    early: "BUILDING",
    forming: "NEAR_BREAKOUT",
    confirmed: "CONFIRMED",
  };
  const LIFECYCLE_ORDER = {
    CONFIRMED: 0,
    RETESTING: 1,
    NEAR_BREAKOUT: 2,
    BUILDING: 3,
    TARGET_ACHIEVED: 4,
    TARGET_OVERSHOT: 5,
    FAILED_BREAKOUT: 6,
    EXPIRED: 7,
    INVALIDATED: 8,
  };
  const SORTABLE_COLUMNS = [
    { id: "symbol", label: "Security" },
    { id: "pattern", label: "Setup" },
    { id: "lifecycle", label: "Lifecycle" },
    { id: "score", label: "Quality", numeric: true },
    { id: "target", label: "Measured target", numeric: true },
    { id: "remaining", label: "Remaining move", numeric: true },
    { id: "horizon", label: "Est. window", numeric: true },
  ];

  let scanData = null;
  let sortKey = "lifecycle";
  let sortOrder = "asc";
  let activeFilter = "all";

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function primaryOf(row) {
    return row.primary || row.patterns?.[0] || null;
  }

  function lifecycleOf(pattern) {
    return pattern.lifecycle_state || LEGACY_LIFECYCLE[pattern.status] || "BUILDING";
  }

  function scoreOf(pattern) {
    return Number(pattern.heuristic_score ?? pattern.confidence ?? 0);
  }

  function remainingOf(pattern) {
    return Number(
      pattern.bias === "bearish"
        ? pattern.remaining_downside_pct ?? Math.abs(pattern.upside_to_target_pct ?? 0)
        : pattern.remaining_upside_pct ?? Math.max(0, pattern.upside_to_target_pct ?? 0),
    );
  }

  function formatMoney(value, currency) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const code = String(currency || "INR").toUpperCase();
    try {
      return new Intl.NumberFormat(code === "INR" ? "en-IN" : "en-US", {
        style: "currency",
        currency: code,
        maximumFractionDigits: 2,
      }).format(Number(value));
    } catch {
      return `${code} ${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    }
  }

  function horizonText(pattern) {
    const horizon = pattern.estimated_horizon || {};
    if (horizon.min_trading_days == null || horizon.max_trading_days == null) {
      return pattern.duration_days ? `~${pattern.duration_days} sessions` : "Not estimated";
    }
    return `${horizon.min_trading_days}–${horizon.max_trading_days} sessions`;
  }

  function stateClass(value) {
    return String(value || "unknown").toLowerCase().replaceAll("_", "-");
  }

  function filterMatch(row) {
    const pattern = primaryOf(row);
    if (!pattern || activeFilter === "all") return Boolean(pattern);
    const lifecycle = lifecycleOf(pattern);
    if (activeFilter === "bullish") {
      return pattern.bias === "bullish" && ["CONFIRMED", "RETESTING"].includes(lifecycle) && pattern.target_status === "ACTIVE";
    }
    if (activeFilter === "bearish") {
      return pattern.bias === "bearish" && ["CONFIRMED", "RETESTING"].includes(lifecycle) && pattern.target_status === "ACTIVE";
    }
    if (activeFilter === "building") return ["BUILDING", "NEAR_BREAKOUT"].includes(lifecycle);
    if (activeFilter === "completed") return ["TARGET_ACHIEVED", "TARGET_OVERSHOT"].includes(lifecycle);
    if (activeFilter === "inactive") return ["FAILED_BREAKOUT", "EXPIRED", "INVALIDATED"].includes(lifecycle);
    return true;
  }

  function compareRows(a, b) {
    const pa = primaryOf(a);
    const pb = primaryOf(b);
    if (!pa || !pb) return 0;
    const direction = sortOrder === "desc" ? -1 : 1;
    let result = 0;
    if (sortKey === "symbol") result = String(a.symbol).localeCompare(String(b.symbol));
    if (sortKey === "pattern") result = String(pa.label).localeCompare(String(pb.label));
    if (sortKey === "lifecycle") result = (LIFECYCLE_ORDER[lifecycleOf(pa)] ?? 99) - (LIFECYCLE_ORDER[lifecycleOf(pb)] ?? 99);
    if (sortKey === "score") result = scoreOf(pa) - scoreOf(pb);
    if (sortKey === "target") result = Number(pa.target_price ?? 0) - Number(pb.target_price ?? 0);
    if (sortKey === "remaining") result = remainingOf(pa) - remainingOf(pb);
    if (sortKey === "horizon") result = Number(pa.estimated_horizon?.median_trading_days ?? pa.duration_days ?? 0) - Number(pb.estimated_horizon?.median_trading_days ?? pb.duration_days ?? 0);
    if (result !== 0) return result * direction;
    return scoreOf(pb) - scoreOf(pa);
  }

  function visibleRows(data) {
    return [...(data.holdings || [])].filter(filterMatch).sort(compareRows);
  }

  function renderSortHeader(column) {
    const selected = sortKey === column.id;
    const arrow = selected ? (sortOrder === "asc" ? "↑" : "↓") : "";
    return `<th class="patterns-sort-th${column.numeric ? " patterns-num" : ""}${selected ? " is-sorted" : ""}">
      <button type="button" class="patterns-sort-btn" data-sort-col="${column.id}" aria-label="Sort by ${escapeHtml(column.label)}">
        ${escapeHtml(column.label)} <span class="patterns-sort-arrow">${arrow}</span>
      </button>
    </th>`;
  }

  function renderRow(row) {
    const pattern = primaryOf(row);
    if (!pattern) return "";
    const lifecycle = lifecycleOf(pattern);
    const targetStatus = pattern.target_status || "ACTIVE";
    const currency = pattern.currency || row.currency || "INR";
    const remaining = remainingOf(pattern);
    const completed = targetStatus !== "ACTIVE";
    const moveClass = completed
      ? "patterns-move--done"
      : pattern.bias === "bearish"
        ? "patterns-move--bear"
        : "patterns-move--bull";
    const moveText = completed
      ? targetStatus.replaceAll("_", " ").toLowerCase()
      : `${remaining.toFixed(1)}% ${pattern.bias === "bearish" ? "downside" : "upside"}`;
    const probability = pattern.calibrated_target_hit_probability;
    const probabilityText = probability == null
      ? "Not calibrated"
      : `${(Number(probability) * (Number(probability) <= 1 ? 100 : 1)).toFixed(1)}% calibrated`;
    return `<tr>
      <td class="patterns-symbol"><strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.exchange || "")} · ${escapeHtml(currency)}</small></td>
      <td><span class="patterns-setup-name">${escapeHtml(pattern.label)}</span><span class="patterns-bias patterns-bias--${escapeHtml(pattern.bias)}">${escapeHtml(pattern.bias)}</span></td>
      <td><span class="patterns-state patterns-state--${stateClass(lifecycle)}">${escapeHtml(LIFECYCLE_LABEL[lifecycle] || lifecycle)}</span></td>
      <td class="patterns-num"><span class="patterns-score">${scoreOf(pattern).toFixed(0)}<small>/100</small></span><small title="A shape-quality score, not a probability">${escapeHtml(probabilityText)}</small></td>
      <td class="patterns-num patterns-price"><strong>${formatMoney(pattern.target_price, currency)}</strong><small><span class="patterns-target-state patterns-target-state--${stateClass(targetStatus)}">${escapeHtml(targetStatus)}</span></small></td>
      <td class="patterns-num ${moveClass}">${escapeHtml(moveText)}</td>
      <td class="patterns-window">${escapeHtml(horizonText(pattern))}<small>heuristic range</small></td>
      <td class="patterns-note">${escapeHtml(pattern.note || "Measured objective; verify on chart.")}</td>
    </tr>`;
  }

  function filterButton(id, label, count) {
    return `<button type="button" class="patterns-filter${activeFilter === id ? " is-active" : ""}" data-pattern-filter="${id}">${escapeHtml(label)} <span>${count}</span></button>`;
  }

  function render(data) {
    const all = (data.holdings || []).filter((row) => primaryOf(row));
    const count = (predicate) => all.filter((row) => predicate(primaryOf(row))).length;
    const active = count((pattern) => ["CONFIRMED", "RETESTING"].includes(lifecycleOf(pattern)) && pattern.target_status === "ACTIVE");
    const bullish = count((pattern) => pattern.bias === "bullish" && ["CONFIRMED", "RETESTING"].includes(lifecycleOf(pattern)) && pattern.target_status === "ACTIVE");
    const bearish = count((pattern) => pattern.bias === "bearish" && ["CONFIRMED", "RETESTING"].includes(lifecycleOf(pattern)) && pattern.target_status === "ACTIVE");
    const building = count((pattern) => ["BUILDING", "NEAR_BREAKOUT"].includes(lifecycleOf(pattern)));
    const completed = count((pattern) => ["TARGET_ACHIEVED", "TARGET_OVERSHOT"].includes(lifecycleOf(pattern)));
    const inactive = count((pattern) => ["FAILED_BREAKOUT", "EXPIRED", "INVALIDATED"].includes(lifecycleOf(pattern)));
    const rows = visibleRows(data);
    const asOf = data.as_of ? `Data through ${escapeHtml(data.as_of)}` : "Latest available daily bar";

    body.innerHTML = `
      <div class="patterns-overview">
        <article class="patterns-metric"><strong>${all.length}</strong><span>Setups detected</span></article>
        <article class="patterns-metric"><strong>${active}</strong><span>Active confirmed</span></article>
        <article class="patterns-metric"><strong>${building}</strong><span>Building / near</span></article>
        <article class="patterns-metric"><strong>${completed}</strong><span>Targets completed</span></article>
      </div>
      <div class="patterns-radar-toolbar">
        <div class="patterns-filter-list" role="group" aria-label="Filter pattern lifecycle">
          ${filterButton("all", "All", all.length)}
          ${filterButton("bullish", "Confirmed bullish", bullish)}
          ${filterButton("bearish", "Confirmed bearish", bearish)}
          ${filterButton("building", "Building", building)}
          ${filterButton("completed", "Target completed", completed)}
          ${inactive ? filterButton("inactive", "Inactive", inactive) : ""}
        </div>
        <span class="patterns-as-of">${asOf}</span>
      </div>
      ${rows.length ? `<div class="patterns-table-wrap">
        <table class="patterns-table" id="chart-patterns-table">
          <thead><tr>${SORTABLE_COLUMNS.map(renderSortHeader).join("")}<th>Detector note</th></tr></thead>
          <tbody>${rows.map(renderRow).join("")}</tbody>
        </table>
      </div>` : `<div class="patterns-empty">No setups match this lifecycle filter.</div>`}`;

    body.querySelectorAll("[data-pattern-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        activeFilter = button.dataset.patternFilter || "all";
        render(data);
      });
    });
    body.querySelectorAll("[data-sort-col]").forEach((button) => {
      button.addEventListener("click", () => {
        const next = button.dataset.sortCol;
        if (sortKey === next) sortOrder = sortOrder === "asc" ? "desc" : "asc";
        else {
          sortKey = next;
          sortOrder = ["score", "remaining", "target"].includes(next) ? "desc" : "asc";
        }
        render(data);
      });
    });
  }

  async function loadScan() {
    body.innerHTML = `<div class="patterns-skeleton" aria-label="Scanning chart patterns"><span></span><span></span><span></span></div>`;
    if (refreshBtn) refreshBtn.disabled = true;
    try {
      const response = await fetch("/api/portfolio/patterns");
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || response.statusText);
      scanData = result;
      render(scanData);
    } catch (error) {
      scanData = null;
      body.innerHTML = `<p class="patterns-error">${escapeHtml(error.message || "Pattern scan failed")}</p>`;
    } finally {
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }

  refreshBtn?.addEventListener("click", loadScan);
  loadScan();
})();
