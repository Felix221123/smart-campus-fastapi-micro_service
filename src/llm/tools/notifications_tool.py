# src/llm/tools/notifications_tool.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.module.models import Notification  


def run(db: Session, user_id: str, limit: int = 10) -> Dict[str, Any]:
    now = datetime.utcnow()

    rows = (
        db.query(
            Notification.id,
            Notification.title,
            Notification.body,
            Notification.type,
            Notification.created_at,
            Notification.scheduled_for,
            Notification.is_read,
        )
        .filter(Notification.user_id == user_id)
        .filter(Notification.is_read.is_(False))
        .filter((Notification.scheduled_for.is_(None)) | (Notification.scheduled_for <= now))
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "id": str(r[0]),
                "title": r[1],
                "body": r[2],
                "type": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "scheduled_for": r[5].isoformat() if r[5] else None,
                "is_read": bool(r[6]),
            }
        )

    return {"unread": items, "count": len(items)}
