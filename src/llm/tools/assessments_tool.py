# src/llm/tools/assessments_tool.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.llm.cache import cache, make_key
from src.module.models import Assessment, Module  # adjust path


def run(db: Session, question: str, limit: int = 8) -> Dict[str, Any]:
    ck = make_key("assessments", limit)
    cached = cache().get(ck)
    if cached is not None:
        return cached

    now = datetime.utcnow()
    rows = (
        db.query(
            Assessment.title,
            Assessment.due_date,
            Assessment.weight,
            Module.code,
            Module.name,
        )
        .join(Module, Module.id == Assessment.module_id)
        .filter(Assessment.due_date >= now)
        .order_by(Assessment.due_date.asc())
        .limit(limit)
        .all()
    )

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "title": r[0],
                "due_date": r[1].isoformat() if r[1] else None,
                "weight": r[2],
                "module_code": r[3],
                "module_name": r[4],
            }
        )

    out = {"upcoming": items}
    cache().set(ck, out, ttl_seconds=30)
    return out
