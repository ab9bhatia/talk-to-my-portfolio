(function () {
  const runButton = document.getElementById("weekly-sync-run");
  const modeInput = document.getElementById("weekly-sync-mode");
  const dryRunInput = document.getElementById("weekly-sync-dry-run");
  const result = document.getElementById("weekly-sync-result");
  if (!runButton || !modeInput || !dryRunInput || !result) return;

  function endpoint(path) {
    return window.appUrl ? window.appUrl(path) : path;
  }

  const TERMINAL_STATUSES = new Set([
    "COMPLETED",
    "COMPLETED_WITH_WARNINGS",
    "FAILED",
    "LOCKED",
    "CANCELLED",
    "SKIPPED_DUPLICATE",
  ]);
  const FAILURE_STATUSES = new Set(["FAILED", "LOCKED", "CANCELLED"]);
  let activeRunId = null;
  let pollTimer = null;

  function statusLabel(status) {
    return String(status || "UNKNOWN").replaceAll("_", " ");
  }

  function currentStep(data) {
    const steps = Array.isArray(data.steps) ? data.steps : [];
    const running = steps.find((step) => step.status === "RUNNING");
    return running ? statusLabel(running.step_name).toLowerCase() : "broker refresh";
  }

  function setBusy(busy) {
    runButton.disabled = busy;
    runButton.classList.toggle("is-loading", busy);
    runButton.textContent = busy ? "Sync running in background…" : "Run weekly sync";
  }

  function clearSyncQuery() {
    const url = new URL(window.location.href);
    url.searchParams.delete("sync_run_id");
    url.searchParams.delete("broker_connected");
    url.searchParams.delete("account");
    window.history.replaceState({}, "", url);
  }

  async function pollRun(runId) {
    activeRunId = runId;
    setBusy(true);
    result.classList.remove("is-error", "is-ok");
    try {
      const response = await fetch(endpoint(`/api/portfolio/sync/jobs/${runId}`), {
        headers: { Accept: "application/json" },
      });
      const data = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(data.detail || "Could not read sync progress.");

      const status = data.status || "QUEUED";
      if (!TERMINAL_STATUSES.has(status)) {
        result.textContent = `${statusLabel(status)} · ${currentStep(data)}. You can keep using the app.`;
        pollTimer = window.setTimeout(function () { pollRun(runId); }, 2000);
        return;
      }

      const failed = FAILURE_STATUSES.has(status);
      result.classList.add(failed ? "is-error" : "is-ok");
      result.textContent = failed
        ? `${statusLabel(status)}: ${data.error || "Review the run audit."}`
        : `${statusLabel(status)} · run ${runId.slice(0, 8)}. Portfolio data is ready.`;
      clearSyncQuery();
      activeRunId = null;
      setBusy(false);
    } catch (error) {
      result.classList.add("is-error");
      result.textContent = `${error.message || "Sync status failed."} The background job may still be running.`;
      activeRunId = null;
      setBusy(false);
    }
  }

  runButton.addEventListener("click", async function () {
    if (activeRunId) return;
    setBusy(true);
    result.classList.remove("is-error", "is-ok");
    result.textContent = "Starting background account sync…";
    try {
      const response = await fetch(endpoint("/api/portfolio/sync/weekly/async"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: modeInput.value,
          dry_run: dryRunInput.checked,
        }),
      });
      const data = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(data.detail || "Weekly sync request failed.");
      if (!data.run_id) throw new Error("Sync was accepted without a run id.");
      result.textContent = data.accepted === false
        ? "A sync is already running. Following its progress…"
        : "Sync queued. You can navigate away; it will continue in the background.";
      await pollRun(data.run_id);
    } catch (error) {
      result.classList.add("is-error");
      result.textContent = error.message || "Weekly sync failed.";
      setBusy(false);
    }
  });

  const query = new URLSearchParams(window.location.search);
  const oauthRunId = query.get("sync_run_id");
  if (oauthRunId) {
    const account = query.get("account");
    result.textContent = `${account ? `${account} connected. ` : ""}Portfolio sync is continuing in the background…`;
    pollRun(oauthRunId);
  }

  window.addEventListener("beforeunload", function () {
    if (pollTimer) window.clearTimeout(pollTimer);
  });
})();
