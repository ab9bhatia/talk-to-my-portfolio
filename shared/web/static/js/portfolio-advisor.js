(function () {
  const app = document.getElementById("advisor-app");
  if (!app) return;

  const rowsEl = document.getElementById("advisor-rows");
  const noticeEl = document.getElementById("advisor-notice");
  const actionFilter = document.getElementById("advisor-action-filter");
  const sellFilter = document.getElementById("advisor-sell-filter");
  const patternFilter = document.getElementById("advisor-pattern-filter");
  const sortEl = document.getElementById("advisor-sort");
  const conflictsOnly = document.getElementById("advisor-conflicts-only");
  const searchEl = document.getElementById("advisor-search");
  const resultCount = document.getElementById("advisor-result-count");
  let payload = null;
  let loadVersion = 0;

  function esc(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function pct(value) {
    return value == null ? "—" : `${Number(value).toFixed(1)}%`;
  }

  function money(value, currency = "INR", maximumFractionDigits = 0) {
    const code = String(currency || "INR").toUpperCase();
    try {
      return new Intl.NumberFormat(code === "INR" ? "en-IN" : "en-US", {
        style: "currency",
        currency: code,
        maximumFractionDigits,
      }).format(Number(value || 0));
    } catch {
      return `${code} ${Number(value || 0).toLocaleString()}`;
    }
  }

  function badge(text, kind) {
    const className = String(kind || "neutral").toLowerCase().replaceAll("_", "-");
    return `<span class="advisor-badge advisor-badge--${esc(className)}">${esc(text)}</span>`;
  }

  function fillSelect(select, values) {
    const current = select.value;
    select.querySelectorAll("option:not(:first-child)").forEach((node) => node.remove());
    [...new Set(values)].sort().forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value.replaceAll("_", " ");
      select.appendChild(option);
    });
    select.value = current;
  }

  function patternLifecycle(pattern) {
    if (!pattern) return "";
    return pattern.lifecycle_state || ({ confirmed: "CONFIRMED", forming: "NEAR_BREAKOUT", early: "BUILDING" }[pattern.status]) || "BUILDING";
  }

  function patternBucket(pattern) {
    if (!pattern) return "NONE";
    const lifecycle = patternLifecycle(pattern);
    if (["CONFIRMED", "RETESTING"].includes(lifecycle) && pattern.target_status === "ACTIVE") return "ACTIVE";
    if (["BUILDING", "NEAR_BREAKOUT"].includes(lifecycle)) return "BUILDING";
    if (["TARGET_ACHIEVED", "TARGET_OVERSHOT"].includes(lifecycle)) return "COMPLETED";
    if (["FAILED_BREAKOUT", "EXPIRED", "INVALIDATED"].includes(lifecycle)) return "INACTIVE";
    return "NONE";
  }

  function patternSummary(pattern) {
    if (!pattern) return '<small>No current setup</small>';
    const lifecycle = patternLifecycle(pattern);
    const score = Number(pattern.heuristic_score ?? pattern.confidence ?? 0);
    const targetStatus = pattern.target_status || "ACTIVE";
    const horizon = pattern.estimated_horizon || {};
    const window = horizon.min_trading_days != null
      ? `${horizon.min_trading_days}–${horizon.max_trading_days} sessions`
      : "Window unavailable";
    const target = pattern.target_price == null
      ? "Target unavailable"
      : `${money(pattern.target_price, pattern.currency, 2)} target`;
    const probability = pattern.calibrated_target_hit_probability == null
      ? "not calibrated"
      : `${pct(Number(pattern.calibrated_target_hit_probability) <= 1 ? Number(pattern.calibrated_target_hit_probability) * 100 : pattern.calibrated_target_hit_probability)} calibrated`;
    const completedClass = targetStatus === "ACTIVE" ? "" : " advisor-target-complete";
    return `${badge(pattern.label, pattern.bias)} ${badge(lifecycle.replaceAll("_", " "), patternBucket(pattern).toLowerCase())}
      <div class="advisor-pattern-line"><strong>${score.toFixed(0)}/100</strong> shape quality · ${esc(probability)}</div>
      <div class="advisor-pattern-meta"><span>${esc(target)}</span><span class="${completedClass}">${esc(targetStatus)}</span><span>${esc(window)}</span></div>`;
  }

  function evidenceDrawer(item) {
    const evidence = (item.evidence || [])
      .map((row) => `<li><strong>${esc(row.claim)}</strong><br>${esc(row.source)} · ${esc(row.as_of)} · ${esc(row.source_type)}</li>`)
      .join("");
    const flags = (item.data_quality_flags || [])
      .map((row) => `<li>${badge(row.severity, row.severity)} ${esc(row.code)} — ${esc(row.message)}</li>`)
      .join("");
    const trace = (item.rule_trace || [])
      .map((row) => `<li><code>${esc(row.rule)}</code> ${row.matched === false ? "not matched" : esc(row.effect || row.result || "considered")}</li>`)
      .join("");
    return `<details class="advisor-evidence"><summary>Inspect evidence, constraints, and rule trace</summary>
      <div class="advisor-evidence-grid">
        <div><h4>Why now</h4><p>${esc(item.why_now)}</p><h4>Tax / settlement</h4><p>${esc(item.tax_note)}</p><p>${esc(item.settlement_note)}</p></div>
        <div><h4>Dated evidence</h4><ul>${evidence || "<li>No dated evidence available.</li>"}</ul></div>
        <div><h4>Data quality</h4><ul>${flags || "<li>No data-quality flags.</li>"}</ul></div>
        <div><h4>Deterministic trace</h4><ul>${trace || "<li>No trace entries.</li>"}</ul></div>
      </div></details>`;
  }

  function filteredRows() {
    const momentumOrder = { STRONG: 5, POSITIVE: 4, NEUTRAL: 3, WEAK: 2, BROKEN: 1 };
    const search = searchEl.value.trim().toLowerCase();
    let rows = [...(payload?.recommendations || [])];
    if (actionFilter.value) rows = rows.filter((row) => row.action === actionFilter.value);
    if (sellFilter.value) rows = rows.filter((row) => row.sell_type === sellFilter.value);
    if (patternFilter.value) rows = rows.filter((row) => patternBucket(row.chart_pattern) === patternFilter.value);
    if (conflictsOnly.checked) rows = rows.filter((row) => row.decision_conflicts?.length);
    if (search) {
      rows = rows.filter((row) => [
        row.symbol,
        row.action,
        row.evidence_state,
        row.sell_type,
        row.chart_pattern?.label,
        patternLifecycle(row.chart_pattern),
      ].some((value) => String(value || "").toLowerCase().includes(search)));
    }
    const sort = sortEl.value;
    rows.sort((a, b) => {
      if (sort === "base_irr") return Number(b.expected_3y_irr?.base_pct ?? -999) - Number(a.expected_3y_irr?.base_pct ?? -999);
      if (sort === "momentum") return (momentumOrder[b.momentum_regime] || 0) - (momentumOrder[a.momentum_regime] || 0);
      if (sort === "action") return String(a.action).localeCompare(String(b.action));
      return Number(b[sort] || 0) - Number(a[sort] || 0);
    });
    return rows;
  }

  function renderRows() {
    if (!payload) return;
    const rows = filteredRows();
    resultCount.textContent = `${rows.length} of ${payload.recommendations?.length || 0} positions`;
    rowsEl.innerHTML = rows.map((item) => {
      const conflict = item.decision_conflicts?.length
        ? `<div class="advisor-conflict">△ ${esc(item.decision_conflicts.join(", "))}</div>`
        : "";
      const meterWidth = Math.max(2, Math.min(100, Number(item.family_weight_pct || 0) * 5));
      const visibleAction = item.evidence_state === "NEEDS_DATA" ? "NEEDS DATA" : item.action;
      const actionKind = item.evidence_state === "NEEDS_DATA" ? "needs-data" : item.action;
      const modelTier = item.evidence_state === "SCREENING_MODEL"
        ? badge("SCREENING MODEL", "screening-model")
        : item.evidence_state === "DOCUMENTED_MODEL"
          ? badge("DOCUMENTED", "documented-model")
          : badge("RESEARCH REQUIRED", "needs-data");
      return `<tr class="advisor-main-row">
        <td><strong class="advisor-symbol">${esc(item.symbol)}</strong><small>${esc(item.instrument_type)} · decision confidence ${item.action_confidence}%</small>${conflict}</td>
        <td>${badge(visibleAction, actionKind)}<br>${item.sell_type !== "NONE" ? badge(item.sell_type, "sell-type") : ""}<small>${item.sell_pct ? `${pct(item.sell_pct)} staged sale` : "No sale modeled"}</small></td>
        <td><span class="advisor-scenarios">${pct(item.expected_3y_irr?.bear_pct)} / <strong>${pct(item.expected_3y_irr?.base_pct)}</strong> / ${pct(item.expected_3y_irr?.bull_pct)}</span><small>Bear / base / bull · ${esc(item.expected_3y_irr?.method || "unavailable")}</small>${modelTier}</td>
        <td>${badge(item.momentum_regime || "UNKNOWN", "momentum")}<br>${patternSummary(item.chart_pattern)}</td>
        <td>${pct(item.family_weight_pct)} → <strong>${pct(item.target_weight_pct)}</strong><div class="advisor-weight-meter"><i style="width:${meterWidth}%"></i></div><small>${money(item.consolidated_value)}</small></td>
        <td>${esc(item.hold_until?.type || "—")}<small>${esc(item.hold_until?.value || "—")}</small></td>
      </tr><tr class="advisor-detail-row"><td colspan="6">${evidenceDrawer(item)}</td></tr>`;
    }).join("") || '<tr><td class="advisor-empty-row" colspan="6">No recommendations match these filters.</td></tr>';
  }

  function queueRow(title, values) {
    return `<div class="advisor-queue-row"><strong>${esc(title)}</strong><span>${values?.length ? values.map(esc).join(", ") : "None"}</span></div>`;
  }

  function renderSummary() {
    const recs = payload.recommendations || [];
    const activePatterns = recs.filter((row) => patternBucket(row.chart_pattern) === "ACTIVE").length;
    const screeningModels = recs.filter((row) => row.evidence_state === "SCREENING_MODEL").length;
    const needsData = recs.filter((row) => row.evidence_state === "NEEDS_DATA").length;
    const stats = [
      ["Holdings", recs.length, `${screeningModels} screened · ${needsData} need research`],
      ["Sell / reduce", recs.filter((row) => ["SELL", "REDUCE"].includes(row.action)).length, "deterministic action queue"],
      ["Add / build", recs.filter((row) => ["ADD", "STRONG_ADD"].includes(row.action)).length, "within allocation limits"],
      ["Active setups", activePatterns, `${recs.filter((row) => row.decision_conflicts?.length).length} timing conflicts`],
    ];
    document.getElementById("advisor-stats").innerHTML = stats
      .map(([label, value, note]) => `<article><strong>${esc(value)}</strong><span>${esc(label)}</span><small>${esc(note)}</small></article>`)
      .join("");
    document.getElementById("advisor-queues").innerHTML = [
      queueRow("Full exits", payload.full_exit_queue),
      queueRow("Reductions", payload.partial_reduction_queue),
      queueRow("Conditional", payload.conditional_hold_queue),
      queueRow("Adds / builds", payload.add_build_queue),
    ].join("");
    const proceeds = Object.entries(payload.proceeds_by_account || {});
    document.getElementById("advisor-proceeds").innerHTML = proceeds.length
      ? proceeds.map(([account, value]) => `<p><strong>${esc(account)}</strong> · ${money(value)}</p>`).join("")
      : "<p>No modeled sale proceeds.</p>";
    document.getElementById("advisor-deadlines").innerHTML = (payload.deadlines || []).slice(0, 12)
      .map((row) => `<p><strong>${esc(row.symbol)}</strong> · ${esc(row.hold_until?.type)}<br><small>${esc(row.hold_until?.value)}</small></p>`).join("") || "<p>No pending review triggers.</p>";
    const status = payload.evidence_status || {};
    const runtime = payload.runtime || {};
    document.getElementById("advisor-evidence-status").innerHTML = `<p><strong>${status.documented_models || 0}</strong> documented models · <strong>${status.screening_models || 0}</strong> screening models · <strong>${status.needs_data || 0}</strong> need research.</p><p>${status.stale_items || 0} stale · ${status.blocking_items || 0} blocking flags.</p><p>Screening models are capped-confidence research signals, never automatic full exits.</p><p>${runtime.patterns?.with_patterns || 0} local pattern scans attached. Shape scores are not calibrated probabilities.</p>`;
  }

  function renderPayload(body) {
    payload = body;
    fillSelect(actionFilter, payload.recommendations.map((row) => row.action));
    fillSelect(sellFilter, payload.recommendations.map((row) => row.sell_type));
    renderSummary();
    renderRows();
  }

  function decisionSetLabel(body) {
    const errors = body.runtime?.account_errors || 0;
    return `${body.schema_version} · generated ${new Date(body.generated_at).toLocaleString()} · ${errors ? `${errors} account sync warning(s)` : "all loaded accounts included"} · execution disabled`;
  }

  async function loadPatternOverlay(version) {
    noticeEl.textContent = `${decisionSetLabel(payload)} · refreshing pattern timing in background…`;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 120000);
    try {
      const scanResponse = await fetch("/api/portfolio/patterns", { signal: controller.signal });
      const scanBody = await scanResponse.json();
      if (!scanResponse.ok) throw new Error(scanBody.detail || `Pattern scan failed (${scanResponse.status})`);
      if (version !== loadVersion) return;

      const response = await fetch("/api/portfolio/advisory?refresh=false&patterns=true");
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `Pattern overlay failed (${response.status})`);
      if (version !== loadVersion) return;

      renderPayload(body);
      noticeEl.textContent = `${decisionSetLabel(body)} · pattern timing ready`;
      noticeEl.className = body.runtime?.account_errors ? "advisor-notice advisor-notice--warning" : "advisor-notice advisor-notice--ok";
    } catch (error) {
      if (version !== loadVersion) return;
      const message = error.name === "AbortError" ? "scan exceeded 120 seconds" : (error.message || "scan failed");
      noticeEl.textContent = `${decisionSetLabel(payload)} · decisions ready; pattern timing unavailable (${message})`;
      noticeEl.className = "advisor-notice advisor-notice--warning";
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function load(refresh) {
    const version = ++loadVersion;
    noticeEl.className = "advisor-notice";
    noticeEl.textContent = refresh ? "Refreshing brokers and deterministic evidence…" : "Building deterministic recommendations…";
    const refreshButton = document.getElementById("advisor-refresh");
    refreshButton.disabled = true;
    try {
      const response = await fetch(`/api/portfolio/advisory?refresh=${refresh ? "true" : "false"}&patterns=false`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `Action Center failed (${response.status})`);
      if (version !== loadVersion) return;
      renderPayload(body);
      const errors = payload.runtime?.account_errors || 0;
      noticeEl.textContent = `${decisionSetLabel(payload)} · decisions ready`;
      noticeEl.className = errors ? "advisor-notice advisor-notice--warning" : "advisor-notice advisor-notice--ok";
      loadPatternOverlay(version);
    } catch (error) {
      if (version !== loadVersion) return;
      noticeEl.textContent = error.message || "Could not load Action Center.";
      noticeEl.className = "advisor-notice advisor-notice--error";
    } finally {
      refreshButton.disabled = false;
    }
  }

  [actionFilter, sellFilter, patternFilter, sortEl, conflictsOnly].forEach((node) => node.addEventListener("change", renderRows));
  searchEl.addEventListener("input", renderRows);
  document.getElementById("advisor-clear").addEventListener("click", () => {
    actionFilter.value = "";
    sellFilter.value = "";
    patternFilter.value = "";
    conflictsOnly.checked = false;
    searchEl.value = "";
    renderRows();
  });
  document.getElementById("advisor-refresh").addEventListener("click", () => load(true));
  load(false);
})();
