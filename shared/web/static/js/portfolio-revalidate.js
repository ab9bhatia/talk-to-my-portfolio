(() => {
  const banner = document.getElementById("portfolio-stale-banner");
  if (!banner) return;

  const needsPoll =
    banner.dataset.stale === "true" || banner.dataset.revalidating === "true";
  if (!needsPoll) return;

  let attempts = 0;
  const maxAttempts = 30;
  const pollMs = 5000;

  async function poll() {
    if (document.hidden) {
      setTimeout(poll, pollMs);
      return;
    }

    attempts += 1;
    try {
      const res = await fetch("/api/portfolio/meta");
      const meta = await res.json();

      if (meta.last_job_status === "error") {
        banner.textContent =
          meta.last_job_error ||
          "Background refresh failed — use Refresh on the page to try again.";
        banner.className = "dash-banner dash-banner--warn portfolio-stale-banner";
        return;
      }

      if (!meta.revalidating && (meta.fresh || meta.last_job_status === "done")) {
        window.location.reload();
        return;
      }

      if (meta.revalidating) {
        banner.textContent = "Updating live prices and metrics in the background…";
        banner.className = "dash-banner dash-banner--info portfolio-stale-banner";
      }
    } catch {
      /* retry */
    }

    if (attempts < maxAttempts) {
      setTimeout(poll, pollMs);
    }
  }

  poll();
})();
