(() => {
  const dialog = document.getElementById("export-excel-dialog");
  const form = document.getElementById("export-excel-form");
  if (!dialog || !form) return;

  const openButtons = document.querySelectorAll(".js-export-excel-open");
  const closeBtn = document.getElementById("export-excel-close");
  const cancelBtn = document.getElementById("export-excel-cancel");
  const submitBtn = document.getElementById("export-excel-submit");
  const errorEl = document.getElementById("export-excel-error");
  const columnsGrid = document.getElementById("export-columns-grid");
  const selectAllBtn = document.getElementById("export-columns-all");
  const selectNoneBtn = document.getElementById("export-columns-none");
  const accountChips = document.getElementById("export-account-chips");
  const accountModeInputs = form.querySelectorAll('input[name="export_accounts_mode"]');

  let activeConfig = null;

  function columnInputs() {
    return columnsGrid ? [...columnsGrid.querySelectorAll('input[name="export_column"]')] : [];
  }

  function accountInputs() {
    return accountChips ? [...accountChips.querySelectorAll(".js-export-account")] : [];
  }

  function accountsMode() {
    const checked = form.querySelector('input[name="export_accounts_mode"]:checked');
    return checked?.value || "all";
  }

  function setAccountChipsVisible(visible) {
    if (!accountChips) return;
    accountChips.hidden = !visible;
  }

  function syncAccountModeUi() {
    const specific = accountsMode() === "specific";
    setAccountChipsVisible(specific);
    accountInputs().forEach((input) => {
      input.disabled = !specific;
    });
  }

  function setError(message) {
    if (!errorEl) return;
    if (message) {
      errorEl.textContent = message;
      errorEl.hidden = false;
    } else {
      errorEl.textContent = "";
      errorEl.hidden = true;
    }
  }

  function setBusy(busy) {
    if (submitBtn) {
      submitBtn.disabled = busy;
      submitBtn.textContent = busy ? "Preparing…" : "Download Excel";
    }
  }

  function readConfig(button) {
    return {
      apiUrl: button.getAttribute("data-export-api") || "/api/portfolio/export",
      sort: button.getAttribute("data-sort") || "value",
      order: button.getAttribute("data-order") || "desc",
      refresh: button.getAttribute("data-refresh") === "1",
      hasAccountFilter: button.getAttribute("data-export-accounts") === "1",
    };
  }

  function resetFormState() {
    columnInputs().forEach((input) => {
      input.checked = true;
    });
    accountModeInputs.forEach((input) => {
      input.checked = input.value === "all";
    });
    accountInputs().forEach((input) => {
      input.checked = true;
      input.disabled = true;
    });
    syncAccountModeUi();
  }

  function openModal(button) {
    activeConfig = readConfig(button);
    setError("");
    resetFormState();
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  function closeModal() {
    setError("");
    setBusy(false);
    if (dialog.open) dialog.close();
    else dialog.removeAttribute("open");
  }

  function selectedAccountCodes() {
    if (!activeConfig?.hasAccountFilter || accountsMode() !== "specific") {
      return [];
    }
    return accountInputs()
      .filter((input) => input.checked)
      .map((input) => input.value);
  }

  openButtons.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      openModal(btn);
    });
  });

  closeBtn?.addEventListener("click", closeModal);
  cancelBtn?.addEventListener("click", closeModal);

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeModal();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeModal();
  });

  accountModeInputs.forEach((input) => {
    input.addEventListener("change", syncAccountModeUi);
  });

  selectAllBtn?.addEventListener("click", () => {
    columnInputs().forEach((input) => {
      input.checked = true;
    });
  });

  selectNoneBtn?.addEventListener("click", () => {
    columnInputs().forEach((input) => {
      input.checked = false;
    });
  });

  function filenameFromDisposition(header) {
    if (!header) return "portfolio.xlsx";
    const match = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(header);
    if (!match) return "portfolio.xlsx";
    try {
      return decodeURIComponent(match[1].replace(/"/g, ""));
    } catch {
      return match[1].replace(/"/g, "");
    }
  }

  async function downloadExport() {
    if (!activeConfig) return;

    const columns = columnInputs()
      .filter((input) => input.checked)
      .map((input) => input.value);
    if (!columns.length) {
      setError("Select at least one column.");
      return;
    }

    const accounts = selectedAccountCodes();
    if (activeConfig.hasAccountFilter && accountsMode() === "specific" && !accounts.length) {
      setError("Select at least one account.");
      return;
    }

    setError("");
    setBusy(true);

    const payload = {
      columns,
      accounts,
      sort: activeConfig.sort,
      order: activeConfig.order,
      refresh: activeConfig.refresh,
    };

    try {
      const response = await fetch(activeConfig.apiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        let detail = `Export failed (${response.status})`;
        try {
          const body = await response.json();
          if (body?.detail) {
            detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
          }
        } catch {
          /* ignore */
        }
        setError(detail);
        return;
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filenameFromDisposition(response.headers.get("Content-Disposition"));
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      closeModal();
    } catch (err) {
      setError(err?.message || "Could not download export.");
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    downloadExport();
  });

  syncAccountModeUi();
})();
