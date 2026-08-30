"""Portfolio-level AI agent — streaming SSE + follow-up threads (multi-provider)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from modules.portfolio.services.agent_threads import (
    append_message,
    create_thread,
    get_thread,
    save_thread_recommendations,
)
from modules.portfolio.services.llm_config import (
    PROVIDER_ANTHROPIC,
    PROVIDER_GEMINI,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI,
    active_provider,
    agent_configured,
    api_key_for_provider,
    model_name,
    ollama_base_url,
)
from modules.portfolio.services.portfolio_context import build_portfolio_context
from shared.security_redaction import redact_text

_SYSTEM_PROMPT = """You explain a deterministic portfolio advisory payload.
Use ONLY the JSON context provided. Be direct, concise, and answer the exact latest question.

Authority boundary:
- context.advisory is the sole source of truth for every structured action, sell type,
  percentage, target weight, expected return, deadline, and data-quality state.
- Never upgrade WATCH/HOLD into BUY, or downgrade it into SELL.
- Never emit a symbol absent from context.advisory.recommendations.
- Chart patterns and momentum are execution-timing evidence only. They cannot manufacture
  a buy/sell or override a sourced fundamental, governance, reconciliation, or tradability rule.
- If evidence is missing, say UNKNOWN. Never invent filings, prices, tax outcomes, or scores.
- Returns are scenarios, not guarantees. Never claim zero tax without verified product evidence.

Critical — theme & sector classification:
- NEVER infer a company's business from its ticker or partial word in the name.
  Example: GRINFRA (G R Infraprojects) is construction/EPC infrastructure, NOT data centers.
- Use each holding's sector, industry, and business_summary fields from context.
- Ignore any misleading substring in symbols; rely on Yahoo sector/industry/summary.
- growth_themes in context (if present) are heuristic hints only — override them when industry data disagrees.

Explanation rules:
- Use investor_profile and constraints from context (saved under Setup → Goals & guardrails).
- Respect max_pct_per_stock and max_pct_per_sector from constraints for concentration advice.
- Flag breaches using deterministic_flags on holdings and flag fields on sector_allocation.
- Frame xirr_outlook vs investor_profile.target_xirr_pct (not a hardcoded 15%).
- Match tone and risk appetite to investor_profile.risk (conservative / moderate / aggressive).
- Honour cash_buffer_pct in constraints when suggesting deployable capital or rebalance size.
- Prefer growth themes from constraints when industry evidence supports them.
- Do NOT invent holdings or prices not in context.
- Governance/sector risks: say "unknown" if not in context — do not fabricate.
- You may choose which deterministic recommendations are relevant to the question, but you may
  only copy their action and sell_type. Do not independently select or alter an action.
- Lead every symbol explanation with decision_presentation.label, then its readiness and do_now.
- Treat decision_presentation as the shared presentation contract used by Dashboard, Action Center,
  Today Brief, and this agent. Never translate raw action codes into different user-facing labels.
- External analyst views are context only. Never use them to create, reverse, or strengthen an action.
- Legacy buy/sell arrays may include only READY_TO_REVIEW decisions. Blocked, research, tax-review,
  monitor-only, and not-executable items stay in symbols with their gate clearly stated.

Reply with JSON only matching this schema:
{
  "schema_version": "advisor-conversation-v2",
  "symbols": [{
    "symbol": "...",
    "deterministic_action": "copy exactly from context.advisory",
    "sell_type": "copy exactly from context.advisory",
    "decision_label": "copy decision_presentation.label",
    "readiness": "copy decision_presentation.readiness",
    "explanation": "concise explanation grounded in the recommendation",
    "uncertainty": "UNKNOWN or the relevant data-quality limitation"
  }],
  "portfolio_actions": [{"action": "copy a deterministic queue/action", "explanation": "..."}],
  "evidence_used": [{"symbol": "...", "source": "copy source", "as_of": "copy as_of"}],
  "warnings": ["..."],
  "stance": "1–3 sentence overall portfolio view",
  "xirr_outlook": "honest view vs investor_profile.target_xirr_pct given current mix and guardrails",
  "buy": [{"symbol": "...", "action": "add|initiate|watch", "rationale": "...", "horizon": "3y+"}],
  "sell_or_trim": [{"symbol": "...", "action": "trim|exit|watch", "rationale": "..."}],
  "rebalance": [{"action": "...", "detail": "...", "rationale": "..."}],
  "red_flags": ["..."],
  "theme_opportunities": [{"theme": "...", "suggestion": "..."}],
  "macro_view": "brief read of macro block",
  "answer": "direct, specific answer to the user's latest message (required when they asked a question)"
}

