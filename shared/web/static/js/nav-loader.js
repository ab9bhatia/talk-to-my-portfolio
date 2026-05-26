(() => {
  const LOADER_PATHS = ["/portfolio", "/portfolio/account/"];

  function showLoader(message) {
    const loader = document.getElementById("page-loader");
    const text = document.getElementById("loader-text");
    if (!loader) return;
    if (text && message) text.textContent = message;
    loader.classList.remove("is-hidden");
    loader.setAttribute("aria-busy", "true");
  }

  function shouldShowLoader(href) {
    if (!href) return false;
    try {
      const url = new URL(href, window.location.origin);
      if (url.origin !== window.location.origin) return false;
      return LOADER_PATHS.some(
        (p) => url.pathname === p || url.pathname.startsWith(p + "/")
      );
    } catch {
      return false;
    }
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || link.target === "_blank" || event.metaKey || event.ctrlKey || event.shiftKey) {
      return;
    }
    const href = link.getAttribute("href");
    if (!shouldShowLoader(href)) return;
    const msg =
      link.dataset.loaderMessage ||
      (href.includes("refresh=1") ? "Refreshing portfolio…" : "Loading portfolio…");
    showLoader(msg);
  });

  window.addEventListener("pageshow", (event) => {
    const loader = document.getElementById("page-loader");
    if (!loader) return;
    if (event.persisted) {
      loader.classList.add("is-hidden");
      loader.setAttribute("aria-busy", "false");
    }
  });
})();
