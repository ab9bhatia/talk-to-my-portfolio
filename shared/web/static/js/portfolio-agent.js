(function () {
  const section = document.getElementById("portfolio-agent");
  if (!section) return;

  const STORAGE_KEY = "portfolio-agent-state:v1";

  const statusPill = document.getElementById("agent-status-pill");
  const hint = document.getElementById("agent-hint");
  const askBtn = document.getElementById("agent-ask-btn");
  const newChatBtn = document.getElementById("agent-new-chat-btn");
  const sessionsNewBtn = document.getElementById("agent-sessions-new-btn");
  const sessionsList = document.getElementById("agent-sessions-list");
  const sessionsEmpty = document.getElementById("agent-sessions-empty");
  const btnSpinner = document.getElementById("agent-btn-spinner");
  const btnLabel = askBtn?.querySelector(".agent-btn-label");
  const questionEl = document.getElementById("agent-question");
  const statusLine = document.getElementById("agent-status-line");
  const errorEl = document.getElementById("agent-error");
  const outcomeEl = document.getElementById("agent-outcome");
  const outcomeTitle = document.getElementById("agent-outcome-title");
  const outcomeMeta = document.getElementById("agent-outcome-meta");
  const chatEl = document.getElementById("agent-chat");
  const resultsEl = document.getElementById("agent-results");
  const followupInput = document.getElementById("agent-followup-input");
  const followupBtn = document.getElementById("agent-followup-btn");

  let threadId = null;
  let busy = false;
  let abortController = null;
  let lastRecommendations = null;
  let sessions = [];

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function formatSessionTime(unixSeconds) {
    if (!unixSeconds) return "";
    const date = new Date(unixSeconds * 1000);
    const now = new Date();
    const sameDay =
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth() &&
      date.getDate() === now.getDate();
    if (sameDay) {
      return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    }
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function saveState() {
    if (!chatEl) return;
    const bubbles = [];
    chatEl.querySelectorAll(".agent-chat-bubble").forEach((node) => {
      const role = node.classList.contains("agent-chat-user") ? "user" : "assistant";
      const text = node.querySelector(".agent-chat-text")?.textContent || "";
      if (text && text !== "…") bubbles.push({ role, text });
    });
    try {
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          threadId,
          bubbles,
          recommendations: lastRecommendations,
          outcomeOpen: outcomeEl?.open ?? false,
        })
      );
    } catch {
      /* quota or private mode */
    }
  }

  function restoreState() {
    let raw;
    try {
      raw = sessionStorage.getItem(STORAGE_KEY);
    } catch {
      return false;
    }
    if (!raw) return false;

    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return false;
    }

    threadId = data.threadId || null;
    if (!data.bubbles?.length && !data.recommendations) return false;

    applyConversation({
      threadId,
      bubbles: data.bubbles,
      recommendations: data.recommendations,
      outcomeOpen: data.outcomeOpen !== false,
      metaNote: " · restored after navigation",
    });
    return true;
  }

  function applyConversation({ threadId: tid, bubbles, recommendations, outcomeOpen, metaNote }) {
    threadId = tid || null;
    lastRecommendations = recommendations || null;

    if (outcomeEl) {
      const hasContent = (bubbles && bubbles.length) || recommendations;
      outcomeEl.hidden = !hasContent;
      outcomeEl.open = outcomeOpen !== false;
    }
    if (outcomeTitle) {
      outcomeTitle.textContent = threadId ? "Agent conversation" : "Agent response";
    }
    if (chatEl) {
      chatEl.innerHTML = "";
      (bubbles || []).forEach((b) => appendChatBubble(b.role, b.text));
    }
    if (recommendations) {
      renderRecommendations(recommendations);
    } else if (resultsEl) {
      resultsEl.hidden = true;
    }
    if (outcomeMeta) outcomeMeta.textContent = metaNote || "";
    renderSessionsList();
    saveState();
  }

  function clearConversation() {
    threadId = null;
    lastRecommendations = null;
    if (chatEl) chatEl.innerHTML = "";
    if (resultsEl) resultsEl.hidden = true;
    if (outcomeEl) {
      outcomeEl.hidden = true;
      outcomeEl.open = false;
    }
    if (outcomeMeta) outcomeMeta.textContent = "";
    showError("");
    renderSessionsList();
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }

  function setBusy(on, statusText) {
    busy = on;
    if (askBtn) askBtn.disabled = on;
    if (followupBtn) followupBtn.disabled = on;
    if (newChatBtn) newChatBtn.disabled = on;
    if (sessionsNewBtn) sessionsNewBtn.disabled = on;
    if (btnSpinner) btnSpinner.hidden = !on;
    if (btnLabel) btnLabel.hidden = on;
    if (statusLine) {
      if (on && statusText) {
        statusLine.hidden = false;
        statusLine.textContent = statusText;
      } else {
        statusLine.hidden = true;
        statusLine.textContent = "";
      }
    }
  }

  function showError(message) {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.hidden = !message;
  }

  function appendChatBubble(role, text, extraClass) {
    if (!chatEl || !text) return null;
    const bubble = document.createElement("div");
    bubble.className = `agent-chat-bubble agent-chat-${role}${extraClass ? ` ${extraClass}` : ""}`;
    bubble.innerHTML = `<div class="agent-chat-role">${role === "user" ? "You" : "Agent"}</div><div class="agent-chat-text">${escapeHtml(text)}</div>`;
    chatEl.appendChild(bubble);
    chatEl.scrollTop = chatEl.scrollHeight;
    return bubble;
  }

  function renderList(title, items, mapFn) {
    if (!items || !items.length) {
      return `<h3>${escapeHtml(title)}</h3><p class="agent-empty">None noted.</p>`;
    }
    const lis = items.map(mapFn).join("");
    return `<h3>${escapeHtml(title)}</h3><ul class="agent-list">${lis}</ul>`;
  }

  function renderRecommendations(rec) {
    const stance = document.getElementById("agent-stance");
    const buy = document.getElementById("agent-buy");
    const sell = document.getElementById("agent-sell");
    const rebalance = document.getElementById("agent-rebalance");
    const redFlags = document.getElementById("agent-red-flags");
    const themes = document.getElementById("agent-themes");
    const macro = document.getElementById("agent-macro");
    const answerBlock = document.getElementById("agent-answer-block");

    if (!stance) return;

    lastRecommendations = rec;

    const answerText = (rec.answer || "").trim();
    if (answerText) {
      answerBlock.hidden = false;
      answerBlock.innerHTML = `<h3>Reply to your question</h3><p>${escapeHtml(answerText)}</p>`;
    } else {
      answerBlock.hidden = true;
      answerBlock.innerHTML = "";
    }

    stance.innerHTML = `
      <h3>Stance</h3>
      <p>${escapeHtml(rec.stance || "—")}</p>
      ${rec.xirr_outlook ? `<p class="agent-sub"><strong>XIRR outlook:</strong> ${escapeHtml(rec.xirr_outlook)}</p>` : ""}
    `;

    const tradeOn = Boolean(document.getElementById("trade-order-dialog"));
    const tradeBtn = (sym, side) =>
      tradeOn
        ? ` <button type="button" class="btn btn-ghost btn-sm js-agent-trade" data-symbol="${escapeHtml(sym || "")}" data-side="${side}">${side === "SELL" ? "Sell" : "Buy"}</button>`
        : "";

    buy.innerHTML = renderList("Buy / add", rec.buy, (row) => {
      const sym = row.symbol || "?";
      return `<li><strong>${escapeHtml(sym)}</strong> — ${escapeHtml(row.action || "")}: ${escapeHtml(row.rationale || "")}${tradeBtn(sym, "BUY")}</li>`;
    });
    sell.innerHTML = renderList("Sell / trim", rec.sell_or_trim, (row) => {
      const sym = row.symbol || "?";
      return `<li><strong>${escapeHtml(sym)}</strong> — ${escapeHtml(row.action || "")}: ${escapeHtml(row.rationale || "")}${tradeBtn(sym, "SELL")}</li>`;
    });
    rebalance.innerHTML = renderList("Rebalance", rec.rebalance, (row) =>
      `<li>${escapeHtml(row.action || "")}: ${escapeHtml(row.detail || row.rationale || "")}</li>`
    );
    redFlags.innerHTML = renderList("Red flags", rec.red_flags, (flag) =>
      `<li>${escapeHtml(String(flag))}</li>`
    );
    themes.innerHTML = renderList("Theme opportunities", rec.theme_opportunities, (row) =>
      `<li><strong>${escapeHtml(row.theme || "")}</strong>: ${escapeHtml(row.suggestion || "")}</li>`
    );
    macro.innerHTML = `<h3>Macro view</h3><p>${escapeHtml(rec.macro_view || "—")}</p>`;

    resultsEl.hidden = false;
    saveState();
  }

  function renderSessionsList() {
    if (!sessionsList) return;
    sessionsList.innerHTML = "";
    const hasSessions = sessions.length > 0;
    if (sessionsEmpty) sessionsEmpty.hidden = hasSessions;

    sessions.forEach((session) => {
      const li = document.createElement("li");
      li.className = "agent-session-row";
      li.setAttribute("role", "listitem");

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "agent-session-item";
      if (session.thread_id === threadId) btn.classList.add("is-active");
      if (session.important) btn.classList.add("is-important");
      btn.dataset.threadId = session.thread_id;
      btn.innerHTML = `
        <span class="agent-session-title">${session.important ? '<span class="agent-session-star-mark" aria-hidden="true">★ </span>' : ""}${escapeHtml(session.title || "Portfolio chat")}</span>
        <span class="agent-session-meta">${escapeHtml(formatSessionTime(session.updated_at))}${session.message_count ? ` · ${session.message_count} msgs` : ""}${session.important ? " · saved" : ""}</span>
      `;
      btn.addEventListener("click", () => loadSession(session.thread_id));

      const actions = document.createElement("div");
      actions.className = "agent-session-actions";

      const starBtn = document.createElement("button");
      starBtn.type = "button";
      starBtn.className = "agent-session-action agent-session-action-star";
      if (session.important) starBtn.classList.add("is-on");
      starBtn.setAttribute(
        "aria-label",
        session.important ? "Unmark as important" : "Mark as important"
      );
      starBtn.title = session.important ? "Unmark important" : "Mark important (keeps chat longer)";
      starBtn.textContent = "★";
      starBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleSessionImportant(session.thread_id, !session.important);
      });

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "agent-session-action agent-session-action-delete";
      deleteBtn.setAttribute("aria-label", "Delete chat");
      deleteBtn.title = "Delete chat";
      deleteBtn.textContent = "×";
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSession(session.thread_id, session.title);
      });

      actions.appendChild(starBtn);
      actions.appendChild(deleteBtn);
      li.appendChild(btn);
      li.appendChild(actions);
      sessionsList.appendChild(li);
    });
  }

  async function toggleSessionImportant(id, important) {
    if (!id || busy) return;
    try {
      const res = await fetch(`/api/portfolio/agent/sessions/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ important }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || `Could not update session (${res.status})`);
      }
      await loadSessions();
    } catch (err) {
      showError(err.message || "Could not update chat.");
    }
  }

  async function deleteSession(id, title) {
    if (!id || busy) return;
    const label = (title || "this chat").trim();
    if (!window.confirm(`Delete "${label}"? This cannot be undone.`)) return;

    try {
      const res = await fetch(`/api/portfolio/agent/sessions/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || `Could not delete session (${res.status})`);
      }
      if (threadId === id) {
        clearConversation();
      }
      await loadSessions();
    } catch (err) {
      showError(err.message || "Could not delete chat.");
    }
  }

  async function loadSessions() {
    if (!sessionsList) return;
    try {
      const res = await fetch("/api/portfolio/agent/sessions");
      if (!res.ok) return;
      const data = await res.json();
      sessions = data.sessions || [];
      renderSessionsList();
    } catch {
      /* sidebar is optional UX */
    }
  }

  async function loadSession(id) {
    if (!id || busy) return;
    if (id === threadId && chatEl?.querySelector(".agent-chat-bubble")) return;

    showError("");
    setBusy(true, "Loading chat…");
    try {
      const res = await fetch(`/api/portfolio/agent/sessions/${encodeURIComponent(id)}`);
      if (res.status === 404) {
        threadId = null;
        try {
          sessionStorage.removeItem(STORAGE_KEY);
        } catch {
          /* ignore */
        }
        await loadSessions();
        throw new Error("This chat expired. Start a new question.");
      }
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || `Could not load session (${res.status})`);
      }
      const data = await res.json();
      applyConversation({
        threadId: data.thread_id,
        bubbles: data.bubbles,
        recommendations: data.recommendations,
        outcomeOpen: true,
        metaNote: " · loaded from history",
      });
    } catch (err) {
      showError(err.message || "Could not load chat.");
    } finally {
      setBusy(false);
    }
  }

  function parseSseChunk(buffer, onEvent) {
    const parts = buffer.split("\n\n");
    const remainder = parts.pop() || "";
    for (const part of parts) {
      if (!part.trim()) continue;
      let event = "message";
      let data = "";
      for (const line of part.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          /* ignore malformed */
        }
      }
    }
    return remainder;
  }

  async function streamAsk(question, { isFollowUp } = { isFollowUp: false }) {
    if (busy) return;
    const msg = (question || "").trim();
    if (!msg) return;

    showError("");
    setBusy(true, "Connecting…");

    if (outcomeEl) {
      outcomeEl.hidden = false;
      outcomeEl.open = true;
    }
    if (outcomeTitle) {
      outcomeTitle.textContent = isFollowUp ? "Agent conversation" : "Agent response";
    }

    appendChatBubble("user", msg);
    if (!isFollowUp && questionEl) questionEl.value = "";
    if (isFollowUp && followupInput) followupInput.value = "";

    const streamBubble = appendChatBubble("assistant", "…", "is-streaming");
    let streamText = "";

    abortController = new AbortController();

    const useNewThread = !isFollowUp;
    const payloadThreadId = isFollowUp ? threadId : null;

    try {
      const res = await fetch("/api/portfolio/agent/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: msg,
          thread_id: payloadThreadId,
          new_thread: useNewThread,
          refresh: false,
        }),
        signal: abortController.signal,
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || `Request failed (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = parseSseChunk(buffer, (event, data) => {
          if (event === "status") {
            setBusy(true, data.message || "Working…");
            if (data.thread_id) threadId = data.thread_id;
          } else if (event === "token" && data.delta) {
            streamText += data.delta;
            const textEl = streamBubble?.querySelector(".agent-chat-text");
            if (textEl) textEl.textContent = streamText.slice(0, 800) + (streamText.length > 800 ? "…" : "");
          } else if (event === "done") {
            threadId = data.thread_id || threadId;
            if (data.recommendations) renderRecommendations(data.recommendations);
            if (outcomeMeta) outcomeMeta.textContent = " · updated just now";
            streamBubble?.remove();
            const summary =
              (data.recommendations?.answer || "").trim() ||
              data.recommendations?.stance ||
              "Response ready — see sections below.";
            appendChatBubble("assistant", summary);
            saveState();
            loadSessions();
            renderSessionsList();
          } else if (event === "error") {
            throw new Error(data.message || "Stream error");
          }
        });
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      streamBubble?.remove();
      showError(err.message || "Agent request failed.");
    } finally {
      setBusy(false);
      abortController = null;
    }
  }

  async function loadStatus() {
    try {
      const res = await fetch("/api/portfolio/agent/status");
      const data = await res.json();
      if (!statusPill) return;
      statusPill.hidden = false;
      if (data.available) {
        statusPill.textContent = `${data.provider} · ${data.model}`;
        statusPill.className = "agent-status-pill is-ready";
        if (hint) hint.textContent = "Chats kept 1 week · ★ saves longer · delete with × on the left";
      } else {
        statusPill.textContent = "API key required";
        statusPill.className = "agent-status-pill is-off";
        if (hint) hint.textContent = "Configure LLM in Connect accounts";
        if (askBtn) askBtn.disabled = true;
      }
    } catch {
      if (hint) hint.textContent = "Could not load agent status.";
    }
  }

  function startNewChat() {
    if (abortController) abortController.abort();
    clearConversation();
    if (questionEl) questionEl.focus();
    loadSessions();
  }

  askBtn?.addEventListener("click", () => {
    streamAsk(questionEl?.value || "", { isFollowUp: false });
  });

  questionEl?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      askBtn?.click();
    }
  });

  followupBtn?.addEventListener("click", () => {
    if (!threadId) {
      showError("Start with Ask portfolio agent first.");
      return;
    }
    streamAsk(followupInput?.value || "", { isFollowUp: true });
  });

  followupInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      followupBtn?.click();
    }
  });

  newChatBtn?.addEventListener("click", startNewChat);
  sessionsNewBtn?.addEventListener("click", startNewChat);

  (async function init() {
    await loadSessions();
    const restored = restoreState();
    if (!restored && threadId) {
      await loadSession(threadId);
    } else if (!restored && sessions.length > 0) {
      /* leave blank canvas for new question; sessions visible in sidebar */
    }
    loadStatus();
  })();
})();
