(function () {
  const app = document.getElementById("advisor-app");
  if (!app) return;

  const rowsEl = document.getElementById("advisor-rows");
  const noticeEl = document.getElementById("advisor-notice");
  const actionFilter = document.getElementById("advisor-action-filter");
  const sellFilter = document.getElementById("advisor-sell-filter");
  const sortEl = document.getElementById("advisor-sort");
  const conflictsOnly = document.getElementById("advisor-conflicts-only");
  let payload = null;

  function esc(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function pct(value) {
    return value == null ? "—" : `${Number(value).toFixed(1)}%`;
  }

  function money(value) {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 0,
    }).format(Number(value || 0));
  }

  function badge(text, kind) {
    return `<span class="advisor-badge advisor-badge--${esc(kind || "neutral")}">${esc(text)}</span>`;
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
    return `<details class="advisor-evidence"><summary>Evidence, tax, conditions, and rule trace</summary>
      <div class="advisor-evidence-grid">
        <div><h4>Why now</h4><p>${esc(item.why_now)}</p><h4>Tax / settlement</h4><p>${esc(item.tax_note)}</p><p>${esc(item.settlement_note)}</p></div>
        <div><h4>Evidence</h4><ul>${evidence || "<li>No dated evidence available.</li>"}</ul></div>
        <div><h4>Quality flags</h4><ul>${flags || "<li>No flags.</li>"}</ul></div>
        <div><h4>Rule trace</h4><ul>${trace}</ul></div>
      </div></details>`;
  }

  function renderRows() {
    if (!payload) return;
    const momentumOrder = { STRONG: 5, POSITIVE: 4, NEUTRAL: 3, WEAK: 2, BROKEN: 1 };
    let rows = [...(payload.recommendations || [])];
    if (actionFilter.value) rows = rows.filter((row) => row.action === actionFilter.value);
    if (sellFilter.value) rows = rows.filter((row) => row.sell_type === sellFilter.value);
    if (conflictsOnly.checked) rows = rows.filter((row) => row.decision_conflicts?.length);
    const sort = sortEl.value;
    rows.sort((a, b) => {
      if (sort === "base_irr") return Number(b.expected_3y_irr?.base_pct ?? -999) - Number(a.expected_3y_irr?.base_pct ?? -999);
      if (sort === "momentum") return (momentumOrder[b.momentum_regime] || 0) - (momentumOrder[a.momentum_regime] || 0);
      if (sort === "action") return String(a.action).localeCompare(String(b.action));
      return Number(b[sort] || 0) - Number(a[sort] || 0);
    });

    rowsEl.innerHTML = rows.map((item) => {
      const pattern = item.chart_pattern;
      const conflict = item.decision_conflicts?.length
        ? `<div class="advisor-conflict">⚠ ${esc(item.decision_conflicts.join(", "))}</div>`
        : "";
      const patternText = pattern
        ? `${badge(pattern.label, pattern.bias)} <small>${esc(pattern.status)} · ${pct(pattern.confidence)}</small>`
        : "<small>No active pattern</small>";
      return `<tr class="advisor-main-row">
        <td><strong>${esc(item.symbol)}</strong><small>${esc(item.instrument_type)} · confidence ${item.action_confidence}%</small>${conflict}</td>
        <td>${badge(item.action, item.action.toLowerCase())}<br>${item.sell_type !== "NONE" ? badge(item.sell_type, "sell-type") : ""}<small>${item.sell_pct ? `${pct(item.sell_pct)} staged sale` : "No sale"}</small></td>
        <td><span class="advisor-scenarios">${pct(item.expected_3y_irr?.bear_pct)} / <strong>${pct(item.expected_3y_irr?.base_pct)}</strong> / ${pct(item.expected_3y_irr?.bull_pct)}</span><small>${esc(item.expected_3y_irr?.method || "unavailable")}</small></td>
        <td>${badge(item.momentum_regime || "UNKNOWN", "momentum")}<br>${patternText}</td>
        <td>${pct(item.family_weight_pct)} → <strong>${pct(item.target_weight_pct)}</strong><small>${money(item.consolidated_value)}</small></td>
        <td>${esc(item.hold_until?.type || "—")}<small>${esc(item.hold_until?.value || "—")}</small></td>
      </tr><tr class="advisor-detail-row"><td colspan="6">${evidenceDrawer(item)}</td></tr>`;
    }).join("") || '<tr><td colspan="6">No recommendations match the filters.</td></tr>';
  }

  function list(title, values) {
    return `<div><strong>${esc(title)}</strong><p>${values?.length ? values.map(esc).join(", ") : "None"}</p></div>`;
  }

  function renderSummary() {
    const recs = payload.recommendations || [];
    const stats = [
      ["Holdings", recs.length],
      ["Sell / reduce", recs.filter((row) => ["SELL", "REDUCE"].includes(row.action)).length],
      ["Add / build", recs.filter((row) => ["ADD", "STRONG_ADD"].includes(row.action)).length],
      ["Signal conflicts", recs.filter((row) => row.decision_conflicts?.length).length],
    ];
    document.getElementById("advisor-stats").innerHTML = stats.map(([label, value]) => `<article><strong>${esc(value)}</strong><span>${esc(label)}</span></article>`).join("");
    document.getElementById("advisor-queues").innerHTML = [
      list("Full exits", payload.full_exit_queue),
      list("Partial reductions", payload.partial_reduction_queue),
      list("Conditional holds", payload.conditional_hold_queue),
      list("Adds / builds", payload.add_build_queue),
    ].join("");
    const proceeds = Object.entries(payload.proceeds_by_account || {});
    document.getElementById("advisor-proceeds").innerHTML = proceeds.length
      ? proceeds.map(([account, value]) => `<p><strong>${esc(account)}</strong> ${money(value)}</p>`).join("")
      : "<p>No modeled sale proceeds.</p>";
    document.getElementById("advisor-deadlines").innerHTML = (payload.deadlines || []).slice(0, 12)
      .map((row) => `<p><strong>${esc(row.symbol)}</strong> · ${esc(row.hold_until?.type)}<br><small>${esc(row.hold_until?.value)}</small></p>`).join("") || "<p>No deadlines.</p>";
    const status = payload.evidence_status || {};
    const runtime = payload.runtime || {};
    document.getElementById("advisor-evidence-status").innerHTML = `<p>${status.with_dated_evidence || 0}/${status.recommendations || 0} recommendations have dated evidence.</p><p>${status.stale_items || 0} stale · ${status.blocking_items || 0} blocking flags.</p><p>${runtime.patterns?.with_patterns || 0} active pattern scans attached.</p>`;
  }

  async function load(refresh) {
    noticeEl.className = "advisor-notice";
    noticeEl.textContent = refresh ? "Refreshing brokers, evidence, and pattern timing…" : "Building deterministic recommendations…";
    try {
      const response = await fetch(`/api/portfolio/advisory?refresh=${refresh ? "true" : "false"}&patterns=true`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `Action Center failed (${response.status})`);
      payload = body;
      fillSelect(actionFilter, payload.recommendations.map((row) => row.action));
      fillSelect(sellFilter, payload.recommendations.map((row) => row.sell_type));
      renderSummary();
      renderRows();
      const errors = payload.runtime?.account_errors || 0;
      noticeEl.textContent = `${payload.schema_version} · generated ${new Date(payload.generated_at).toLocaleString()} · ${errors ? `${errors} account sync warning(s)` : "all loaded accounts included"} · execution disabled`;
      noticeEl.className = errors ? "advisor-notice advisor-notice--warning" : "advisor-notice advisor-notice--ok";
    } catch (error) {
      noticeEl.textContent = error.message || "Could not load Action Center.";
      noticeEl.className = "advisor-notice advisor-notice--error";
    }
  }

  [actionFilter, sellFilter, sortEl, conflictsOnly].forEach((node) => node.addEventListener("change", renderRows));
  document.getElementById("advisor-refresh").addEventListener("click", () => load(true));
  load(false);
})();
