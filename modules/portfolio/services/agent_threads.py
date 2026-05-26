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
            "SELECT thread_id, context_json, created_at, updated_at FROM agent_threads WHERE thread_id = ?",
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
    return {
        "thread_id": row["thread_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "context": json.loads(row["context_json"]),
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
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
