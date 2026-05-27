(function () {
  const dialog = document.getElementById("setup-llm-dialog");
  const form = document.getElementById("setup-llm-form");
  const tilesEl = document.getElementById("setup-llm-tiles");
  const providerSelect = document.getElementById("setup-llm-provider");
  const fieldsEl = document.getElementById("setup-llm-fields");
  const introEl = document.getElementById("setup-llm-intro");
  const configureBtn = document.getElementById("setup-llm-configure-btn");
  const toast = document.getElementById("setup-toast");
  const card = document.getElementById("setup-llm-card");
  const detailEl = document.getElementById("setup-llm-detail");

  if (!dialog || !form) return;

  const CUSTOM_MODEL = "__custom__";

  const PROVIDER_META = {
    openai: { glyph: "O", color: "openai" },
    anthropic: { glyph: "C", color: "anthropic" },
    gemini: { glyph: "G", color: "gemini" },
    ollama: { glyph: "◉", color: "ollama" },
  };

  let catalog = [];
  let currentValues = { provider: "", model: "", base_url: "", api_key_set: false };
  let ollamaFetchTimer = null;

  function showToast(msg, isError) {
    if (!toast) return;
    toast.innerHTML = `<span class="setup-toast-icon">${isError ? "!" : "✓"}</span><span>${msg}</span>`;
    toast.hidden = false;
    toast.classList.toggle("is-error", !!isError);
    setTimeout(() => { toast.hidden = true; }, 5200);
  }

  function currentProvider() {
    return catalog.find((p) => p.id === providerSelect.value);
  }

  function modelOptionsForProvider(provider) {
    return provider?.models || [];
  }

  function resolveModelValue(selectEl, customInput) {
    if (selectEl.value === CUSTOM_MODEL) {
      return (customInput?.value || "").trim();
    }
    return selectEl.value;
  }

  function buildModelSelect(field, provider, values) {
    const wrap = document.createElement("div");
    wrap.className = "setup-field setup-field--model-select";

    const label = document.createElement("span");
    label.className = "setup-field-label";
    label.textContent = field.label + (field.required ? " *" : "");
    wrap.appendChild(label);

    const select = document.createElement("select");
    select.name = "model_select";
    select.id = `setup-llm-${field.name}`;
    select.className = "setup-select";
    select.required = true;

    const options = modelOptionsForProvider(provider);
    const current = (values.model || provider.default_model || "").trim();

    options.forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.recommended ? `${opt.label}` : opt.label;
      if (opt.value === current) o.selected = true;
      select.appendChild(o);
    });

    const inList = options.some((o) => o.value === current);
    const customOpt = document.createElement("option");
    customOpt.value = CUSTOM_MODEL;
    customOpt.textContent = "Other (type model id…)";
    if (current && !inList) customOpt.selected = true;
    select.appendChild(customOpt);

    wrap.appendChild(select);

    const customWrap = document.createElement("label");
    customWrap.className = "setup-field setup-field--model-custom";
    customWrap.hidden = select.value !== CUSTOM_MODEL;
    const customLabel = document.createElement("span");
    customLabel.className = "setup-field-label";
    customLabel.textContent = "Custom model id";
    const customInput = document.createElement("input");
    customInput.type = "text";
    customInput.name = "model_custom";
    customInput.className = "setup-input";
    customInput.placeholder = provider.id === "ollama" ? "e.g. llama3.2:latest" : "Exact model id from provider docs";
    if (current && !inList) customInput.value = current;
    customWrap.appendChild(customLabel);
    customWrap.appendChild(customInput);
    wrap.appendChild(customWrap);

    select.addEventListener("change", () => {
      customWrap.hidden = select.value !== CUSTOM_MODEL;
      customInput.required = select.value === CUSTOM_MODEL;
    });

    wrap.dataset.modelField = "1";
    return wrap;
  }

  function buildField(field, provider, values) {
    if (field.type === "model_select") {
      return buildModelSelect(field, provider, values);
    }

    const wrap = document.createElement("label");
    wrap.className = "setup-field";
    const id = `setup-llm-${field.name}`;
    const label = document.createElement("span");
    label.className = "setup-field-label";
    label.textContent = field.label + (field.required ? " *" : "");
    wrap.appendChild(label);

    let input;
    if (field.type === "secret") {
      input = document.createElement("input");
      input.type = "password";
      input.autocomplete = "off";
      input.className = "setup-input";
      input.placeholder = values.api_key_set ? "Leave blank to keep current" : "Paste API key";
    } else {
      input = document.createElement("input");
      input.type = field.type === "url" ? "url" : "text";
      input.className = "setup-input";
      const val = field.name === "base_url" ? values.base_url : "";
      if (val) input.value = val;
      if (field.placeholder) input.placeholder = field.placeholder;
      if (field.name === "base_url" && provider?.models_dynamic) {
        input.addEventListener("change", () => scheduleOllamaModelRefresh(input.value));
        input.addEventListener("blur", () => scheduleOllamaModelRefresh(input.value));
      }
    }
    input.name = field.name;
    input.id = id;
    input.required =
      !!field.required && field.type === "secret" ? !values.api_key_set : !!field.required;
    wrap.appendChild(input);
    return wrap;
  }

  function refreshModelDropdown(provider, selectedModel) {
    const block = fieldsEl.querySelector("[data-model-field]");
    if (!block || !provider) return;
    const values = { ...currentValues, model: selectedModel || currentValues.model };
    const field = (provider.fields || []).find((f) => f.name === "model");
    if (!field) return;
    const next = buildModelSelect(field, provider, values);
    block.replaceWith(next);
  }

  async function scheduleOllamaModelRefresh(baseUrl) {
    if (providerSelect.value !== "ollama") return;
    clearTimeout(ollamaFetchTimer);
    ollamaFetchTimer = setTimeout(async () => {
      const provider = catalog.find((p) => p.id === "ollama");
      if (!provider) return;
      try {
        const q = new URLSearchParams({ base_url: baseUrl || "http://localhost:11434" });
        const res = await fetch(`/api/portfolio/setup/llm/ollama-models?${q}`);
        const data = await res.json();
        if (data.models?.length) {
          provider.models = data.models;
          refreshModelDropdown(provider, currentValues.model);
        }
      } catch {
        /* keep static list */
      }
    }, 400);
  }

  function renderFields() {
    const provider = currentProvider();
    fieldsEl.innerHTML = "";
    if (!provider) return;
    introEl.textContent = provider.description || "";
    (provider.fields || []).forEach((f) => {
      fieldsEl.appendChild(buildField(f, provider, currentValues));
    });
    if (provider.id === "ollama" && provider.models_dynamic) {
      const baseInput = fieldsEl.querySelector('[name="base_url"]');
      if (baseInput?.value) scheduleOllamaModelRefresh(baseInput.value);
    }
  }

  function selectProvider(id) {
    providerSelect.value = id;
    tilesEl.querySelectorAll(".setup-broker-tile").forEach((el) => {
      el.classList.toggle("is-selected", el.dataset.provider === id);
      el.setAttribute("aria-checked", el.dataset.provider === id ? "true" : "false");
    });
    const provider = currentProvider();
    if (provider) {
      const known = (provider.models || []).some((m) => m.value === currentValues.model);
      if (!known) {
        currentValues.model = provider.default_model || currentValues.model || "";
      }
      if (id === "ollama" && !currentValues.base_url) {
        currentValues.base_url = "http://localhost:11434";
      }
    }
    renderFields();
  }

  function renderTiles() {
    tilesEl.innerHTML = "";
    providerSelect.innerHTML = "";
    catalog.forEach((p) => {
      const meta = PROVIDER_META[p.id] || { glyph: p.label[0], color: p.id };
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `setup-broker-tile setup-broker-tile--${meta.color}`;
      btn.dataset.provider = p.id;
      btn.setAttribute("role", "radio");
      btn.innerHTML = `
        <span class="setup-broker-tile-icon">${meta.glyph}</span>
        <span class="setup-broker-tile-label">${p.label}</span>
      `;
      btn.addEventListener("click", () => selectProvider(p.id));
      tilesEl.appendChild(btn);

      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.label;
      providerSelect.appendChild(opt);
    });
  }

  async function loadConfig() {
    const res = await fetch("/api/portfolio/setup/llm");
    const data = await res.json();
    catalog = data.providers || [];
    currentValues = data.values || currentValues;
    renderTiles();
    const pid = data.provider || catalog[0]?.id;
    if (pid) {
      currentValues.provider = pid;
      currentValues.model = data.model || currentValues.model;
      currentValues.base_url = data.ollama_base_url || currentValues.base_url;
      currentValues.api_key_set = data.values?.api_key_set || data.api_configured;
      selectProvider(pid);
    }
  }

  function openDialog() {
    loadConfig().then(() => {
      dialog.showModal();
      document.body.classList.add("setup-modal-open");
    });
  }

  function closeDialog() {
    dialog.close();
    document.body.classList.remove("setup-modal-open");
  }

  configureBtn?.addEventListener("click", openDialog);
  document.getElementById("setup-llm-dialog-close")?.addEventListener("click", closeDialog);
  document.getElementById("setup-llm-cancel-btn")?.addEventListener("click", closeDialog);
  dialog.addEventListener("close", () => document.body.classList.remove("setup-modal-open"));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const provider = String(fd.get("provider") || "");

    const modelSelect = form.querySelector('select[name="model_select"]');
    const modelCustom = form.querySelector('input[name="model_custom"]');
    let model = "";
    if (modelSelect) {
      model = resolveModelValue(modelSelect, modelCustom);
    }

    const body = {
      provider,
      model: model || undefined,
      base_url: String(fd.get("base_url") || "").trim() || undefined,
      api_key: String(fd.get("api_key") || "").trim() || undefined,
    };
    try {
      const res = await fetch("/api/portfolio/setup/llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Save failed");
      const msg = data.message || "LLM settings saved to .env";
      showToast(msg, false);
      if (card) card.classList.toggle("is-live", !!data.configured);
      if (detailEl && data.configured) {
        detailEl.innerHTML = `Model: <code class="setup-inline-code">${data.model || ""}</code> · Chats kept 1 week · saved in .env`;
      }
      closeDialog();
    } catch (err) {
      showToast(err.message || "Could not save LLM settings", true);
    }
  });
})();
