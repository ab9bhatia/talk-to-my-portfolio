(function () {
  const dialog = document.getElementById("trade-order-dialog");
  if (!dialog) return;

  const form = document.getElementById("trade-order-form");
  const titleEl = document.getElementById("trade-dialog-title");
  const accountEl = document.getElementById("trade-account");
  const exchangeEl = document.getElementById("trade-exchange");
  const orderTypeEl = document.getElementById("trade-order-type");
  const qtyEl = document.getElementById("trade-quantity");
  const priceWrap = document.getElementById("trade-price-wrap");
  const priceEl = document.getElementById("trade-price");
  const confirmEl = document.getElementById("trade-confirm-check");
  const submitBtn = document.getElementById("trade-submit-btn");
  const errorEl = document.getElementById("trade-error");
  const successEl = document.getElementById("trade-success");
  const maxHint = document.getElementById("trade-max-hint");
  const sessionHint = document.getElementById("trade-session-hint");

  let state = {
    symbol: "",
    side: "BUY",
    accounts: [],
  };

  function showError(msg) {
    if (!errorEl) return;
    errorEl.textContent = msg || "";
    errorEl.hidden = !msg;
  }

  function showSuccess(msg) {
    if (!successEl) return;
    successEl.textContent = msg || "";
    successEl.hidden = !msg;
  }

  function updateSubmitEnabled() {
    if (!submitBtn || !confirmEl) return;
    const qty = parseInt(qtyEl?.value || "0", 10);
    submitBtn.disabled = !confirmEl.checked || qty < 1;
  }

  function selectedAccount() {
    const idx = parseInt(accountEl?.value || "0", 10);
    return state.accounts[idx] || null;
  }

  function fillAccounts() {
    if (!accountEl) return;
    accountEl.innerHTML = "";
    state.accounts.forEach((acc, i) => {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = `${acc.code} · ${acc.broker.toUpperCase()} (${acc.exchange}) — held ${acc.quantity}`;
      accountEl.appendChild(opt);
    });
    const acc = selectedAccount();
    if (acc && exchangeEl) exchangeEl.value = acc.exchange || "NSE";
    refreshMaxHint();
  }

  function refreshMaxHint() {
    const acc = selectedAccount();
    if (!maxHint || !acc) return;
    if (state.side === "SELL") {
      maxHint.textContent = `Max sell: ${acc.quantity} shares in ${acc.code}`;
      if (qtyEl && !qtyEl.value) qtyEl.placeholder = String(Math.floor(acc.quantity));
    } else {
      maxHint.textContent = "Delivery (CNC) buy";
      if (qtyEl) qtyEl.placeholder = "";
    }
  }

  async function refreshSessionHint() {
    if (!sessionHint) return;
    try {
      const res = await fetch("/api/portfolio/trading/status");
      const data = await res.json();
      const session = data.nse_session;
      if (!session) {
        sessionHint.hidden = true;
        return;
      }
      if (session.phase === "amo") {
        sessionHint.textContent = session.note || "NSE is closed — order will be sent as AMO. Use Limit with a price.";
        sessionHint.hidden = false;
        if (orderTypeEl && orderTypeEl.value === "MARKET") {
          orderTypeEl.value = "LIMIT";
          if (priceWrap) priceWrap.hidden = false;
        }
      } else if (session.phase === "maintenance") {
        sessionHint.textContent = session.note || "Orders unavailable during Zerodha maintenance (1:00–5:30 AM IST).";
        sessionHint.hidden = false;
      } else {
        sessionHint.hidden = true;
      }
    } catch {
      sessionHint.hidden = true;
    }
  }

  function openTradeDialog({ symbol, side, accounts }) {
    state.symbol = symbol;
    state.side = side;
    state.accounts = accounts || [];
    showError("");
    showSuccess("");

    if (!state.accounts.length) {
      showError("No tradable account for this holding (Zerodha/Groww Indian equity only).");
      dialog.showModal();
      return;
    }
    if (confirmEl) confirmEl.checked = false;
    if (qtyEl) qtyEl.value = state.side === "SELL" ? "" : "1";
    if (priceEl) priceEl.value = "";
    if (orderTypeEl) orderTypeEl.value = "MARKET";
    if (priceWrap) priceWrap.hidden = true;

    if (titleEl) {
      titleEl.textContent = `${side === "SELL" ? "Sell" : "Buy"} ${symbol}`;
    }
    fillAccounts();
    updateSubmitEnabled();
    dialog.showModal();
    refreshSessionHint();
  }

  function accountsFromButton(btn) {
    let accounts = [];
    try {
      accounts = JSON.parse(btn.dataset.tradeAccounts || "[]");
    } catch {
      accounts = [];
    }
    if (accounts.length) return accounts;
    const wrap = btn.closest(".holding-trade-actions");
    if (!wrap) return [];
    try {
      return JSON.parse(wrap.dataset.tradeAccounts || "[]");
    } catch {
      return [];
    }
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".js-trade-open");
    if (!btn) return;
    const wrap = btn.closest(".holding-trade-actions");
    openTradeDialog({
      symbol: btn.dataset.symbol || wrap?.dataset.tradeSymbol || "",
      side: btn.dataset.side || "BUY",
      accounts: accountsFromButton(btn),
    });
  });

  orderTypeEl?.addEventListener("change", () => {
    const isLimit = orderTypeEl.value === "LIMIT";
    if (priceWrap) priceWrap.hidden = !isLimit;
    updateSubmitEnabled();
  });

  accountEl?.addEventListener("change", () => {
    const acc = selectedAccount();
    if (acc && exchangeEl) exchangeEl.value = acc.exchange || "NSE";
    refreshMaxHint();
  });

  confirmEl?.addEventListener("change", updateSubmitEnabled);
  qtyEl?.addEventListener("input", updateSubmitEnabled);

  dialog.querySelectorAll(".trade-dialog-close").forEach((btn) => {
    btn.addEventListener("click", () => dialog.close());
  });

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const acc = selectedAccount();
    if (!acc) return;

    const quantity = parseInt(qtyEl?.value || "0", 10);
    if (quantity < 1) {
      showError("Enter a valid quantity.");
      return;
    }
    if (state.side === "SELL" && quantity > acc.quantity) {
      showError(`Cannot sell more than ${acc.quantity} shares in ${acc.code}.`);
      return;
    }

    showError("");
    showSuccess("");
    submitBtn.disabled = true;
    submitBtn.textContent = "Placing…";

    const body = {
      account_id: acc.account_id,
      symbol: state.symbol,
      exchange: exchangeEl?.value || acc.exchange,
      side: state.side,
      quantity,
      order_type: orderTypeEl?.value || "MARKET",
      price: orderTypeEl?.value === "LIMIT" ? parseFloat(priceEl?.value || "0") : null,
      confirmed: true,
    };

    try {
      const res = await fetch("/api/portfolio/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail;
        const msg = Array.isArray(detail)
          ? detail.map((d) => d.msg || d).join("; ")
          : detail || `Order failed (${res.status})`;
        throw new Error(msg);
      }
      const amoNote = data.amo ? " AMO — executes when the market opens." : "";
      showSuccess(
        `Order placed on ${data.account_code} (${data.broker}).${amoNote} Order id: ${data.order_id || "—"}. Refresh holdings to see updates.`
      );
      submitBtn.textContent = "Placed";
    } catch (err) {
      showError(err.message || "Order failed.");
      submitBtn.disabled = false;
      submitBtn.textContent = "Place order";
      updateSubmitEnabled();
    }
  });

  document.addEventListener("click", (e) => {
    const agentBtn = e.target.closest(".js-agent-trade");
    if (!agentBtn) return;
    const symbol = agentBtn.dataset.symbol || "";
    const side = agentBtn.dataset.side || "BUY";
    fetch("/api/portfolio/trading/status")
      .then((r) => r.json())
      .then((status) => {
        if (!status.enabled) {
          showError("Trading is disabled. Set TRADING_ENABLED=true in .env.");
          dialog.showModal();
          return;
        }
        const accounts = [];
        (status.zerodha_accounts || []).forEach((a) => {
          accounts.push({
            account_id: a.account_id,
            code: a.code,
            broker: "zerodha",
            exchange: "NSE",
            quantity: 0,
          });
        });
        (status.groww_accounts || []).forEach((a) => {
          accounts.push({
            account_id: a.account_id,
            code: a.code,
            broker: "groww",
            exchange: "NSE",
            quantity: 0,
          });
        });
        openTradeDialog({ symbol, side, accounts });
      })
      .catch(() => showError("Could not load trading accounts."));
  });
})();
