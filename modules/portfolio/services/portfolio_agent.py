"""Portfolio-level AI agent — streaming SSE + follow-up threads (multi-provider)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
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

_SYSTEM_PROMPT = """You are a personal portfolio advisor for an Indian equity investor.
Use ONLY the JSON context provided (holdings with sector/industry/business summaries, signals, macro).
Be direct and actionable — personal use only.

Critical — theme & sector classification:
- NEVER infer a company's business from its ticker or partial word in the name.
  Example: GRINFRA (G R Infraprojects) is construction/EPC infrastructure, NOT data centers.
- Use each holding's sector, industry, and business_summary fields from context.
- Ignore any misleading substring in symbols; rely on Yahoo sector/industry/summary.
- growth_themes in context (if present) are heuristic hints only — override them when industry data disagrees.

Rules:
- Respect max % per stock and per sector from constraints.
- Flag concentration and high debt/equity using deterministic_flags.
- Prefer growth themes from constraints when industry evidence supports them.
- Horizon 3+ years, aggressive risk, growth goal, ~15% XIRR target, OK with 15–20% drawdown.
- Do NOT invent holdings or prices not in context.
- Governance/sector risks: say "unknown" if not in context — do not fabricate.

Reply with JSON only matching this schema:
{
  "stance": "1–3 sentence overall portfolio view",
  "xirr_outlook": "honest view vs 15% target given current mix",
  "buy": [{"symbol": "...", "action": "add|initiate|watch", "rationale": "...", "horizon": "3y+"}],
  "sell_or_trim": [{"symbol": "...", "action": "trim|exit|watch", "rationale": "..."}],
  "rebalance": [{"action": "...", "detail": "...", "rationale": "..."}],
  "red_flags": ["..."],
  "theme_opportunities": [{"theme": "...", "suggestion": "..."}],
  "macro_view": "brief read of macro block",
  "answer": "direct, specific answer to the user's latest message (required when they asked a question)"
}

Important: Each user message is different. The "answer" field MUST address their exact question.
Do not repeat a generic portfolio overview unless they asked for one.
Update buy/sell/rebalance only when relevant to their question; otherwise use empty arrays."""

_FOLLOWUP_PROMPT = """You are continuing a portfolio advisory conversation.
The portfolio context JSON was provided at the start of this thread.
Answer the user's follow-up using that context and prior messages in this thread.
Stay concise. If they ask for trades, use the same JSON schema as the first reply.
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
                    f"{json.dumps(thread['context'], default=str)}\n\n"
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
        f"Portfolio context JSON:\n{json.dumps(context, default=str)}",
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
        try:
            recommendations = _parse_agent_json(full_text)
        except (json.JSONDecodeError, ValueError):
            recommendations = {
                "stance": "",
                "xirr_outlook": "",
                "buy": [],
                "sell_or_trim": [],
                "rebalance": [],
                "red_flags": [],
                "theme_opportunities": [],
                "macro_view": "",
                "answer": full_text,
            }

        append_message(
            active_thread_id,
            "assistant",
            _assistant_history_text(recommendations, full_text),
        )
        save_thread_recommendations(active_thread_id, recommendations)

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
        detail = exc.read().decode(errors="replace")[:500]
        yield _format_sse("error", {"message": f"LLM API error ({exc.code}): {detail}"})
    except Exception as exc:
        yield _format_sse("error", {"message": str(exc)})


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
