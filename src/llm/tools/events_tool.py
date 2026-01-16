# src/llm/tools/events_tool.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.llm.cache import cache, make_key
from src.module.models import Event, Society  # adjust path


def run(db: Session, question: str, limit: int = 8) -> Dict[str, Any]:
    ck = make_key("events", limit)
    cached = cache().get(ck)
    if cached is not None:
        return cached

    now = datetime.utcnow()
    rows = (
        db.query(
            Event.title,
            Event.start_time,
            Event.end_time,
            Event.location,
            Event.mode,
            Society.name,
        )
        .outerjoin(Society, Society.id == Event.organiser_society_id)
        .filter(Event.start_time >= now)
        .order_by(Event.start_time.asc())
        .limit(limit)
        .all()
    )

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "title": r[0],
                "start_time": r[1].isoformat() if r[1] else None,
                "end_time": r[2].isoformat() if r[2] else None,
                "location": r[3],
                "mode": r[4],
                "organiser": r[5],
            }
        )

    out = {"upcoming": items}
    cache().set(ck, out, ttl_seconds=30)
    return out
