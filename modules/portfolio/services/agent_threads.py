"""Conversation threads for portfolio agent (STM — SQLite-backed)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from modules.portfolio.db.portfolio_cache import connect

_THREAD_TTL_SECONDS = 4 * 60 * 60


def _purge_expired() -> None:
    cutoff = time.time() - _THREAD_TTL_SECONDS
    with connect() as conn:
        stale = conn.execute(
            "SELECT thread_id FROM agent_threads WHERE updated_at < ?", (cutoff,)
        ).fetchall()
        for row in stale:
            tid = row["thread_id"]
            conn.execute("DELETE FROM agent_messages WHERE thread_id = ?", (tid,))
            conn.execute("DELETE FROM agent_threads WHERE thread_id = ?", (tid,))


def _session_title(first_user_message: str | None) -> str:
    text = (first_user_message or "").strip().replace("\n", " ")
    if not text:
        return "Portfolio chat"
    if len(text) <= 72:
        return text
    return text[:69].rstrip() + "…"


def list_sessions(*, limit: int = 50) -> list[dict[str, Any]]:
    """Recent agent threads that have at least one message (for sidebar)."""
    _purge_expired()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                t.thread_id,
                t.created_at,
                t.updated_at,
                (
                    SELECT content FROM agent_messages m
                    WHERE m.thread_id = t.thread_id AND m.role = 'user'
                    ORDER BY m.created_at ASC, m.id ASC
                    LIMIT 1
                ) AS first_user_message,
                (
                    SELECT COUNT(*) FROM agent_messages m
                    WHERE m.thread_id = t.thread_id
                ) AS message_count
            FROM agent_threads t
            WHERE EXISTS (
                SELECT 1 FROM agent_messages m WHERE m.thread_id = t.thread_id
            )
            ORDER BY t.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "thread_id": row["thread_id"],
            "title": _session_title(row["first_user_message"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": int(row["message_count"] or 0),
        }
        for row in rows
    ]


def save_thread_recommendations(thread_id: str, recommendations: dict[str, Any]) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            UPDATE agent_threads
            SET last_recommendations_json = ?, updated_at = ?
            WHERE thread_id = ?
            """,
            (json.dumps(recommendations, default=str), now, thread_id),
        )


def create_thread(*, context: dict[str, Any]) -> str:
    _purge_expired()
    thread_id = str(uuid.uuid4())
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_threads (thread_id, context_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (thread_id, json.dumps(context, default=str), now, now),
        )
    return thread_id


def get_thread(thread_id: str) -> dict[str, Any] | None:
    _purge_expired()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT thread_id, context_json, created_at, updated_at, last_recommendations_json
            FROM agent_threads WHERE thread_id = ?
            """,
            (thread_id,),
        ).fetchone()
        if not row:
            return None
        messages = conn.execute(
            """
            SELECT role, content FROM agent_messages
            WHERE thread_id = ? ORDER BY created_at ASC, id ASC
            """,
            (thread_id,),
        ).fetchall()
    recommendations: dict[str, Any] | None = None
    raw_rec = row["last_recommendations_json"]
    if raw_rec:
        try:
            parsed = json.loads(raw_rec)
            if isinstance(parsed, dict):
                recommendations = parsed
        except json.JSONDecodeError:
            pass

    first_user = next((m["content"] for m in messages if m["role"] == "user"), None)

    return {
        "thread_id": row["thread_id"],
        "title": _session_title(first_user),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "context": json.loads(row["context_json"]),
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "recommendations": recommendations,
    }


def append_message(thread_id: str, role: str, content: str) -> None:
    thread = get_thread(thread_id)
    if not thread:
        raise KeyError(f"Unknown or expired thread: {thread_id}")
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_messages (thread_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (thread_id, role, content, now),
        )
        conn.execute(
            "UPDATE agent_threads SET updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )
