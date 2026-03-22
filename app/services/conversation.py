"""
Conversation History Service
=============================
Stores and retrieves per-session Q&A history in Supabase.

Schema (see sql/conversation_sessions.sql):
    conversation_sessions(session_id TEXT, event_id TEXT, messages JSONB)
    UNIQUE (session_id, event_id)

Strategy for long context:
    Keep the last MAX_HISTORY_TURNS question/answer pairs.
    Each turn = 2 messages: {"role":"user"} + {"role":"assistant"}.
    Older turns are silently dropped — no summarisation needed for
    typical sports session lengths (< 10 questions per match).
"""

import json
import logging
from typing import Optional

from app.db.connection import acquire

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 6  # 6 Q&A pairs → up to 12 messages in context


async def load_history(session_id: str, event_id: str) -> list[dict]:
    """Return the trimmed message list for (session_id, event_id).

    Returns [] if no session exists or DB is unavailable.
    The list is ready to be inserted between the system prompt and the
    current user message in a chat.completions.create() call.
    """
    try:
        async with acquire() as conn:
            row = await conn.fetchrow(
                "SELECT messages FROM conversation_sessions "
                "WHERE session_id = $1 AND event_id = $2",
                session_id, event_id,
            )
    except Exception as exc:
        logger.warning("conversation.load_history failed: %s", exc)
        return []

    if not row:
        return []

    # Supabase pgBouncer returns JSONB as a raw string; decode if needed.
    messages = row["messages"]
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except (ValueError, TypeError):
            return []
    if not isinstance(messages, list):
        return []

    # Window: keep only the most recent MAX_HISTORY_TURNS pairs
    max_msgs = MAX_HISTORY_TURNS * 2
    if len(messages) > max_msgs:
        messages = messages[-max_msgs:]

    return messages


async def save_turn(
    session_id: str,
    event_id: str,
    question: str,
    response_headline: str,
    response_analysis: str,
) -> None:
    """Append a user/assistant pair to the session.

    The assistant content is a concise summary (headline + analysis) so
    the model has meaningful context without the full JSON structure.
    """
    new_pair = json.dumps([
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": f"{response_headline} — {response_analysis}",
        },
    ])

    try:
        async with acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_sessions (session_id, event_id, messages)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (session_id, event_id) DO UPDATE
                    SET messages   = conversation_sessions.messages || $3::jsonb,
                        updated_at = NOW()
                """,
                session_id, event_id, new_pair,
            )
    except Exception as exc:
        logger.warning("conversation.save_turn failed: %s", exc)


async def clear_session(session_id: str, event_id: str) -> None:
    """Delete the session history (e.g. user clicks 'reset chat')."""
    try:
        async with acquire() as conn:
            await conn.execute(
                "DELETE FROM conversation_sessions "
                "WHERE session_id = $1 AND event_id = $2",
                session_id, event_id,
            )
    except Exception as exc:
        logger.warning("conversation.clear_session failed: %s", exc)
