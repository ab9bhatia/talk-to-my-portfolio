(() => {
  const STORAGE_KEY = "portfolioSummaryRevealed";

  function setRevealed(wrap, revealed) {
    wrap.classList.toggle("is-revealed", revealed);
    wrap.dataset.masked = revealed ? "false" : "true";
    const button = wrap.querySelector(".portfolio-summary-toggle");
    if (button) button.setAttribute("aria-pressed", revealed ? "true" : "false");
    try {
      localStorage.setItem(STORAGE_KEY, revealed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  function initPortfolioSummary() {
    const wrap = document.getElementById("portfolio-summary");
    if (!wrap) return;

    const button = wrap.querySelector(".portfolio-summary-toggle");
    if (!button) return;

    let revealed = false;
    if (wrap.dataset.defaultMasked !== "true") {
      try {
        revealed = localStorage.getItem(STORAGE_KEY) === "1";
      } catch {
        /* ignore */
      }
    }
    setRevealed(wrap, revealed);

    button.addEventListener("click", () => {
      setRevealed(wrap, !wrap.classList.contains("is-revealed"));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPortfolioSummary);
  } else {
    initPortfolioSummary();
  }
})();
