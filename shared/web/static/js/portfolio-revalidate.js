(() => {
  const banner = document.getElementById("portfolio-stale-banner");
  if (!banner) return;

  const needsPoll =
    banner.dataset.stale === "true" || banner.dataset.revalidating === "true";
  if (!needsPoll) return;

  let attempts = 0;
  const maxAttempts = 90;

  async function poll() {
    attempts += 1;
    try {
      const res = await fetch("/api/portfolio/meta");
      const meta = await res.json();
      if (meta.fresh && !meta.revalidating) {
        window.location.reload();
        return;
      }
      if (meta.revalidating) {
        banner.textContent = "Updating prices and metrics in the background…";
        banner.className = "alert alert-info portfolio-stale-banner";
      }
    } catch {
      /* retry */
    }
    if (attempts < maxAttempts) {
      setTimeout(poll, 2000);
    }
  }

  poll();
})();
