(function () {
  const meta = document.querySelector('meta[name="app-root"]');
  const ROOT = meta?.getAttribute("content") || "";

  window.appUrl = function appUrl(path) {
    if (!path) return ROOT || "/";
    const p = path.startsWith("/") ? path : `/${path}`;
    return `${ROOT}${p}`;
  };

  const origFetch = window.fetch.bind(window);
  let csrfPromise = null;

  async function csrfHeader() {
    if (!csrfPromise) {
      csrfPromise = origFetch(window.appUrl("/api/portfolio/security/csrf"), {
        credentials: "same-origin",
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((payload) => payload?.csrf_token || "")
        .catch(() => "");
    }
    return csrfPromise;
  }

  window.fetch = async function fetchWithRoot(input, init) {
    if (typeof input === "string" && input.startsWith("/") && !input.startsWith("//")) {
      if (!ROOT || (!input.startsWith(ROOT + "/") && input !== ROOT)) {
        input = window.appUrl(input);
      }
    }
    const method = String(init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const token = await csrfHeader();
      if (token) {
        const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
        headers.set("X-Portfolio-CSRF", token);
        init = { ...(init || {}), headers, credentials: init?.credentials || "same-origin" };
      }
    }
    return origFetch(input, init);
  };
})();
