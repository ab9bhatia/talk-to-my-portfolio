(function () {
  const runButton = document.getElementById("weekly-sync-run");
  const modeInput = document.getElementById("weekly-sync-mode");
  const dryRunInput = document.getElementById("weekly-sync-dry-run");
  const result = document.getElementById("weekly-sync-result");
  if (!runButton || !modeInput || !dryRunInput || !result) return;

  function endpoint(path) {
    return window.appUrl ? window.appUrl(path) : path;
  }

  runButton.addEventListener("click", async function () {
    runButton.disabled = true;
    runButton.classList.add("is-loading");
    result.classList.remove("is-error", "is-ok");
    result.textContent = "Running account sync and deterministic review…";
    try {
      const response = await fetch(endpoint("/api/portfolio/sync/weekly"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: modeInput.value,
          dry_run: dryRunInput.checked,
        }),
      });
      const data = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(data.detail || "Weekly sync request failed.");
      const status = data.status || "UNKNOWN";
      const failed = ["FAILED", "LOCKED", "CANCELLED"].includes(status);
      result.classList.add(failed ? "is-error" : "is-ok");
      result.textContent = failed
        ? `${status.replaceAll("_", " ")}: ${data.error || "Review the run audit."}`
        : `${status.replaceAll("_", " ")} · run ${String(data.run_id || "").slice(0, 8)}. Refreshing status…`;
      if (!failed) window.setTimeout(function () { window.location.reload(); }, 900);
    } catch (error) {
      result.classList.add("is-error");
      result.textContent = error.message || "Weekly sync failed.";
    } finally {
      runButton.disabled = false;
      runButton.classList.remove("is-loading");
    }
  });
})();
