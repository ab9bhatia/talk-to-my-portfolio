(function () {
  const meta = document.querySelector('meta[name="app-root"]');
  const ROOT = meta?.getAttribute("content") || "";

  window.appUrl = function appUrl(path) {
    if (!path) return ROOT || "/";
    const p = path.startsWith("/") ? path : `/${path}`;
    return `${ROOT}${p}`;
  };

  const origFetch = window.fetch.bind(window);
  window.fetch = function fetchWithRoot(input, init) {
    if (typeof input === "string" && input.startsWith("/") && !input.startsWith("//")) {
      if (!ROOT || (!input.startsWith(ROOT + "/") && input !== ROOT)) {
        input = window.appUrl(input);
      }
    }
    return origFetch(input, init);
  };
})();
