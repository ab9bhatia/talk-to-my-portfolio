(() => {
  const chartInstances = new Map();
  let searchDebounceTimer = null;
  let holdingsPageByTable = new Map();
  const DEFAULT_PAGE_SIZE = 50;

  function loadHoldingsFinancials() {
    const el = document.getElementById("holdings-financials-data");
    if (!el?.textContent) return {};
    try {
      return JSON.parse(el.textContent);
    } catch {
      return {};
    }
  }

  const HOLDINGS_FINANCIALS = loadHoldingsFinancials();

  const PIE_COLORS = [
    "#60a5fa",
    "#34d399",
    "#fbbf24",
    "#f472b6",
    "#a78bfa",
    "#22d3ee",
    "#fb923c",
    "#94a3b8",
  ];

  function showLoader(message) {
    const loader = document.getElementById("page-loader");
    const text = document.getElementById("loader-text");
    if (!loader) return;
    if (text && message) text.textContent = message;
    loader.classList.remove("is-hidden");
    loader.setAttribute("aria-busy", "true");
  }

  function hideLoader() {
    const loader = document.getElementById("page-loader");
    if (!loader) return;
    loader.classList.add("is-hidden");
    loader.setAttribute("aria-busy", "false");
    setHoldingsOverlay("", false);
    document.getElementById("holdings-controls-form")?.classList.remove("holdings-form-busy");
  }

  const GROUP_BY_MESSAGES = {
    "": "Loading flat holdings…",
    cap: "Grouping by market cap…",
    sector: "Grouping by sector…",
    account: "Grouping by account…",
    signal: "Grouping by signal…",
    asset_class: "Grouping by asset class…",
  };

  function setHoldingsOverlay(message, visible) {
    const overlay = document.getElementById("holdings-loading-overlay");
    const text = document.getElementById("holdings-loading-text");
    const viewport = document.getElementById("holdings-viewport");
    if (text && message) text.textContent = message;
    if (overlay) overlay.hidden = !visible;
    if (viewport) viewport.classList.toggle("is-loading", visible);
  }

  function initGroupBySelect() {
    const form = document.getElementById("holdings-controls-form");
    const select = form?.querySelector(".js-group-by-select");
    if (!select || !form) return;

    select.addEventListener("change", () => {
      const value = select.value || "";
      const label = select.options[select.selectedIndex]?.text?.trim() || "view";
      const message = GROUP_BY_MESSAGES[value] || `Applying ${label}…`;
      showLoader(message);
      setHoldingsOverlay(message, true);
      // Do not disable the select before submit — disabled fields are omitted from GET.
      form.classList.add("holdings-form-busy");
      form.submit();
    });
  }

  function initPageLoader() {
    showLoader("Loading portfolio…");

    const form = document.getElementById("holdings-controls-form");
    form?.addEventListener("submit", () => {
      const select = form.querySelector(".js-group-by-select");
      const value = select?.value || "";
      const message = GROUP_BY_MESSAGES[value] || "Updating view…";
      showLoader(message);
      setHoldingsOverlay(message, true);
    });

    document.querySelectorAll(".js-show-loader").forEach((el) => {
      el.addEventListener("click", () => {
        const msg = el.dataset.loaderMessage || "Loading…";
        showLoader(msg);
      });
    });

    window.addEventListener("pageshow", (event) => {
      if (event.persisted) hideLoader();
    });
  }

  function formatInr(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    return `₹${Number(value).toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function formatInrWhole(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    return `₹${Number(value).toLocaleString("en-IN", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    })}`;
  }

  function formatPct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    const num = Number(value);
    const sign = num > 0 ? "+" : "";
    return `${sign}${num.toFixed(2)}%`;
  }

  function getSymbolList() {
    const table = document.getElementById("holdings-table");
    if (table?.dataset.symbols) {
      return table.dataset.symbols.split(",").filter(Boolean);
    }
    const symbols = new Set();
    document.querySelectorAll(".holding-row[data-symbol]").forEach((row) => {
      if (row.dataset.symbol) symbols.add(row.dataset.symbol);
    });
    return [...symbols].sort();
  }

  function pageSizeForTable(table) {
    const raw = table?.dataset?.pageSize;
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_PAGE_SIZE;
  }

  function tableKey(table) {
    return table.id || table.closest(".holdings-group")?.dataset?.groupLabel || String(table);
  }

  function getSelectedAccountCodes() {
    const boxes = document.querySelectorAll(".js-account-filter");
    if (!boxes.length) return null;
    return [...boxes].filter((box) => box.checked).map((box) => box.value);
  }

  function getSelectedAssetClasses() {
    const boxes = document.querySelectorAll(".js-asset-filter");
    if (!boxes.length) return null;
    return [...boxes].filter((box) => box.checked).map((box) => box.value);
  }

  function rowMatchesAssetClass(row, selectedClasses) {
    if (selectedClasses === null) return true;
    if (!selectedClasses.length) return false;
    const assetClass = row.dataset.assetClass || "equity";
    return selectedClasses.includes(assetClass);
  }

  function rowFinancials(row, selectedCodes) {
    const key = row.dataset.holdingKey;
    const byAccount = key ? HOLDINGS_FINANCIALS[key] : null;

    if (byAccount) {
      const codes =
        selectedCodes === null
          ? Object.keys(byAccount)
          : (selectedCodes || []).filter((code) => byAccount[code]);

      let invested = 0;
      let current = 0;
      for (const code of codes) {
        const part = byAccount[code];
        if (!part) continue;
        invested += Number(part.invested) || 0;
        current += Number(part.current_value) || 0;
      }
      return { invested, current, pnl: current - invested };
    }

    const accountCodes = (row.dataset.accountCodes || "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    if (selectedCodes?.length && accountCodes.length) {
      const matched = accountCodes.some((code) => selectedCodes.includes(code));
      if (!matched) {
        return { invested: 0, current: 0, pnl: 0 };
      }
    }

    return {
      invested: Number(row.dataset.invested) || 0,
      current: Number(row.dataset.currentValue) || 0,
      pnl: Number(row.dataset.pnl) || 0,
    };
  }

  function formatInrSummary(value) {
    if (!Number.isFinite(value)) return "—";
    return `₹${value.toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function formatPctSummary(value) {
    if (!Number.isFinite(value)) return "0.00%";
    const sign = value > 0 ? "+" : "";
    return `${sign}${value.toFixed(2)}%`;
  }

  function formatPctCompact(value) {
    if (!Number.isFinite(value)) return "—";
    const sign = value > 0 ? "+" : "";
    return `${sign}${value.toFixed(1)}%`;
  }

  const SIGNAL_GROUP_ORDER = ["B+", "B", "H", "S", "S+", "Unrated"];

  function parseAccountBreakdown(row) {
    const raw = row.dataset.accountBreakdown;
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function rowSliceMetrics(row, selectedCodes) {
    const fin = rowFinancials(row, selectedCodes);
    const breakdown = parseAccountBreakdown(row);

    if (breakdown?.length) {
      const codes =
        selectedCodes === null
          ? breakdown.map((part) => part.abbrev).filter(Boolean)
          : selectedCodes;
      let qty = 0;
      let invested = 0;
      for (const part of breakdown) {
        if (!codes.includes(part.abbrev)) continue;
        qty += Number(part.quantity) || 0;
        invested += Number(part.invested) || 0;
      }
      const pnl = fin.pnl;
      return {
        invested: fin.invested,
        current: fin.current,
        pnl,
        pnlPct: invested > 0 ? (pnl / invested) * 100 : 0,
        quantity: qty,
        avgPrice: qty > 0 ? invested / qty : null,
      };
    }

    const invested = fin.invested;
    const pnl = fin.pnl;
    return {
      invested,
      current: fin.current,
      pnl,
      pnlPct: invested > 0 ? (pnl / invested) * 100 : 0,
      quantity: Number(row.dataset.quantity) || 0,
      avgPrice: Number(row.dataset.avgPrice) || null,
    };
  }

  function rowMatchesFilters(row, selectedCodes, selectedAssets, query) {
    if (!rowMatchesAccounts(row, selectedCodes)) return false;
    if (!rowMatchesAssetClass(row, selectedAssets)) return false;
    const haystack = (row.dataset.search || row.dataset.symbol || "").toLowerCase();
    if (query && !haystack.includes(query)) return false;
    const fin = rowFinancials(row, selectedCodes);
    return fin.current > 0 || fin.invested > 0;
  }

  function updateRowDisplayValues(row, metrics, portfolioTotal) {
    const valueCell = row.querySelector(".col-value");
    const pnlCell = row.querySelector(".col-pnl");
    const weightCell = row.querySelector(".col-weight");
    const qtyCell = row.querySelector(".col-qty");
    const avgCell = row.querySelector(".col-avg");

    if (valueCell) valueCell.textContent = formatInrWhole(metrics.current);
    if (pnlCell) {
      pnlCell.classList.remove("positive", "negative", "neutral");
      pnlCell.classList.add(
        metrics.pnl > 0 ? "positive" : metrics.pnl < 0 ? "negative" : "neutral"
      );
      pnlCell.innerHTML = `${formatInrWhole(metrics.pnl)}<span class="cell-sub">${formatPctCompact(metrics.pnlPct)}</span>`;
    }
    if (weightCell && portfolioTotal > 0) {
      weightCell.textContent = `${((metrics.current / portfolioTotal) * 100).toFixed(1)}%`;
    }
    if (qtyCell && metrics.quantity != null) {
      qtyCell.textContent = String(Math.round(metrics.quantity));
    }
    if (avgCell && metrics.avgPrice != null) {
      avgCell.textContent = formatInrWhole(metrics.avgPrice);
    }
  }

  function computeFilteredTotals(query, selectedCodes, selectedAssets) {
    let totalInvested = 0;
    let totalCurrent = 0;
    let visibleRows = 0;

    document.querySelectorAll(".holding-row").forEach((row) => {
      if (!rowMatchesFilters(row, selectedCodes, selectedAssets, query)) return;
      const metrics = rowSliceMetrics(row, selectedCodes);
      totalInvested += metrics.invested;
      totalCurrent += metrics.current;
      visibleRows += 1;
    });

    return {
      totalInvested,
      totalCurrent,
      totalPnl: totalCurrent - totalInvested,
      visibleRows,
    };
  }

  function sortGroupTotalsForChart(totals, groupBy) {
    if (groupBy === "signal") {
      return [...totals].sort(
        (a, b) =>
          SIGNAL_GROUP_ORDER.indexOf(a.label) - SIGNAL_GROUP_ORDER.indexOf(b.label)
      );
    }
    return [...totals].sort((a, b) => b.value - a.value);
  }

  function updateGroupedViewFromFilters(query, selectedCodes, selectedAssets, portfolioTotal) {
    const groupsRoot = document.getElementById("holdings-groups");
    if (!groupsRoot) return;

    const groupBy = groupsRoot.dataset.groupBy || "";
    const groupTotals = [];
    let activeGroups = 0;

    document.querySelectorAll(".holdings-group").forEach((groupEl) => {
      let groupValue = 0;
      let groupInvested = 0;
      let groupCount = 0;

      groupEl.querySelectorAll("tbody .holding-row").forEach((row) => {
        if (!rowMatchesFilters(row, selectedCodes, selectedAssets, query)) return;
        const metrics = rowSliceMetrics(row, selectedCodes);
        groupValue += metrics.current;
        groupInvested += metrics.invested;
        groupCount += 1;
      });

      const groupPnl = groupValue - groupInvested;
      const groupPnlPct = groupInvested > 0 ? (groupPnl / groupInvested) * 100 : 0;
      const groupPct =
        portfolioTotal > 0 ? Math.min(100, (groupValue / portfolioTotal) * 100) : 0;

      const countEl = groupEl.querySelector("[data-group-count]");
      const pctEl = groupEl.querySelector("[data-group-pct]");
      const fillEl = groupEl.querySelector("[data-group-fill]");
      const valueEl = groupEl.querySelector("[data-group-value]");
      const pnlEl = groupEl.querySelector("[data-group-pnl]");

      if (countEl) {
        countEl.textContent =
          groupCount === 1 ? "1 holding" : `${groupCount} holdings`;
      }
      if (pctEl) {
        pctEl.textContent = `${groupPct.toFixed(1)}% of portfolio`;
      }
      if (fillEl) {
        fillEl.style.width = `${groupPct.toFixed(1)}%`;
      }
      if (valueEl) {
        valueEl.textContent = formatInrSummary(groupValue);
      }
      if (pnlEl) {
        pnlEl.classList.remove("positive", "negative", "neutral");
        pnlEl.classList.add(
          groupPnl > 0 ? "positive" : groupPnl < 0 ? "negative" : "neutral"
        );
        pnlEl.innerHTML = `${formatInrSummary(groupPnl)}<span class="cell-sub">(${formatPctSummary(groupPnlPct)})</span>`;
      }

      const filtersEmpty =
        (selectedCodes && !selectedCodes.length) || (selectedAssets && !selectedAssets.length);
      const visible = !filtersEmpty && groupCount > 0;
      groupEl.hidden = !visible;
      if (visible) {
        activeGroups += 1;
        groupTotals.push({
          label: groupEl.dataset.groupLabel || "?",
          value: groupValue,
          pct: groupPct,
        });
      }
    });

    const badge = document.getElementById("grouped-overview-badge");
    if (badge) {
      badge.textContent = activeGroups === 1 ? "1 group" : `${activeGroups} groups`;
    }

    const overview = document.getElementById("portfolio-groups-chart");
    if (overview && groupTotals.length) {
      const ordered = sortGroupTotalsForChart(groupTotals, groupBy);
      renderOverviewBarChart(
        "portfolio-groups-chart",
        {
          labels: ordered.map((g) => g.label),
          values: ordered.map((g) => g.value),
          pcts: ordered.map((g) => g.pct),
        },
        "portfolio-overview"
      );
    }
  }

  function refreshAllRowDisplayValues(query, selectedCodes, selectedAssets, portfolioTotal) {
    document.querySelectorAll(".holding-row").forEach((row) => {
      if (!rowMatchesFilters(row, selectedCodes, selectedAssets, query)) return;
      const metrics = rowSliceMetrics(row, selectedCodes);
      updateRowDisplayValues(row, metrics, portfolioTotal);
    });
  }

  function updateFilteredPortfolioSummary() {
    const wrap = document.getElementById("portfolio-summary");
    const query = (document.getElementById("symbol-search")?.value || "").trim().toLowerCase();
    const selectedCodes = getSelectedAccountCodes();
    const selectedAssets = getSelectedAssetClasses();
    const totals = computeFilteredTotals(query, selectedCodes, selectedAssets);

    if (wrap) {
      const totalPnlPct = totals.totalInvested
        ? (totals.totalPnl / totals.totalInvested) * 100
        : 0;

      const valueEl = wrap.querySelector('[data-summary="value"]');
      const investedEl = wrap.querySelector('[data-summary="invested"]');
      const pnlEl = wrap.querySelector('[data-summary="pnl"]');
      const pnlPctEl = wrap.querySelector('[data-summary="pnl-pct"]');
      const pnlWrap = wrap.querySelector('[data-summary="pnl-wrap"]');

      if (valueEl) valueEl.textContent = formatInrSummary(totals.totalCurrent);
      if (investedEl) investedEl.textContent = formatInrSummary(totals.totalInvested);
      if (pnlEl) pnlEl.textContent = formatInrSummary(totals.totalPnl);
      if (pnlPctEl) pnlPctEl.textContent = `(${formatPctSummary(totalPnlPct)})`;
      if (pnlWrap) {
        pnlWrap.classList.remove("positive", "negative", "neutral");
        pnlWrap.classList.add(
          totals.totalPnl > 0 ? "positive" : totals.totalPnl < 0 ? "negative" : "neutral"
        );
      }
    }

    const meta = document.getElementById("holdings-filtered-meta");
    if (meta) {
      meta.textContent =
        totals.visibleRows === 1
          ? "1 position (filtered)"
          : `${totals.visibleRows} positions (filtered)`;
    }
  }

  function rowMatchesAccounts(row, selectedCodes) {
    if (selectedCodes === null) return true;
    if (!selectedCodes.length) return false;
    const codes = (row.dataset.accountCodes || "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    if (!codes.length) return true;
    return codes.some((code) => selectedCodes.includes(code));
  }

  function matchingRowsForTable(
    table,
    query,
    selectedCodes = getSelectedAccountCodes(),
    selectedAssets = getSelectedAssetClasses()
  ) {
    return [...table.querySelectorAll("tbody .holding-row")].filter((row) =>
      rowMatchesFilters(row, selectedCodes, selectedAssets, query)
    );
  }

  function collapseDetailRow(table, row) {
    const key = row.dataset.holdingKey;
    if (!key) return;
    const detail = table.querySelector(`.holding-detail-row[data-detail-for="${key}"]`);
    if (!detail) return;
    detail.hidden = true;
    const btn = row.querySelector(".row-expander");
    if (btn) {
      btn.setAttribute("aria-expanded", "false");
      btn.textContent = "▸";
    }
  }

  function renderPaginationBar(nav, { page, totalPages, totalMatching, pageSize, onPage }) {
    if (!nav) return;
    if (totalMatching <= pageSize) {
      nav.hidden = true;
      nav.innerHTML = "";
      return;
    }
    nav.hidden = false;
    const start = (page - 1) * pageSize + 1;
    const end = Math.min(page * pageSize, totalMatching);

    const prevDisabled = page <= 1;
    const nextDisabled = page >= totalPages;

    const pageButtons = [];
    const maxPageButtons = 7;
    let rangeStart = Math.max(1, page - Math.floor(maxPageButtons / 2));
    let rangeEnd = Math.min(totalPages, rangeStart + maxPageButtons - 1);
    rangeStart = Math.max(1, rangeEnd - maxPageButtons + 1);
    for (let p = rangeStart; p <= rangeEnd; p += 1) {
      const active = p === page ? " is-active" : "";
      pageButtons.push(
        `<button type="button" class="holdings-page-num${active}" data-page-num="${p}">${p}</button>`
      );
    }

    nav.innerHTML = `
      <span class="holdings-pagination-meta">
        Showing ${start}–${end} of ${totalMatching}
      </span>
      <div class="holdings-pagination-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-page-action="prev" ${
          prevDisabled ? "disabled" : ""
        }>Previous</button>
        <div class="holdings-pagination-pages">${pageButtons.join("")}</div>
        <button type="button" class="btn btn-ghost btn-sm" data-page-action="next" ${
          nextDisabled ? "disabled" : ""
        }>Next</button>
      </div>`;

    nav.querySelectorAll("[data-page-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.pageAction;
        if (action === "prev" && page > 1) onPage(page - 1);
        if (action === "next" && page < totalPages) onPage(page + 1);
      });
    });
    nav.querySelectorAll("[data-page-num]").forEach((button) => {
      button.addEventListener("click", () => {
        const num = Number(button.dataset.pageNum);
        if (num >= 1 && num <= totalPages) onPage(num);
      });
    });
  }

  function applyPaginationToTable(table, query, pageOverride, selectedCodes, selectedAssets) {
    const pageSize = pageSizeForTable(table);
    const key = tableKey(table);
    const codes = selectedCodes ?? getSelectedAccountCodes();
    const assets = selectedAssets ?? getSelectedAssetClasses();
    const matching = matchingRowsForTable(table, query, codes, assets);
    const totalPages = Math.max(1, Math.ceil(matching.length / pageSize));
    let page = pageOverride ?? holdingsPageByTable.get(key) ?? 1;
    if (page > totalPages) page = totalPages;
    if (page < 1) page = 1;
    holdingsPageByTable.set(key, page);

    const start = (page - 1) * pageSize;
    const end = start + pageSize;
    const pageKeys = new Set(
      matching.slice(start, end).map((row) => row.dataset.holdingKey).filter(Boolean)
    );

    table.querySelectorAll("tbody .holding-row").forEach((row) => {
      const haystack = (row.dataset.search || row.dataset.symbol || "").toLowerCase();
      const searchMatch = !query || haystack.includes(query);
      const accountMatch = rowMatchesAccounts(row, codes);
      const assetMatch = rowMatchesAssetClass(row, assets);
      const pageMatch = pageKeys.has(row.dataset.holdingKey);
      const visible = searchMatch && accountMatch && assetMatch && pageMatch;
      row.hidden = !visible;
      if (!visible) collapseDetailRow(table, row);
    });

    table.querySelectorAll("tbody .holding-detail-row").forEach((detail) => {
      const parentKey = detail.dataset.detailFor;
      const parentRow = table.querySelector(`.holding-row[data-holding-key="${parentKey}"]`);
      if (parentRow?.hidden) detail.hidden = true;
    });

    let nav =
      table.id === "holdings-table"
        ? document.getElementById("holdings-pagination")
        : table.parentElement?.querySelector(".holdings-pagination-nested");

    renderPaginationBar(nav, {
      page,
      totalPages,
      totalMatching: matching.length,
      pageSize,
      onPage: (nextPage) => {
        holdingsPageByTable.set(key, nextPage);
        applyPaginationToTable(table, query, nextPage, codes, assets);
        table.scrollIntoView({ behavior: "smooth", block: "nearest" });
      },
    });

    return matching.length;
  }

  function resetPaginationForFilterChange() {
    holdingsPageByTable.clear();
  }

  function applySymbolSearch() {
    const input = document.getElementById("symbol-search");
    const noMatch = document.getElementById("holdings-no-match");
    const query = (input?.value || "").trim().toLowerCase();
    const selectedCodes = getSelectedAccountCodes();
    const selectedAssets = getSelectedAssetClasses();

    const totals = computeFilteredTotals(query, selectedCodes, selectedAssets);
    refreshAllRowDisplayValues(query, selectedCodes, selectedAssets, totals.totalCurrent);
    updateGroupedViewFromFilters(query, selectedCodes, selectedAssets, totals.totalCurrent);

    let visibleCount = 0;

    const flatTable = document.getElementById("holdings-table");
    if (flatTable) {
      visibleCount += applyPaginationToTable(flatTable, query);
    }

    document.querySelectorAll(".holdings-group").forEach((group) => {
      if (group.hidden) return;
      const table = group.querySelector(".holdings-table-nested");
      if (!table) return;
      visibleCount += applyPaginationToTable(table, query);
    });

    if (noMatch) {
      const totalRows =
        (flatTable?.querySelectorAll(".holding-row").length || 0) +
        [...document.querySelectorAll(".holdings-group .holding-row")].length;
      noMatch.hidden = visibleCount > 0 || totalRows === 0;
      if (visibleCount === 0 && totalRows > 0) {
        const cell = noMatch.querySelector("td");
        const text =
          selectedCodes && !selectedCodes.length
            ? "Select at least one account."
            : selectedAssets && !selectedAssets.length
              ? "Select Equity and/or Mutual funds."
              : "No holdings match your filters.";
        if (cell) cell.textContent = text;
        else noMatch.textContent = text;
      }
    }

    updateFilteredPortfolioSummary();
  }

  function filterBoxesForGroup(group) {
    const cls = group === "account" ? ".js-account-filter" : ".js-asset-filter";
    return [...document.querySelectorAll(cls)];
  }

  function selectAllFilterGroup(group) {
    filterBoxesForGroup(group).forEach((box) => {
      box.checked = true;
    });
  }

  function clearAllFilterGroup(group) {
    filterBoxesForGroup(group).forEach((box) => {
      box.checked = false;
    });
  }

  function initFilterChipGroup(group) {
    const boxes = filterBoxesForGroup(group);
    if (!boxes.length) return;

    document.querySelectorAll(`.js-filter-select-all[data-filter-group="${group}"]`).forEach((btn) => {
      btn.addEventListener("click", () => {
        const allChecked = boxes.every((box) => box.checked);
        if (allChecked) clearAllFilterGroup(group);
        else selectAllFilterGroup(group);
        resetPaginationForFilterChange();
        applySymbolSearch();
      });
    });

    boxes.forEach((box) => {
      box.addEventListener("pointerdown", () => {
        box.dataset.wasChecked = box.checked ? "1" : "0";
        box.dataset.checkedCountBefore = String(boxes.filter((item) => item.checked).length);
      });

      box.addEventListener("change", () => {
        resetPaginationForFilterChange();
        applySymbolSearch();
      });

      box.addEventListener("click", (event) => {
        const wasChecked = box.dataset.wasChecked === "1";
        const checkedCountBefore = Number(box.dataset.checkedCountBefore || "0");
        const allCheckedBefore = checkedCountBefore === boxes.length;
        const singleCheckedBefore = checkedCountBefore === 1 && wasChecked;

        if (allCheckedBefore && wasChecked) {
          event.preventDefault();
          boxes.forEach((item) => {
            item.checked = item === box;
          });
          resetPaginationForFilterChange();
          applySymbolSearch();
          return;
        }

        if (singleCheckedBefore) {
          event.preventDefault();
          selectAllFilterGroup(group);
          resetPaginationForFilterChange();
          applySymbolSearch();
        }
      });
    });
  }

  function initPortfolioFilters() {
    initFilterChipGroup("account");
    initFilterChipGroup("asset");
  }

  function updateSearchSuggestions() {
    const input = document.getElementById("symbol-search");
    const list = document.getElementById("symbol-suggestions");
    if (!input || !list) return;

    const query = input.value.trim().toLowerCase();
    if (!query) {
      list.hidden = true;
      list.innerHTML = "";
      return;
    }

    const matches = getSymbolList()
      .filter((symbol) => symbol.toLowerCase().includes(query))
      .slice(0, 8);

    if (matches.length === 0) {
      list.hidden = true;
      list.innerHTML = "";
      return;
    }

    list.innerHTML = matches
      .map(
        (symbol) =>
          `<li><button type="button" data-symbol="${symbol}">${symbol}</button></li>`
      )
      .join("");
    list.hidden = false;

    list.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.dataset.symbol || "";
        list.hidden = true;
        applySymbolSearch();
      });
    });
  }

  function scheduleSearch() {
    updateSearchSuggestions();
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
      resetPaginationForFilterChange();
      applySymbolSearch();
    }, 120);
  }

  function parseChartData(canvas) {
    const raw = canvas?.dataset?.chart;
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function chartSegmentPcts(chartData) {
    const total = chartData.values.reduce((sum, value) => sum + Number(value || 0), 0);
    if (chartData.pcts?.length === chartData.values.length) {
      return chartData.pcts.map((pct) => Number(pct));
    }
    return chartData.values.map((value) =>
      total > 0 ? (Number(value || 0) / total) * 100 : 0
    );
  }

  function renderDoughnutChart(canvasId, chartData, instanceKey, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart || !chartData?.values?.length) return;

    if (chartInstances.has(instanceKey)) {
      chartInstances.get(instanceKey).destroy();
    }

    const showPercentages = Boolean(options.showPercentages);
    const pcts = chartSegmentPcts(chartData);
    const legendLabels = showPercentages
      ? chartData.labels.map((label, index) => `${label} (${pcts[index].toFixed(1)}%)`)
      : chartData.labels;

    const chart = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: legendLabels,
        datasets: [
          {
            data: chartData.values,
            backgroundColor: chartData.labels.map(
              (_, i) => PIE_COLORS[i % PIE_COLORS.length]
            ),
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "right",
            labels: { color: "#94a3b8", boxWidth: 10, font: { size: 10 } },
          },
          tooltip: showPercentages
            ? {
                callbacks: {
                  label(context) {
                    const value = context.parsed ?? 0;
                    const pct = pcts[context.dataIndex] ?? 0;
                    return `${chartData.labels[context.dataIndex]}: ${formatInr(value)} (${pct.toFixed(1)}%)`;
                  },
                },
              }
            : {},
        },
      },
    });

    chartInstances.set(instanceKey, chart);
  }

  function renderOverviewBarChart(canvasId, chartData, instanceKey) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart || !chartData?.values?.length) return;

    if (chartInstances.has(instanceKey)) {
      chartInstances.get(instanceKey).destroy();
    }

    const pcts = chartSegmentPcts(chartData);
    const labels = chartData.labels.map(
      (label, index) => `${label} · ${pcts[index].toFixed(1)}%`
    );

    const chart = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            data: chartData.values,
            backgroundColor: chartData.labels.map((_, i) => PIE_COLORS[i % PIE_COLORS.length]),
            borderRadius: 4,
            barThickness: 14,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(context) {
                const idx = context.dataIndex;
                const value = context.parsed?.x ?? 0;
                return `${chartData.labels[idx]}: ${formatInrWhole(value)} (${pcts[idx].toFixed(1)}%)`;
              },
            },
          },
        },
        scales: {
          x: {
            ticks: {
              color: "#94a3b8",
              callback: (value) => formatInrWhole(value),
            },
            grid: { color: "rgba(148, 163, 184, 0.12)" },
          },
          y: {
            ticks: { color: "#e2e8f0", font: { size: 11 } },
            grid: { display: false },
          },
        },
      },
    });

    chartInstances.set(instanceKey, chart);
  }

  function initGroupCharts() {
    const overview = document.getElementById("portfolio-groups-chart");
    if (!overview) return;

    const data = parseChartData(overview);
    if (!data) return;

    const kind = overview.dataset.chartKind || "bar";
    if (kind === "bar") {
      renderOverviewBarChart("portfolio-groups-chart", data, "portfolio-overview");
    } else {
      renderDoughnutChart("portfolio-groups-chart", data, "portfolio-overview", {
        showPercentages: true,
      });
    }
  }

  function toggleGroupPanel(button) {
    const group = button.closest(".holdings-group");
    const panel = group?.querySelector(".group-holdings-panel");
    if (!panel) return;

    const expanded = button.getAttribute("aria-expanded") === "true";
    if (expanded) {
      button.setAttribute("aria-expanded", "false");
      button.textContent = "▸";
      panel.hidden = true;
      return;
    }

    button.setAttribute("aria-expanded", "true");
    button.textContent = "▾";
    panel.hidden = false;

    const table = panel.querySelector(".holdings-table-nested");
    if (table) {
      const input = document.getElementById("symbol-search");
      const query = (input?.value || "").trim().toLowerCase();
      applyPaginationToTable(table, query);
      initColumnResize();
    }
  }

  function initGroupExpanders() {
    document.querySelectorAll(".group-expander").forEach((button) => {
      button.addEventListener("click", () => toggleGroupPanel(button));
    });
  }

  function formatChangePct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    const num = Number(value);
    const sign = num > 0 ? "+" : "";
    return `${sign}${num.toFixed(1)}%`;
  }

  function changeClass(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "neutral";
    }
    const num = Number(value);
    if (num > 0) return "positive";
    if (num < 0) return "negative";
    return "neutral";
  }

  function renderResultsTable(results) {
    if (!results || results.length === 0) {
      return '<p class="detail-empty">Recent quarterly results not available on Yahoo for this symbol.</p>';
    }

    const hasFinancials = results.some(
      (row) => row.revenue_cr !== undefined || row.net_income_cr !== undefined
    );
    if (hasFinancials) {
      const body = results
        .map(
          (row) => `<tr>
            <td>${row.period}</td>
            <td class="text-right">${row.revenue_cr ?? "—"}</td>
            <td class="text-right ${changeClass(row.revenue_qoq_pct)}">${formatChangePct(row.revenue_qoq_pct)}</td>
            <td class="text-right ${changeClass(row.revenue_yoy_pct)}">${formatChangePct(row.revenue_yoy_pct)}</td>
            <td class="text-right">${row.net_income_cr ?? "—"}</td>
            <td class="text-right ${changeClass(row.net_income_qoq_pct)}">${formatChangePct(row.net_income_qoq_pct)}</td>
            <td class="text-right ${changeClass(row.net_income_yoy_pct)}">${formatChangePct(row.net_income_yoy_pct)}</td>
          </tr>`
        )
        .join("");
      return `<div class="detail-table-wrap"><table class="detail-table detail-table-results">
        <thead><tr>
          <th>Period</th>
          <th class="text-right">Revenue (₹ Cr)</th>
          <th class="text-right">QoQ</th>
          <th class="text-right">YoY</th>
          <th class="text-right">Net income (₹ Cr)</th>
          <th class="text-right">QoQ</th>
          <th class="text-right">YoY</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table></div>`;
    }

    const body = results
      .map(
        (row) => `<tr>
          <td>${row.period}</td>
          <td class="text-right">${row.eps_estimate ?? "—"}</td>
          <td class="text-right">${row.reported_eps ?? "—"}</td>
          <td class="text-right ${changeClass(row.eps_qoq_pct)}">${formatChangePct(row.eps_qoq_pct)}</td>
          <td class="text-right ${changeClass(row.eps_yoy_pct)}">${formatChangePct(row.eps_yoy_pct)}</td>
          <td class="text-right">${row.surprise_pct ?? "—"}</td>
        </tr>`
      )
      .join("");
    return `<div class="detail-table-wrap"><table class="detail-table detail-table-results">
      <thead><tr>
        <th>Period</th>
        <th class="text-right">EPS est.</th>
        <th class="text-right">Reported EPS</th>
        <th class="text-right">EPS QoQ</th>
        <th class="text-right">EPS YoY</th>
        <th class="text-right">Surprise %</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
  }

  function escapeHtml(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  const SIGNAL_SHORT = {
    "Strong buy": "B+",
    Buy: "B",
    Hold: "H",
    Sell: "S",
    "Strong sell": "S+",
  };

  function signalShortLabel(label) {
    return SIGNAL_SHORT[label] || (label ? label.slice(0, 2) : "");
  }

  function signalDisplayFull(label) {
    const short = signalShortLabel(label);
    return short ? `${label} (${short})` : label;
  }

  function renderRatingBadge(rating, { full = false } = {}) {
    if (!rating?.label) {
      return '<span class="rating-badge rating-badge-compact rating-hold">—</span>';
    }
    const slug = rating.slug || "hold";
    const text = full ? signalDisplayFull(rating.label) : signalShortLabel(rating.label);
    const compactClass = full ? "" : " rating-badge-compact";
    return `<span class="rating-badge${compactClass} rating-${slug}" title="${rating.label}">${text}</span>`;
  }

  function renderRatingReasons(rating) {
    if (!rating?.reasons?.length) {
      return '<p class="detail-empty">No signal rationale available for this symbol.</p>';
    }
    const items = rating.reasons
      .map((reason) => `<li>${reason}</li>`)
      .join("");
    return `<ul class="rating-reasons">${items}</ul>`;
  }

  function renderSignalEventsList(events) {
    if (!events?.length) return "";
    const items = events
      .map((event) => {
        const label = escapeHtml(event.label || "Event");
        const date = escapeHtml(event.date || "");
        return `<li><strong>${label}</strong>${date ? ` · ${date}` : ""}</li>`;
      })
      .join("");
    return `
      <div class="signal-events-block">
        <h6 class="signal-col-subtitle">Upcoming events</h6>
        <ul class="signal-events">${items}</ul>
      </div>`;
  }

  function renderSignalNewsList(news) {
    if (!news?.length) {
      return '<p class="detail-empty">No recent news available for this symbol.</p>';
    }
    const items = news
      .map((item) => {
        const title = escapeHtml(item.title || "News");
        const meta = [item.date, item.publisher].filter(Boolean).map(escapeHtml).join(" · ");
        const summary = item.summary ? `<p class="signal-news-summary">${escapeHtml(item.summary)}</p>` : "";
        const titleHtml = item.url
          ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${title}</a>`
          : title;
        return `<li class="signal-news-item">
          <div class="signal-news-title">${titleHtml}</div>
          ${meta ? `<span class="signal-news-meta">${meta}</span>` : ""}
          ${summary}
        </li>`;
      })
      .join("");
    return `<ul class="signal-news">${items}</ul>`;
  }

  function renderSignalContextPanel(rating, events, news) {
    const reasons = renderRatingReasons(rating);
    const eventsHtml = renderSignalEventsList(events);
    const newsHtml = renderSignalNewsList(news);
    const hasSignal = Boolean(rating?.reasons?.length || eventsHtml);
    const hasNews = Boolean(news?.length);

    if (!hasSignal && !hasNews) {
      return `
        <section class="signal-context-panel rating-reasons-section">
          <p class="detail-empty">No signal rationale or recent news available for this symbol.</p>
        </section>`;
    }

    return `
      <section class="signal-context-panel rating-reasons-section">
        <div class="signal-context-grid">
          <div class="signal-context-col signal-context-col-reasons">
            <h5 class="signal-col-title">Why this signal?</h5>
            <div class="signal-col-scroll">
              ${hasSignal ? `${reasons}${eventsHtml}` : '<p class="detail-empty">No signal rationale available.</p>'}
            </div>
          </div>
          <div class="signal-context-col signal-context-col-news">
            <h5 class="signal-col-title">Recent news</h5>
            <div class="signal-col-scroll">
              ${newsHtml}
            </div>
          </div>
        </div>
      </section>`;
  }

  function renderForecast(forecast) {
    if (!forecast || !forecast.projected_value_1y) {
      return '<p class="detail-empty">1Y forecast unavailable — no analyst target or price history.</p>';
    }

    const upsideClass =
      forecast.upside_pct > 0 ? "positive" : forecast.upside_pct < 0 ? "negative" : "neutral";

    return `<div class="forecast-grid">
      <div class="forecast-card">
        <div class="forecast-label">Current value</div>
        <div class="forecast-value">${formatInr(forecast.current_value)}</div>
      </div>
      <div class="forecast-card highlight">
        <div class="forecast-label">Projected value (1Y)</div>
        <div class="forecast-value">${formatInr(forecast.projected_value_1y)}</div>
      </div>
      <div class="forecast-card">
        <div class="forecast-label">Target price</div>
        <div class="forecast-value">${formatInr(forecast.target_price)}</div>
      </div>
      <div class="forecast-card">
        <div class="forecast-label">Upside</div>
        <div class="forecast-value ${upsideClass}">${formatPct(forecast.upside_pct)}</div>
      </div>
    </div>
    <p class="detail-note">${forecast.note || ""}</p>`;
  }

  const CHART_RANGES = {
    "1y": { label: "1Y", days: 252, maxTicks: 6 },
    "3y": { label: "3Y", days: 252 * 3, maxTicks: 8 },
    "5y": { label: "5Y", days: 252 * 5, maxTicks: 10 },
    "10y": { label: "10Y", days: 252 * 10, maxTicks: 12 },
  };

  /** Same trailing P/E axis for every symbol (not auto-scaled per stock). */
  const PE_AXIS = { min: 0, max: 100, step: 10 };

  function sliceChartByRange(fullChart, rangeKey) {
    const config = CHART_RANGES[rangeKey] || CHART_RANGES["1y"];
    const len = fullChart.prices.length;
    const start = Math.max(0, len - config.days);
    return {
      labels: fullChart.labels.slice(start),
      prices: fullChart.prices.slice(start),
      dma200: fullChart.dma200?.slice(start) ?? [],
      pe_ratio: fullChart.pe_ratio?.slice(start) ?? [],
      maxTicks: config.maxTicks,
    };
  }

  function chartHasPe(peSeries) {
    return peSeries?.some((value) => value !== null && value !== undefined && !Number.isNaN(Number(value)));
  }

  function renderLineChart(canvasId, chartData, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart || !chartData?.prices?.length) return;

    const maxTicks = options.maxTicks ?? 6;

    if (chartInstances.has(canvasId)) {
      chartInstances.get(canvasId).destroy();
    }

    const datasets = [
      {
        label: "Price",
        data: chartData.prices,
        borderColor: "#60a5fa",
        backgroundColor: "rgba(96, 165, 250, 0.12)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        borderWidth: 2,
        yAxisID: "yPrice",
      },
    ];

    if (chartData.dma200?.some((v) => v !== null && v !== undefined)) {
      datasets.push({
        label: "200 DMA",
        data: chartData.dma200,
        borderColor: "#fbbf24",
        backgroundColor: "transparent",
        fill: false,
        tension: 0.25,
        pointRadius: 0,
        borderWidth: 1.5,
        spanGaps: true,
        yAxisID: "yPrice",
      });
    }

    const showPe = chartHasPe(chartData.pe_ratio);
    if (showPe) {
      datasets.push({
        label: "Trailing P/E",
        data: chartData.pe_ratio,
        borderColor: "#fb7185",
        backgroundColor: "transparent",
        fill: false,
        tension: 0.15,
        pointRadius: 0,
        borderWidth: 2,
        borderDash: [5, 4],
        spanGaps: true,
        yAxisID: "yPe",
      });
    }

    datasets.forEach((dataset) => {
      if (!dataset.yAxisID) dataset.yAxisID = "yPrice";
    });

    const scales = {
      x: {
        ticks: { maxTicksLimit: maxTicks, color: "#94a3b8", maxRotation: 0 },
        grid: { color: "rgba(148, 163, 184, 0.12)" },
      },
      yPrice: {
        type: "linear",
        position: "left",
        ticks: { color: "#94a3b8" },
        grid: { color: "rgba(148, 163, 184, 0.12)" },
        title: { display: true, text: "Price (₹)", color: "#94a3b8", font: { size: 10 } },
      },
    };

    if (showPe) {
      scales.yPe = {
        type: "linear",
        position: "right",
        min: PE_AXIS.min,
        max: PE_AXIS.max,
        grace: 0,
        ticks: {
          color: "#fb7185",
          stepSize: PE_AXIS.step,
          callback: (value) => (Number.isInteger(value) ? value : ""),
        },
        grid: { drawOnChartArea: false },
        title: { display: true, text: "Trailing P/E", color: "#fb7185", font: { size: 10 } },
      };
    }

    const chart = new Chart(canvas, {
      type: "line",
      data: { labels: chartData.labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: datasets.length > 1,
            labels: { color: "#94a3b8", boxWidth: 12 },
          },
          tooltip: {
            callbacks: {
              label(context) {
                const label = context.dataset.label || "";
                const raw = context.parsed.y;
                if (raw === null || raw === undefined) return `${label}: —`;
                if (context.dataset.yAxisID === "yPe") {
                  return `${label}: ${Number(raw).toFixed(1)}`;
                }
                return `${label}: ${formatInr(raw)}`;
              },
            },
          },
        },
        scales,
      },
    });

    chartInstances.set(canvasId, chart);
  }

  function initChartRangeControls(section, canvasId, fullChart) {
    const buttons = section.querySelectorAll(".chart-range-btn");
    if (!buttons.length || !fullChart?.prices?.length) return;

    const defaultRange = fullChart.default_range || "1y";

    const applyRange = (rangeKey) => {
      const sliced = sliceChartByRange(fullChart, rangeKey);
      renderLineChart(canvasId, sliced, { maxTicks: sliced.maxTicks });
      buttons.forEach((btn) => {
        const active = btn.dataset.range === rangeKey;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
    };

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        applyRange(button.dataset.range || "1y");
      });
    });

    applyRange(defaultRange);
  }

  function renderChartRangeButtons() {
    return Object.entries(CHART_RANGES)
      .map(
        ([key, config]) =>
          `<button type="button" class="chart-range-btn${key === "1y" ? " is-active" : ""}" data-range="${key}" aria-pressed="${key === "1y" ? "true" : "false"}">${config.label}</button>`
      )
      .join("");
  }

  async function loadInsights(detailRow) {
    const panel = detailRow.querySelector(".holding-detail-panel");
    const asyncSlot = panel?.querySelector(".detail-async");
    if (!panel || !asyncSlot || asyncSlot.dataset.loaded === "true") return;

    const symbol = detailRow.dataset.symbol;
    const exchange = detailRow.dataset.exchange || "NSE";
    const quantity = detailRow.dataset.quantity || "0";
    const lastPrice = detailRow.dataset.lastPrice || "0";
    const canvasId = `chart-${detailRow.dataset.detailFor.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

    asyncSlot.classList.add("is-loading");
    asyncSlot.innerHTML = `<div class="detail-loading">Loading chart &amp; news for ${symbol}…</div>`;

    try {
      const params = new URLSearchParams({
        exchange,
        quantity,
        last_price: lastPrice,
      });
      const response = await fetch(
        `/api/portfolio/insights/${encodeURIComponent(symbol)}?${params}`
      );
      if (!response.ok) {
        let detail = "";
        try {
          const err = await response.json();
          detail = err.detail ? `: ${err.detail}` : "";
        } catch {
          /* ignore */
        }
        throw new Error(`Failed to load insights${detail}`);
      }
      const data = await response.json();

      asyncSlot.classList.remove("is-loading");

      if (!data.available && !data.yahoo_ticker) {
        asyncSlot.innerHTML = `<p class="detail-empty">${data.message || "Chart and news unavailable."}</p>`;
        asyncSlot.dataset.loaded = "true";
        return;
      }

      const rating = data.forecast?.rating;
      const hasChart = Boolean(data.chart?.prices?.length);
      const chartBody = hasChart
        ? `<div class="chart-wrap"><canvas id="${canvasId}"></canvas></div>`
        : `<p class="detail-empty chart-empty-msg">Price history unavailable from Yahoo for this symbol.</p>`;

      asyncSlot.innerHTML = `
        <div class="detail-header detail-header-row">
          <div>
            <h4>${data.name || symbol}</h4>
            <span class="detail-sub">${data.yahoo_ticker || ""}</span>
          </div>
          ${renderRatingBadge(rating, { full: true })}
        </div>
        <div class="detail-grid detail-grid-insights">
          <section class="detail-section chart-section detail-section-full" data-chart-canvas="${canvasId}">
            <div class="chart-section-header">
              <h5>Price · 200 DMA · Trailing P/E (right axis)</h5>
              <div class="chart-range-toggle" role="group" aria-label="Chart time range">
                ${renderChartRangeButtons()}
              </div>
            </div>
            ${chartBody}
          </section>
          <section class="detail-section detail-section-half">
            <h5>Recent results</h5>
            ${renderResultsTable(data.results)}
          </section>
          <section class="detail-section detail-section-half">
            <h5>1Y forecast (your holding)</h5>
            ${renderForecast(data.forecast)}
          </section>
          ${renderSignalContextPanel(rating, data.events, data.news)}
        </div>`;

      asyncSlot.dataset.loaded = "true";
      if (hasChart) {
        const chartSection = asyncSlot.querySelector(".chart-section");
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            if (chartSection) initChartRangeControls(chartSection, canvasId, data.chart);
          });
        });
      }
    } catch (error) {
      asyncSlot.classList.remove("is-loading");
      const message = error instanceof Error ? error.message : "Could not load insights.";
      asyncSlot.innerHTML = `<p class="detail-empty">${message} Try again later.</p>`;
    }
  }

  function toggleRow(button) {
    const row = button.closest(".holding-row");
    if (!row) return;

    const key = row.dataset.holdingKey;
    const detailRow = document.querySelector(`.holding-detail-row[data-detail-for="${key}"]`);
    if (!detailRow) return;

    const expanded = button.getAttribute("aria-expanded") === "true";
    if (expanded) {
      button.setAttribute("aria-expanded", "false");
      button.textContent = "▸";
      detailRow.hidden = true;
      return;
    }

    button.setAttribute("aria-expanded", "true");
    button.textContent = "▾";
    detailRow.hidden = false;
    loadInsights(detailRow);
  }

  function initSearch() {
    const searchInput = document.getElementById("symbol-search");
    if (!searchInput) return;

    searchInput.addEventListener("input", scheduleSearch);
    searchInput.addEventListener("focus", updateSearchSuggestions);

    searchInput.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        searchInput.value = "";
        document.getElementById("symbol-suggestions").hidden = true;
        scheduleSearch();
      }
    });

    document.addEventListener("click", (event) => {
      const list = document.getElementById("symbol-suggestions");
      const input = document.getElementById("symbol-search");
      if (!list || !input) return;
      if (!list.contains(event.target) && event.target !== input) {
        list.hidden = true;
      }
    });
  }

  const COL_WIDTHS_STORAGE_PREFIX = "holdings-col-widths:";

  function tableStorageKey(table) {
    const base = table.id || table.closest(".holdings-group")?.dataset?.groupLabel || "nested";
    const withAccount = table.querySelector("col[data-col-id='account']") ? "acct" : "noacct";
    return `${COL_WIDTHS_STORAGE_PREFIX}${base}:${withAccount}`;
  }

  function applyColumnWidth(table, colId, widthPx) {
    const col = table.querySelector(`colgroup col[data-col-id="${colId}"]`);
    if (!col) return;
    col.style.width = `${widthPx}px`;
  }

  function loadSavedColumnWidths(table) {
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(tableStorageKey(table)) || "{}");
    } catch {
      saved = {};
    }
    Object.entries(saved).forEach(([colId, width]) => {
      const px = Number(width);
      if (px > 24) applyColumnWidth(table, colId, px);
    });
  }

  function saveColumnWidths(table) {
    const widths = {};
    table.querySelectorAll("colgroup col[data-col-id]").forEach((col) => {
      const id = col.dataset.colId;
      const w = col.getBoundingClientRect().width;
      if (id && w > 24) widths[id] = Math.round(w);
    });
    try {
      localStorage.setItem(tableStorageKey(table), JSON.stringify(widths));
    } catch {
      /* ignore */
    }
  }

  function resetAllColumnWidths() {
    try {
      Object.keys(localStorage).forEach((key) => {
        if (key.startsWith(COL_WIDTHS_STORAGE_PREFIX)) {
          localStorage.removeItem(key);
        }
      });
    } catch {
      /* ignore */
    }
    window.location.reload();
  }

  function initColumnResizeReset() {
    document.getElementById("reset-col-widths")?.addEventListener("click", resetAllColumnWidths);
  }

  function initColumnResize() {
    document.querySelectorAll('.holdings-table[data-col-resizable="true"]').forEach((table) => {
      loadSavedColumnWidths(table);

      table.querySelectorAll("th[data-col-id] .col-resize-handle").forEach((handle) => {
        handle.addEventListener("mousedown", (event) => {
          event.preventDefault();
          event.stopPropagation();

          const th = handle.closest("th[data-col-id]");
          const colId = th?.dataset.colId;
          if (!colId) return;

          const col = table.querySelector(`colgroup col[data-col-id="${colId}"]`);
          if (!col) return;

          const startX = event.clientX;
          const startWidth = col.getBoundingClientRect().width || th.getBoundingClientRect().width;
          document.body.classList.add("col-resize-active");

          const onMove = (moveEvent) => {
            const next = Math.max(32, Math.min(480, startWidth + (moveEvent.clientX - startX)));
            applyColumnWidth(table, colId, next);
          };

          const onUp = () => {
            document.body.classList.remove("col-resize-active");
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            saveColumnWidths(table);
          };

          document.addEventListener("mousemove", onMove);
          document.addEventListener("mouseup", onUp);
        });
      });
    });
  }

  function initHoldingsTable() {
    initPageLoader();
    initSearch();
    initPortfolioFilters();
    initGroupBySelect();
    initGroupCharts();
    initGroupExpanders();
    initColumnResize();
    initColumnResizeReset();
    applySymbolSearch();
    updateFilteredPortfolioSummary();

    document.querySelectorAll(".row-expander").forEach((button) => {
      button.addEventListener("click", () => toggleRow(button));
    });

    hideLoader();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initHoldingsTable);
  } else {
    initHoldingsTable();
  }
})();