Important: The "answer" field MUST address the exact latest question.
Do not repeat a generic portfolio overview unless they asked for one.
The legacy buy/sell/rebalance arrays are compatibility views only. Include only actions copied
from context.advisory; otherwise use empty arrays."""

_FOLLOWUP_PROMPT = """You are continuing a portfolio advisory conversation.
The portfolio context JSON was provided at the start of this thread (includes user_goals and constraints).
Answer the user's follow-up using that context and prior messages in this thread.
Stay concise. Apply the same guardrails (max position %, max sector %, target return, risk profile).
If they ask for trades, use the same JSON schema as the first reply.
For simple follow-ups you may reply with plain text in the "answer" field and leave other arrays empty."""


def agent_available() -> bool:
    return agent_configured()


def agent_status() -> dict[str, Any]:
    provider = active_provider() or "none"
    return {
        "available": agent_available(),
        "provider": provider,
        "model": model_name(),
        "api_configured": agent_configured(),
        "streaming": True,
    }


_PRIVATE_CONTEXT_KEYS = {
    "account_id", "user_id", "owner_ref", "account", "accounts", "account_profile",
    "account_profiles", "tax_profile", "tax_note", "settlement_note", "tax_rule_refs",
    "tax_evidence", "proceeds_by_account", "tax_lots", "lots", "api_key", "access_token",
}


def external_context_preview(context: dict[str, Any]) -> dict[str, Any]:
    """Exact default-deny payload permitted to leave the machine for an external LLM."""
    allow_account_tax = os.getenv("PORTFOLIO_ALLOW_LLM_ACCOUNT_TAX_CONTEXT", "").lower() in {
        "1", "true", "yes", "on",
    }

    def sanitize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: sanitize(item)
                for key, item in value.items()
                if allow_account_tax
                or (key not in _PRIVATE_CONTEXT_KEYS and not key.startswith("tax_"))
            }
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value

    preview = sanitize(deepcopy(context))
    preview["privacy"] = {
        "account_tax_context_shared": allow_account_tax,
        "preview_is_transmitted_context": True,
    }
    return preview


def _provider_error_message(provider: str, status_code: int) -> str:
    label = provider.title() if provider else "LLM provider"
    if status_code == 429:
        return (
            f"{label} rate or quota limit reached. Your local portfolio API is healthy; "
            "wait briefly and retry, then check provider billing/quota and the selected model in Setup if it persists."
        )
    if status_code in {401, 403}:
        return f"{label} rejected the API credentials or model access. Re-save the provider key and model in Setup."
    if status_code == 404:
        return f"{label} could not access the configured model. Select a model available to this API key in Setup."
    if status_code >= 500:
        return f"{label} is temporarily unavailable. Your deterministic Action Center remains available; retry later."
    return f"{label} request failed ({status_code}). Provider details were redacted; verify the model and credentials in Setup."


def _deterministic_provider_fallback(
    *,
    context: dict[str, Any],
    question: str,
    provider: str,
    status_code: int,
) -> dict[str, Any]:
    """Use only the local audited decision set when narrative generation fails."""
    recommendations = list((context.get("advisory") or {}).get("recommendations") or [])
    priority = {"SELL": 0, "REDUCE": 1, "RECONCILE": 2, "STRONG_ADD": 3, "ADD": 4}
    selected = sorted(
        [row for row in recommendations if str(row.get("action")) in priority],
        key=lambda row: (
            priority[str(row.get("action"))],
            -float(row.get("family_weight_pct") or 0),
        ),
    )[:10]
    symbols = []
    for row in selected:
        flags = row.get("data_quality_flags") or []
        presentation = row.get("decision_presentation") or {}
        label = str(presentation.get("label") or row.get("action") or "Decision unavailable")
        readiness = str(presentation.get("readiness") or "DATA_BLOCKED")
        do_now = str(presentation.get("do_now") or row.get("why_now") or "")
        symbols.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "deterministic_action": str(row.get("action") or "WATCH"),
                "sell_type": str(row.get("sell_type") or "NONE"),
                "decision_label": label,
                "readiness": readiness,
                "explanation": f"{label}. {do_now}",
                "uncertainty": str(
                    (flags[0].get("message") if flags else None)
                    or "Review the dated evidence before acting."
                ),
            }
        )
    asks_return = "xirr" in question.lower() or "return" in question.lower()
    answer = (
        "A target XIRR is a stretch objective, not a guarantee. The external LLM could not "
        "generate a narrative, so the app is showing only the current deterministic action queue; "
        "review its ADD/REDUCE rows and evidence in Action Center."
        if asks_return
        else "The external LLM could not generate a narrative, so the app is showing the highest-priority deterministic decisions without changing any action."
    )
    warning = _provider_error_message(provider, status_code)
    return {
        "schema_version": "advisor-conversation-v2",
        "symbols": symbols,
        "portfolio_actions": [],
        "evidence_used": [],
        "warnings": [warning],
        "stance": "Deterministic fallback — no LLM-authored interpretation was used.",
        "xirr_outlook": "Unavailable from the provider fallback; true XIRR requires dated cash flows.",
        "buy": [
            {
                "symbol": row["symbol"],
                "action": "add",
                "rationale": row["explanation"],
                "horizon": "3y+",
            }
            for row in symbols
            if row["readiness"] == "READY_TO_REVIEW"
            and row["deterministic_action"] in {"ADD", "STRONG_ADD"}
        ],
        "sell_or_trim": [
            {
                "symbol": row["symbol"],
                "action": "exit" if row["deterministic_action"] == "SELL" else "trim",
                "rationale": row["explanation"],
            }
            for row in symbols
            if row["readiness"] == "READY_TO_REVIEW"
            and row["deterministic_action"] in {"SELL", "REDUCE"}
        ],
        "rebalance": [],
        "red_flags": [warning],
        "theme_opportunities": [],
        "macro_view": "Unavailable while the narrative provider is rate-limited.",
        "answer": answer,
        "degraded": True,
    }


def _parse_agent_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError("Agent response must be a JSON object")
    return parsed


def _validate_agent_response(
    parsed: dict[str, Any],
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Replace model-selected actions with deterministic values and drop contradictions."""
    advisory = context.get("advisory") or {}
    deterministic = {
        str(item.get("symbol") or "").upper(): item
        for item in advisory.get("recommendations") or []
        if item.get("symbol")
    }
    warnings = [str(item) for item in parsed.get("warnings") or []]
    requested_rows = list(parsed.get("symbols") or [])
    for row in parsed.get("buy") or []:
        requested_rows.append(
            {
                "symbol": row.get("symbol"),
                "explanation": row.get("rationale"),
                "uncertainty": "",
            }
        )
    for row in parsed.get("sell_or_trim") or []:
        requested_rows.append(
            {
                "symbol": row.get("symbol"),
                "explanation": row.get("rationale"),
                "uncertainty": "",
            }
        )

    symbols: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in requested_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        recommendation = deterministic.get(symbol)
        if not recommendation:
            if symbol:
                warnings.append(f"Removed unknown symbol from model output: {symbol}.")
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        supplied_action = str(row.get("deterministic_action") or "").upper()
        actual_action = str(recommendation.get("action") or "WATCH")
        if supplied_action and supplied_action != actual_action:
            warnings.append(
                f"Corrected {symbol} action from {supplied_action} to deterministic {actual_action}."
            )
        flags = recommendation.get("data_quality_flags") or []
        presentation = recommendation.get("decision_presentation") or {}
        label = str(presentation.get("label") or actual_action)
        readiness = str(presentation.get("readiness") or "DATA_BLOCKED")
        uncertainty = str(row.get("uncertainty") or "").strip()
        if not uncertainty and flags:
            uncertainty = str(flags[0].get("message") or flags[0].get("code") or "UNKNOWN")
        symbols.append(
            {
                "symbol": symbol,
                "deterministic_action": actual_action,
                "sell_type": str(recommendation.get("sell_type") or "NONE"),
                "decision_label": label,
                "readiness": readiness,
                "explanation": (
                    f"{label}. "
                    f"{str(row.get('explanation') or presentation.get('do_now') or recommendation.get('why_now') or '')}"
                ),
                "uncertainty": uncertainty or "No additional uncertainty supplied.",
            }
        )

    buy = [
        {
            "symbol": row["symbol"],
            "action": "add",
            "rationale": row["explanation"],
            "horizon": "3y+",
        }
        for row in symbols
        if row["readiness"] == "READY_TO_REVIEW"
        and row["deterministic_action"] in {"ADD", "STRONG_ADD"}
    ]
    sell = [
        {
            "symbol": row["symbol"],
            "action": "exit" if row["deterministic_action"] == "SELL" else "trim",
            "rationale": row["explanation"],
        }
        for row in symbols
        if row["readiness"] == "READY_TO_REVIEW"
        and row["deterministic_action"] in {"REDUCE", "SELL"}
    ]
    if parsed.get("rebalance"):
        warnings.append(
            "Model-authored rebalance instructions were removed; use the deterministic rebalance evaluator."
        )

    evidence_used: list[dict[str, Any]] = []
    for row in symbols:
        recommendation = deterministic[row["symbol"]]
        for item in recommendation.get("evidence") or []:
            evidence_used.append(
                {
                    "symbol": row["symbol"],
                    "source": item.get("source"),
                    "as_of": item.get("as_of"),
                }
            )

    return {
        "schema_version": "advisor-conversation-v2",
        "symbols": symbols,
        "portfolio_actions": [],
        "evidence_used": evidence_used,
        "warnings": list(dict.fromkeys(warnings)),
        "stance": str(parsed.get("stance") or ""),
        "xirr_outlook": str(parsed.get("xirr_outlook") or ""),
        "buy": buy,
        "sell_or_trim": sell,
        "rebalance": [],
        "red_flags": [str(item) for item in parsed.get("red_flags") or []],
        "theme_opportunities": list(parsed.get("theme_opportunities") or []),
        "macro_view": str(parsed.get("macro_view") or ""),
        "answer": str(parsed.get("answer") or parsed.get("stance") or ""),
    }


