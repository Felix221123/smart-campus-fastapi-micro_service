# src/llm/tools/spaces_tool.py
from __future__ import annotations
from typing import Any, Dict, List

from sqlalchemy.orm import Session
from sqlalchemy import desc

from src.module.models import Space  # adjust path


def run(db: Session, question: str, limit: int = 10) -> Dict[str, Any]:
    q = (question or "").lower()

    # very light heuristics
    want_accessible = "accessible" in q
    want_quiet = "quiet" in q or "silent" in q
    want_library = "library" in q

    qry = db.query(Space)

    if want_accessible:
        qry = qry.filter(Space.is_accessible.is_(True))

    if want_library:
        qry = qry.filter(Space.location.ilike("%library%"))

    if want_quiet:
        qry = qry.order_by(desc(Space.quiet_score.nullslast()))
    else:
        qry = qry.order_by(Space.name.asc())

    rows = qry.limit(limit).all()

    spaces: List[Dict[str, Any]] = []
    for s in rows:
        spaces.append(
            {
                "space_id": str(s.id),
                "name": s.name,
                "type": s.type,
                "location": s.location,
                "quiet_score": float(s.quiet_score) if s.quiet_score is not None else None,
                "is_accessible": bool(s.is_accessible),
                "capacity": s.capacity,
            }
        )

    return {"spaces": spaces}
