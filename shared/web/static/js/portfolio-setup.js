(function () {
  const dialog = document.getElementById("setup-dialog");
  const brokerSelect = document.getElementById("setup-broker-select");
  const brokerTiles = document.getElementById("setup-broker-tiles");
  const brokerPicker = document.getElementById("setup-broker-picker");
  const brokerPanel = document.getElementById("setup-broker-panel");
  const fieldsEl = document.getElementById("setup-fields");
  const introEl = document.getElementById("setup-broker-intro");
  const stepsEl = document.getElementById("setup-broker-steps");
  const linksEl = document.getElementById("setup-broker-links");
  const importSection = document.getElementById("setup-import-section");
  const form = document.getElementById("setup-form");
  const toast = document.getElementById("setup-toast");
  const saveBtn = document.getElementById("setup-save-btn");
  const importBtn = document.getElementById("setup-import-btn");
  const connectLink = document.getElementById("setup-connect-link");
  const enabledWrap = document.getElementById("setup-enabled-wrap");
  const enabledInput = document.getElementById("setup-enabled");
  const modalEyebrow = document.getElementById("setup-modal-eyebrow");
  const modalTitle = document.getElementById("setup-modal-title");
  const editModeInput = document.getElementById("setup-edit-mode");
  const editBrokerInput = document.getElementById("setup-edit-broker");
  const editIdInput = document.getElementById("setup-edit-id");
  const callbackDefault = window.__SETUP_CALLBACK__ || "";
  const visionOk = !!window.__SETUP_VISION__;

  const BROKER_META = {
    zerodha: { glyph: "Z", color: "zerodha" },
    groww: { glyph: "G", color: "groww" },
    dhan: { glyph: "D", color: "dhan" },
    sarwa: { glyph: "S", color: "sarwa" },
    custom: { glyph: "◇", color: "custom" },
  };

  function hideConnectLink() {
    if (!connectLink) return;
    connectLink.hidden = true;
    connectLink.setAttribute("hidden", "");
    connectLink.removeAttribute("href");
    connectLink.textContent = "";
  }

  function showConnectLink(account) {
    if (!connectLink || !account || account.broker !== "zerodha" || !account.connect_url) {
      hideConnectLink();
      return;
    }
    connectLink.href = account.connect_url;
    connectLink.textContent = "Connect Zerodha ↗";
    connectLink.removeAttribute("hidden");
    connectLink.hidden = false;
  }

  let catalog = [];
  let mode = "add";
  let editAccount = null;

  function isEdit() {
    return mode === "edit";
  }

  function showToast(msg, isError) {
    if (!toast) return;
    toast.innerHTML = `<span class="setup-toast-icon">${isError ? "!" : "✓"}</span><span>${msg}</span>`;
    toast.hidden = false;
    toast.classList.toggle("is-error", !!isError);
    setTimeout(() => { toast.hidden = true; }, 5200);
  }

  function slugify(label) {
    return (label || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .replace(/^(\d)/, "a$1")
      .slice(0, 31);
  }

  function openDialog() {
    dialog?.showModal();
    document.body.classList.add("setup-modal-open");
  }

  function closeDialog() {
    dialog?.close();
    document.body.classList.remove("setup-modal-open");
    editAccount = null;
    mode = "add";
    editModeInput.value = "0";
  }

  dialog?.addEventListener("close", () => document.body.classList.remove("setup-modal-open"));

  function currentBroker() {
    return catalog.find((b) => b.id === brokerSelect.value);
  }

  function brokerAcceptsUpload(brokerId) {
    const b = catalog.find((x) => x.id === brokerId);
    return b && (brokerId === "custom" || brokerId === "sarwa");
  }

  function updateImportSection(brokerId) {
    const show = isEdit()
      ? brokerId === "custom" || brokerId === "sarwa"
      : brokerId === "custom";
    importSection.hidden = !show;
    if (!show) return;

    const fileInput = document.getElementById("setup-import-file");
    const accept = brokerId === "sarwa"
      ? ".png,.jpg,.jpeg,.webp"
      : (catalog.find((b) => b.id === "custom")?.accept_upload || ".csv,.xlsx,.xls,.png,.jpg,.jpeg");
    if (fileInput) fileInput.accept = accept;

    const hint = document.getElementById("setup-dropzone-hint");
    if (brokerId === "sarwa") {
      hint.textContent = visionOk ? "Sarwa Trade screenshot (USD)" : "Needs OPENAI_API_KEY for screenshots";
    } else {
      hint.textContent = visionOk
        ? "CSV, Excel, or broker screenshot (INR)"
        : "CSV or Excel (add OpenAI key for screenshots)";
    }
    importBtn.hidden = !isEdit();
  }

  function selectBroker(id) {
    brokerSelect.value = id;
    brokerTiles.querySelectorAll(".setup-broker-tile").forEach((el) => {
      el.classList.toggle("is-selected", el.dataset.broker === id);
      el.setAttribute("aria-checked", el.dataset.broker === id ? "true" : "false");
    });
    const broker = currentBroker();
    const available = broker?.available !== false;
    brokerPanel.hidden = !available && !isEdit();
    saveBtn.disabled = !available && !isEdit();
    if (available || isEdit()) renderBrokerForm();
    updateImportSection(id);
  }

  function renderBrokerTiles() {
    brokerTiles.innerHTML = "";
    catalog.forEach((b) => {
      const meta = BROKER_META[b.id] || { glyph: b.label[0], color: b.id };
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `setup-broker-tile setup-broker-tile--${meta.color}${b.available ? "" : " is-disabled"}`;
      btn.dataset.broker = b.id;
      btn.disabled = !b.available;
      btn.setAttribute("role", "radio");
      btn.innerHTML = `
        <span class="setup-broker-tile-icon">${meta.glyph}</span>
        <span class="setup-broker-tile-label">${b.label}</span>
        ${b.available ? "" : '<span class="setup-broker-tile-soon">Soon</span>'}
      `;
      btn.addEventListener("click", () => selectBroker(b.id));
      brokerTiles.appendChild(btn);

      const opt = document.createElement("option");
      opt.value = b.id;
      opt.textContent = b.label;
      opt.disabled = !b.available;
      brokerSelect.appendChild(opt);
    });
    const first = catalog.find((b) => b.available)?.id || catalog[0]?.id;
    if (first) selectBroker(first);
  }

  async function loadCatalog() {
    const res = await fetch("/api/portfolio/setup/brokers");
    const data = await res.json();
    catalog = data.brokers || [];
    brokerSelect.innerHTML = "";
    renderBrokerTiles();
  }

  function secretPlaceholder(set) {
    return set ? "Leave blank to keep current" : "";
  }

  function renderBrokerForm() {
    const broker = currentBroker();
    if (!broker && !isEdit()) return;

    hideConnectLink();
    fieldsEl.innerHTML = "";
    stepsEl.innerHTML = "";
    linksEl.innerHTML = "";
    document.getElementById("setup-guide").hidden = isEdit();

    if (!isEdit()) {
      introEl.textContent = broker?.description || "";
      (broker?.steps || []).forEach((step, i) => {
        const li = document.createElement("li");
        li.innerHTML = `<span class="setup-step-num">${i + 1}</span><span>${step}</span>`;
        stepsEl.appendChild(li);
      });
      if (broker?.external_url) {
        const a = document.createElement("a");
        a.href = broker.external_url;
        a.target = "_blank";
        a.rel = "noopener";
        a.className = "setup-guide-cta";
        a.textContent = `Open ${broker.label} developer portal`;
        linksEl.appendChild(a);
      }
      (broker?.fields || []).forEach((f) => fieldsEl.appendChild(buildField(f, broker, null)));
    } else if (editAccount) {
      fillEditFields(editAccount);
    }

    if (broker?.auth_methods && !isEdit()) {
      const wrap = document.createElement("div");
      wrap.className = "setup-auth-methods";
      const select = fieldsEl.querySelector('[name="auth_method"]');
      const renderAuthFields = () => {
        wrap.querySelectorAll(".auth-method-fields").forEach((el) => el.remove());
        const method = select ? select.value : "totp";
        const spec = broker.auth_methods.find((m) => m.id === method);
        (spec?.fields || []).forEach((f) => {
          const block = document.createElement("div");
          block.className = "auth-method-fields setup-form-grid";
          block.appendChild(buildField(f, broker, null));
          wrap.appendChild(block);
        });
      };
      if (select) {
        select.addEventListener("change", renderAuthFields);
        renderAuthFields();
      }
      fieldsEl.appendChild(wrap);
    }

    if (!isEdit()) {
      const labelInput = fieldsEl.querySelector('[name="label"]');
      const idInput = fieldsEl.querySelector('[name="id"]');
      if (labelInput && idInput && !idInput.dataset.touched) {
        labelInput.addEventListener("input", () => {
          if (!idInput.dataset.touched) idInput.value = slugify(labelInput.value);
        });
        idInput.addEventListener("input", () => { idInput.dataset.touched = "1"; });
      }
    }

    const redirect = fieldsEl.querySelector('[name="redirect_url"]');
    if (redirect && !redirect.value) {
      redirect.value = broker?.default_callback_url || callbackDefault;
    }
  }

  function buildField(f, broker, account) {
    const label = document.createElement("label");
    label.className = "setup-field";
    const span = document.createElement("span");
    span.className = "setup-field-label";
    span.textContent = f.label + (f.required && !isEdit() ? "" : isEdit() ? "" : "");
    label.appendChild(span);

    let input;
    if (f.type === "auth_method" && broker?.auth_methods) {
      input = document.createElement("select");
      input.className = "setup-input";
      input.name = f.name;
      broker.auth_methods.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.label;
        input.appendChild(opt);
      });
      if (account?.auth_method) input.value = account.auth_method;
    } else {
      input = document.createElement("input");
      input.className = "setup-input";
      input.name = f.name;
      input.type = f.type === "secret" ? "password" : f.type === "url" ? "url" : "text";
      if (f.required && !isEdit()) input.required = true;
      if (f.name === "id" && isEdit()) {
        input.readOnly = true;
        input.classList.add("setup-input--readonly");
      }
      if (f.type === "secret" && isEdit() && account?.secrets) {
        const key = f.name === "api_key" ? "api_key_set" : f.name === "api_secret" ? "api_secret_set"
          : f.name === "totp_token" ? "totp_token_set" : f.name === "totp_secret" ? "totp_secret_set" : null;
        if (key && account.secrets[key]) {
          input.placeholder = secretPlaceholder(true);
        }
      }
    }
    label.appendChild(input);
    if (f.hint) {
      const hint = document.createElement("p");
      hint.className = "setup-field-hint";
      hint.textContent = f.hint;
      label.appendChild(hint);
    }
    return label;
  }

  function fillEditFields(account) {
    const broker = catalog.find((b) => b.id === account.broker) || { fields: [] };
    const defs = broker.fields || [];
    defs.forEach((f) => {
      const field = buildField(f, broker, account);
      const input = field.querySelector("[name]");
      if (!input) return;
      if (f.name === "label") input.value = account.label || "";
      if (f.name === "id") input.value = account.id || "";
      if (f.name === "code") input.value = account.code || "";
      if (f.name === "user_id") input.value = account.user_id || "";
      if (f.name === "redirect_url") input.value = account.redirect_url || callbackDefault;
      fieldsEl.appendChild(field);
    });

    if (account.broker === "groww" && broker.auth_methods) {
      const m = document.createElement("label");
      m.className = "setup-field";
      m.innerHTML = `<span class="setup-field-label">Authentication</span>`;
      const sel = document.createElement("select");
      sel.className = "setup-input";
      sel.name = "auth_method";
      broker.auth_methods.forEach((am) => {
        const o = document.createElement("option");
        o.value = am.id;
        o.textContent = am.label;
        sel.appendChild(o);
      });
      sel.value = account.auth_method || "totp";
      m.appendChild(sel);
      fieldsEl.appendChild(m);

      const wrap = document.createElement("div");
      wrap.className = "setup-auth-methods";
      const renderAuth = () => {
        wrap.innerHTML = "";
        const spec = broker.auth_methods.find((x) => x.id === sel.value);
        (spec?.fields || []).forEach((f) => {
          const block = document.createElement("div");
          block.className = "auth-method-fields setup-form-grid";
          block.appendChild(buildField(f, broker, account));
          wrap.appendChild(block);
        });
      };
      sel.addEventListener("change", renderAuth);
      renderAuth();
      fieldsEl.appendChild(wrap);
    }

    enabledWrap.hidden = false;
    enabledInput.checked = account.enabled !== false;
    showConnectLink(account);
  }

  function openAdd() {
    mode = "add";
    editModeInput.value = "0";
    editBrokerInput.value = "";
    editIdInput.value = "";
    modalEyebrow.textContent = "New connection";
    modalTitle.textContent = "Add account";
    brokerPicker.hidden = false;
    enabledWrap.hidden = true;
    hideConnectLink();
    importBtn.hidden = true;
    saveBtn.textContent = "Save & continue";
    document.getElementById("setup-import-file").value = "";
    document.getElementById("setup-file-name").hidden = true;
    openDialog();
    loadCatalog();
  }

  async function openEdit(broker, accountId) {
    mode = "edit";
    editModeInput.value = "1";
    editBrokerInput.value = broker;
    editIdInput.value = accountId;
    modalEyebrow.textContent = "Manage connection";
    modalTitle.textContent = "Edit account";
    brokerPicker.hidden = true;
    saveBtn.textContent = "Save changes";
    saveBtn.disabled = false;
    hideConnectLink();

    openDialog();

    const res = await fetch(`/api/portfolio/setup/accounts/${broker}/${accountId}`);
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || "Could not load account", true);
      closeDialog();
      return;
    }
    editAccount = data;

    if (!catalog.length) {
      const br = await fetch("/api/portfolio/setup/brokers");
      catalog = (await br.json()).brokers || [];
    }
    brokerSelect.innerHTML = "";
    const opt = document.createElement("option");
    opt.value = broker;
    opt.textContent = broker;
    brokerSelect.appendChild(opt);
    brokerSelect.value = broker;

    brokerPanel.hidden = false;
    renderBrokerForm();
    updateImportSection(broker);

    if (data.has_holdings) {
      document.getElementById("setup-import-desc").textContent =
        `${data.holdings_count || 0} positions on file — upload to replace.`;
    }
  }

  function wireDropzone() {
    const zone = document.getElementById("setup-dropzone");
    const input = document.getElementById("setup-import-file");
    const nameEl = document.getElementById("setup-file-name");
    if (!zone || !input) return;

    zone.addEventListener("click", (e) => {
      if (e.target.closest("#setup-browse-file")) return;
      if (e.target.closest("button") && e.target.id !== "setup-browse-file") return;
      input.click();
    });
    document.getElementById("setup-browse-file")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      input.click();
    });
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      nameEl.hidden = !file;
      nameEl.textContent = file ? file.name : "";
      zone.classList.toggle("has-file", !!file);
    });
    ["dragenter", "dragover"].forEach((ev) => {
      zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-dragover"); });
    });
    ["dragleave", "drop"].forEach((ev) => {
      zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("is-dragover"); });
    });
    zone.addEventListener("drop", (e) => {
      const file = e.dataTransfer?.files?.[0];
      if (!file) return;
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("change"));
    });
  }

  wireDropzone();

  async function uploadHoldingsOnly() {
    const broker = editBrokerInput.value || brokerSelect.value;
    const accountId = editIdInput.value;
    const file = document.getElementById("setup-import-file")?.files?.[0];
    if (!file) {
      showToast("Choose a file first", true);
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    importBtn.disabled = true;
    try {
      const res = await fetch(`/api/portfolio/setup/accounts/${broker}/${accountId}/import`, {
        method: "POST",
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || "Import failed", true);
        return;
      }
      showToast(`Imported ${data.imported} holdings — dashboard updated`);
      setTimeout(() => location.reload(), 700);
    } finally {
      importBtn.disabled = false;
    }
  }

  ["setup-add-btn", "setup-add-btn-empty", "setup-add-card"].forEach((id) => {
    document.getElementById(id)?.addEventListener("click", openAdd);
  });

  document.querySelectorAll(".setup-card--interactive").forEach((card) => {
    const open = () => openEdit(card.dataset.broker, card.dataset.id);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
  });

  document.getElementById("setup-dialog-close")?.addEventListener("click", closeDialog);
  document.getElementById("setup-cancel-btn")?.addEventListener("click", closeDialog);
  importBtn?.addEventListener("click", uploadHoldingsOnly);

  document.getElementById("setup-copy-callback")?.addEventListener("click", () => {
    const text = document.getElementById("setup-callback-display")?.textContent || "";
    navigator.clipboard?.writeText(text).then(() => showToast("Redirect URL copied"));
  });

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const broker = isEdit() ? editBrokerInput.value : brokerSelect.value;
    const fd = new FormData(form);
    const payload = {};
    fd.forEach((v, k) => {
      if (["import_file", "edit_mode", "edit_broker", "edit_id"].includes(k)) return;
      if (k === "enabled") payload.enabled = enabledInput.checked;
      else payload[k] = v;
    });
    if (!payload.enabled && enabledInput) payload.enabled = enabledInput.checked;

    saveBtn.disabled = true;
    saveBtn.classList.add("is-loading");
    try {
      let res;
      if (isEdit()) {
        res = await fetch(`/api/portfolio/setup/accounts/${broker}/${editIdInput.value}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        res = await fetch(`/api/portfolio/setup/accounts/${broker}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || "Could not save", true);
        return;
      }

      const importFile = document.getElementById("setup-import-file")?.files?.[0];
      const accountId = isEdit() ? editIdInput.value : data.account?.id;

      if (importFile && accountId && brokerAcceptsUpload(broker)) {
        const body = new FormData();
        body.append("file", importFile);
        const imp = await fetch(`/api/portfolio/setup/accounts/${broker}/${accountId}/import`, {
          method: "POST",
          body,
        });
        const impData = await imp.json().catch(() => ({}));
        if (!imp.ok) {
          showToast(impData.detail || "Saved settings; import failed", true);
          setTimeout(() => location.reload(), 900);
          return;
        }
        showToast(`Saved — ${impData.imported} holdings imported`);
      } else if (!isEdit() && data.connect_url) {
        showToast("Saved — opening Zerodha login");
        window.location.href = data.connect_url;
        return;
      } else {
        showToast(isEdit() ? "Account updated" : "Account saved");
      }
      closeDialog();
      setTimeout(() => location.reload(), 600);
    } finally {
      saveBtn.disabled = false;
      saveBtn.classList.remove("is-loading");
    }
  });
})();