def _malformed_json_fallback(
    content: str,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Keep deterministic advice available when an LLM violates its JSON contract."""
    recommendations = list((context.get("advisory") or {}).get("recommendations") or [])
    decisions = []
    for item in recommendations[:5]:
        presentation = item.get("decision_presentation") or {}
        decisions.append(
            f"{item.get('symbol')}: {presentation.get('label') or 'Decision unavailable'} "
            f"({presentation.get('readiness_label') or 'data blocked'})"
        )
    answer = (
        "The narrative response was malformed. Current deterministic decisions: "
        + ("; ".join(decisions) if decisions else "none available")
        + ". Open Action Center for the evidence and gates."
    )
    return {
        "schema_version": "advisor-conversation-v2",
        "symbols": [],
        "portfolio_actions": [],
        "evidence_used": [],
        "warnings": ["LLM response was malformed; deterministic advisory remains authoritative."],
        "stance": "",
        "xirr_outlook": "",
        "buy": [],
        "sell_or_trim": [],
        "rebalance": [],
        "red_flags": ["LLM response was malformed; deterministic advisory remains authoritative."],
        "theme_opportunities": [],
        "macro_view": "",
        "answer": answer,
        "provider_output_redacted": bool(content),
        "deterministic_advisory": context.get("advisory"),
    }


def _assistant_history_text(recommendations: dict[str, Any], full_text: str) -> str:
    """Store a short assistant turn for follow-ups (avoids repeating full JSON)."""
    answer = (recommendations.get("answer") or "").strip()
    stance = (recommendations.get("stance") or "").strip()
    parts: list[str] = []
    if answer:
        parts.append(answer)
    elif stance:
        parts.append(stance)
    if parts:
        return "\n".join(parts)
    return full_text[:8000]


def _chat_messages(
    *,
    context: dict[str, Any],
    question: str,
    thread: dict[str, Any] | None,
) -> list[dict[str, str]]:
    q = question.strip()
    if thread and thread.get("messages"):
        messages: list[dict[str, str]] = [
            {"role": "system", "content": f"{_SYSTEM_PROMPT}\n\n{_FOLLOWUP_PROMPT}"},
            {
                "role": "user",
                "content": (
                    "Portfolio context for this thread:\n"
                    f"{json.dumps(external_context_preview(thread['context']), default=str)}\n\n"
                    "Use this context for all follow-ups."
                ),
            },
        ]
        for msg in thread["messages"]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append(
            {
                "role": "user",
                "content": f"Follow-up question (answer this specifically):\n{q}",
            }
        )
        return messages

    user_parts = [
        f"User question (answer this first in the \"answer\" field; be specific):\n{q}",
        f"Portfolio context JSON:\n{json.dumps(external_context_preview(context), default=str)}",
        "Fill the JSON schema. Tailor stance, buy/sell, and rebalance to the question — not a generic template.",
    ]

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _split_system_messages(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    system_parts: list[str] = []
    rest: list[dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(msg["content"])
        else:
            rest.append(msg)
    return "\n\n".join(system_parts), rest


def _stream_openai(messages: list[dict[str, str]]) -> Iterator[str]:
    api_key = api_key_for_provider(PROVIDER_OPENAI)
    if not api_key:
        raise RuntimeError("OpenAI API key not configured")

    body = json.dumps(
        {
            "model": model_name(),
            "messages": messages,
            "temperature": 0.3,
            "stream": True,
            "response_format": {"type": "json_object"},
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded or not decoded.startswith("data:"):
                continue
            payload = decoded[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                yield text


def _stream_anthropic(messages: list[dict[str, str]]) -> Iterator[str]:
    api_key = api_key_for_provider(PROVIDER_ANTHROPIC)
    if not api_key:
        raise RuntimeError("Anthropic API key not configured")

    system_text, chat = _split_system_messages(messages)
    anthropic_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in chat
        if m["role"] in ("user", "assistant")
    ]

    body = json.dumps(
        {
            "model": model_name(),
            "max_tokens": 4096,
            "system": system_text,
            "messages": anthropic_messages,
            "stream": True,
            "temperature": 0.3,
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded.startswith("data:"):
                continue
            payload = decoded[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("type") == "content_block_delta":
                delta = chunk.get("delta") or {}
                text = delta.get("text")
                if text:
                    yield text


def _stream_ollama(messages: list[dict[str, str]]) -> Iterator[str]:
    base = ollama_base_url()
    body = json.dumps(
        {
            "model": model_name(),
            "messages": messages,
            "stream": True,
            "format": "json",
            "options": {"temperature": 0.3},
        }
    ).encode()

    req = urllib.request.Request(
        f"{base}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=300) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            try:
                chunk = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            msg = chunk.get("message") or {}
            text = msg.get("content")
            if text:
                yield text
            if chunk.get("done"):
                break


def _stream_gemini(messages: list[dict[str, str]]) -> Iterator[str]:
    api_key = api_key_for_provider(PROVIDER_GEMINI)
    if not api_key:
        raise RuntimeError("Gemini API key not configured")

    system_text, chat = _split_system_messages(messages)
    contents = []
    for msg in chat:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    model = model_name()
    if not model.startswith("models/"):
        model_path = f"models/{model}"
    else:
        model_path = model

    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.3,
                "responseMimeType": "application/json",
            },
        }
    ).encode()

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/{model_path}:"
        f"streamGenerateContent?alt=sse&key={api_key}"
    )
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded.startswith("data:"):
                continue
            payload = decoded[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            candidates = chunk.get("candidates") or []
            if not candidates:
                continue
            parts = (candidates[0].get("content") or {}).get("parts") or []
            for part in parts:
                text = part.get("text")
                if text:
                    yield text


def _stream_llm_sse(*, messages: list[dict[str, str]]) -> Iterator[str]:
    provider = active_provider()
    if provider == PROVIDER_OPENAI:
        yield from _stream_openai(messages)
    elif provider == PROVIDER_ANTHROPIC:
        yield from _stream_anthropic(messages)
    elif provider == PROVIDER_OLLAMA:
        yield from _stream_ollama(messages)
    elif provider == PROVIDER_GEMINI:
        yield from _stream_gemini(messages)
    else:
        raise RuntimeError(
            "LLM provider not configured. Open Connect accounts → Portfolio agent (LLM)."
        )


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def stream_portfolio_agent(
    *,
    question: str | None = None,
    thread_id: str | None = None,
    refresh: bool = False,
    new_thread: bool = False,
) -> Iterator[str]:
    """
    Server-Sent Events stream (grpc-style typed events for clients).

    Events: status | token | done | error
    """
    if not agent_available():
        yield _format_sse(
            "error",
            {"message": "LLM not configured — set provider in Connect accounts → Portfolio agent"},
        )
        return

    user_message = (question or "").strip()
    if not user_message:
        user_message = "Give portfolio-level recommendations for the next 3+ years."

    provider = active_provider() or "unknown"
    started = time.perf_counter()
    context: dict[str, Any] | None = None
    active_thread_id: str | None = None

    def record(status: str, error_code: str | None = None) -> None:
        try:
            from modules.portfolio.db.operating_console import record_provider_event

            record_provider_event(
                provider=provider,
                operation="portfolio_agent",
                duration_ms=(time.perf_counter() - started) * 1000,
                status=status,
                error_code=error_code,
            )
        except Exception:
            pass

    try:
        if new_thread:
            thread_id = None
        thread = get_thread(thread_id) if thread_id else None
        if thread_id and not thread:
            yield _format_sse("error", {"message": "Conversation expired. Start a new thread."})
            return

        if thread:
            context = thread["context"]
            active_thread_id = thread_id
        else:
            yield _format_sse("status", {"message": "Loading portfolio context…"})
            context = build_portfolio_context(refresh=refresh)
            active_thread_id = create_thread(context=context)
            yield _format_sse(
                "status",
                {"message": f"Analyzing with {provider}…", "thread_id": active_thread_id},
            )

        messages = _chat_messages(context=context, question=user_message, thread=thread)
        append_message(active_thread_id, "user", user_message)

        parts: list[str] = []
        for delta in _stream_llm_sse(messages=messages):
            parts.append(delta)
            yield _format_sse("token", {"delta": delta})

        full_text = "".join(parts)
        if not full_text.strip():
            raise RuntimeError(
                f"{provider} returned an empty response. Check the configured model and API access."
            )
        try:
            recommendations = _validate_agent_response(
                _parse_agent_json(full_text),
                context=context,
            )
        except (json.JSONDecodeError, ValueError):
            recommendations = _malformed_json_fallback(full_text, context=context)

        append_message(
            active_thread_id,
            "assistant",
            _assistant_history_text(recommendations, full_text),
        )
        save_thread_recommendations(active_thread_id, recommendations)
        record("OK")

        yield _format_sse(
            "done",
            {
                "thread_id": active_thread_id,
                "question": user_message,
                "recommendations": recommendations,
                "context_meta": {
                    "holdings_count": len(context.get("holdings") or []),
                    "cached_at": context.get("cached_at"),
                    "from_cache": context.get("from_cache"),
                },
            },
        )
    except urllib.error.HTTPError as exc:
        record("ERROR", f"HTTP_{exc.code}")
        message = _provider_error_message(provider, exc.code)
        if context is not None and active_thread_id:
            fallback = _deterministic_provider_fallback(
                context=context,
                question=user_message,
                provider=provider,
                status_code=exc.code,
            )
            append_message(active_thread_id, "assistant", fallback["answer"])
            save_thread_recommendations(active_thread_id, fallback)
            yield _format_sse(
                "done",
                {
                    "thread_id": active_thread_id,
                    "question": user_message,
                    "recommendations": fallback,
                    "degraded": True,
                    "provider_error": {
                        "status_code": exc.code,
                        "message": message,
                        "setup_path": "/portfolio/setup",
                    },
                    "context_meta": {
                        "holdings_count": len(context.get("holdings") or []),
                        "cached_at": context.get("cached_at"),
                        "from_cache": context.get("from_cache"),
                    },
                },
            )
        else:
            yield _format_sse(
                "error",
                {
                    "message": message,
                    "status_code": exc.code,
                    "setup_path": "/portfolio/setup",
                },
            )
    except Exception as exc:
        record("ERROR", type(exc).__name__)
        yield _format_sse("error", {"message": redact_text(exc, limit=300)})


def ask_portfolio_agent(
    *,
    question: str | None = None,
    thread_id: str | None = None,
    refresh: bool = False,
    new_thread: bool = False,
) -> dict[str, Any]:
    """Non-streaming fallback — collects full SSE stream."""
    result: dict[str, Any] | None = None
    error: str | None = None

    for chunk in stream_portfolio_agent(
        question=question,
        thread_id=thread_id,
        refresh=refresh,
        new_thread=new_thread,
    ):
        if chunk.startswith("event: done"):
            line = chunk.split("\n", 1)[1]
            if line.startswith("data: "):
                result = json.loads(line[6:])
        elif chunk.startswith("event: error"):
            line = chunk.split("\n", 1)[1]
            if line.startswith("data: "):
                error = json.loads(line[6:]).get("message", "Unknown error")

    if error:
        raise RuntimeError(error)
    if not result:
        raise RuntimeError("Agent returned no response")

    return {
        "status": agent_status(),
        "thread_id": result.get("thread_id"),
        "question": result.get("question"),
        "recommendations": result.get("recommendations"),
        "context_meta": result.get("context_meta"),
    }
