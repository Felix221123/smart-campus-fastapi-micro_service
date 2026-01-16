# DB-backed memory:
# create conversation
# append messages
# fetch last N messages
# store conversation metadata (pending_action etc)
# log agent events

# src/llm/memory.py
from __future__ import annotations

import os
import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text
from src.llm.cache import cache, make_key


DB_SCHEMA = os.getenv("DB_SCHEMA", "public")



def _meta_key(cid: str) -> str:
    return make_key("meta", DB_SCHEMA, cid)

def _msgs_key(cid: str, limit: int) -> str:
    return make_key("msgs", DB_SCHEMA, cid, limit)


def ensure_conversation(
    db: Session,
    user_id: Optional[str],
    channel: str = "web",
    title: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    if conversation_id:
        # validate exists
        row = db.execute(
            text(f'SELECT id FROM "{DB_SCHEMA}".assistant_conversations WHERE id = :id'),
            {"id": conversation_id},
        ).fetchone()
        if row:
            return conversation_id

    new_id = str(uuid.uuid4())
    db.execute(
        text(
            f'''
            INSERT INTO "{DB_SCHEMA}".assistant_conversations
            (id, user_id, title, channel, metadata, created_at, updated_at)
            VALUES (:id, :user_id, :title, :channel, CAST(:metadata AS jsonb), now(), now())
            '''
        ),
        {
            "id": new_id,
            "user_id": user_id,
            "title": title,
            "channel": channel,
            "metadata": json.dumps({}),
        },
    )
    db.commit()
    return new_id


def get_metadata(db: Session, conversation_id: str) -> Dict[str, Any]:
    c = cache()
    k = _meta_key(conversation_id)
    hit = c.get(k)
    if hit is not None:
        return hit

    row = db.execute(
        text(f'SELECT metadata FROM "{DB_SCHEMA}".assistant_conversations WHERE id = :id'),
        {"id": conversation_id},
    ).fetchone()

    if not row or row[0] is None:
        c.set(k, {}, ttl_seconds=60)
        return {}

    val = row[0]
    if isinstance(val, str):
        meta = json.loads(val)
    else:
        meta = dict(val)

    c.set(k, meta, ttl_seconds=60)
    return meta




def set_metadata(db: Session, conversation_id: str, metadata: Dict[str, Any]) -> None:
    db.execute(
        text(
            f'''
            UPDATE "{DB_SCHEMA}".assistant_conversations
            SET metadata = CAST(:metadata AS jsonb), updated_at = now()
            WHERE id = :id
            '''
        ),
        {"id": conversation_id, "metadata": json.dumps(metadata)},
    )
    db.commit()

    c = cache()
    c.set(_meta_key(conversation_id), metadata, ttl_seconds=60)



def append_message(
    db: Session,
    conversation_id: str,
    role: str,
    content: str,
    tool_name: Optional[str] = None,
    tool_payload: Optional[Dict[str, Any]] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    latency_ms: Optional[int] = None,
) -> None:
    db.execute(
        text(
            f'''
            INSERT INTO "{DB_SCHEMA}".assistant_messages
            (id, conversation_id, role, content, tool_name, tool_payload, tokens_in, tokens_out, latency_ms, created_at)
            VALUES (:id, :conversation_id, :role, :content, :tool_name, CAST(:tool_payload AS jsonb), :tokens_in, :tokens_out, :latency_ms, now())
            '''
        ),
        {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "tool_name": tool_name,
            "tool_payload": json.dumps(tool_payload or {}) if tool_payload is not None else None,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": latency_ms,
        },
    )
    db.commit()

    c = cache()
    # invalidate common message windows
    for lim in (6, 8, 12, 20):
        c.delete(_msgs_key(conversation_id, lim))


def recent_messages(db: Session, conversation_id: str, limit: int = 12) -> List[Dict[str, str]]:
    c = cache()
    k = _msgs_key(conversation_id, limit)
    hit = c.get(k)
    if hit is not None:
        return hit

    rows = db.execute(
        text(
            f'''
            SELECT role, content
            FROM "{DB_SCHEMA}".assistant_messages
            WHERE conversation_id = :cid
            ORDER BY created_at DESC
            LIMIT :limit
            '''
        ),
        {"cid": conversation_id, "limit": limit},
    ).fetchall()

    rows = list(reversed(rows))
    out = [{"role": r[0], "content": r[1]} for r in rows]
    c.set(k, out, ttl_seconds=15)  # short TTL
    return out



def log_event(
    db: Session,
    conversation_id: str,
    user_id: Optional[str],
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    db.execute(
        text(
            f'''
            INSERT INTO "{DB_SCHEMA}".assistant_agent_events
            (id, conversation_id, user_id, event_type, payload, created_at)
            VALUES (:id, :cid, :uid, :event_type, CAST(:payload AS jsonb), now())
            '''
        ),
        {
            "id": str(uuid.uuid4()),
            "cid": conversation_id,
            "uid": user_id,
            "event_type": event_type,
            "payload": json.dumps(payload),
        },
    )
    db.commit()
