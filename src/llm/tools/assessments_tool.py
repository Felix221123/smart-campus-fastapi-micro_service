# src/llm/tools/assessments_tool.py
from __future__ import annotations

import os
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.llm.cache import cache, make_key
from src.module.models import Assessment, Module  


TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/London"))

_THIS_WEEK_PATTERNS = [r"\bthis\s+week\b", r"\bfor\s+this\s+week\b", r"\bby\s+end\s+of\s+week\b"]
_NEXT_WEEK_PATTERNS = [r"\bnext[-\s]?week\b", r"\bfor\s+next[-\s]?week\b", r"\bfollowing\s+week\b"]
_THIS_MONTH_PATTERNS = [r"\bthis\s+month\b", r"\bfor\s+this\s+month\b"]
_NEXT_MONTH_PATTERNS = [r"\bnext\s+month\b", r"\bfor\s+next\s+month\b"]
_TODAY_PATTERNS = [r"\btoday\b"]
_TOMORROW_PATTERNS = [r"\btomorrow\b"]

_OVERDUE_PATTERNS = [
    r"\boverdue\b",
    r"\bpast\s+due\b",
    r"\bmissed\s+deadline\b",
    r"\bwas\s+due\b",
]

_LIMIT_RE = re.compile(r"\b(?:next|show|list|get)\s+(\d{1,2})\b", re.I)



def _matches_any(q: str, patterns: List[str]) -> bool:
    return any(re.search(p, q) for p in patterns)

def _start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday

def _month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1, day=1)
    else:
        next_month = start.replace(month=start.month + 1, day=1)
    return start, next_month  # [start, next_month)

def _parse_limit(question: str, default: int) -> int:
    m = _LIMIT_RE.search(question or "")
    if not m:
        return default
    n = int(m.group(1))
    return max(1, min(n, 50))


def _range_for_question(question: str) -> Tuple[Optional[datetime], Optional[datetime], str, bool]:
    """
    Returns (start_utc_naive, end_utc_naive, label, overdue_mode)
    - If end is None: open-ended future (or past for overdue)
    - Datetimes returned are UTC *naive* (safe with DBs storing naive UTC).
    """
    q = (question or "").lower()
    now_local = datetime.now(TZ)
    today = now_local.date()

    overdue_mode = _matches_any(q, _OVERDUE_PATTERNS)

    # TODAY / TOMORROW
    if _matches_any(q, _TODAY_PATTERNS):
        start_local = datetime.combine(today, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        label = f"today ({today.isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label, False

    if _matches_any(q, _TOMORROW_PATTERNS):
        d = today + timedelta(days=1)
        start_local = datetime.combine(d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        label = f"tomorrow ({d.isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label, False

    # THIS WEEK / NEXT WEEK
    if _matches_any(q, _THIS_WEEK_PATTERNS):
        start_d = _start_of_week(today)
        start_local = datetime.combine(start_d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=7)
        label = f"this week ({start_d.isoformat()} to {(start_d + timedelta(days=6)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label, False

    if _matches_any(q, _NEXT_WEEK_PATTERNS):
        start_d = _start_of_week(today) + timedelta(days=7)
        start_local = datetime.combine(start_d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=7)
        label = f"next week ({start_d.isoformat()} to {(start_d + timedelta(days=6)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label, False

    # THIS MONTH / NEXT MONTH
    if _matches_any(q, _THIS_MONTH_PATTERNS):
        m_start, m_end = _month_bounds(today)
        start_local = datetime.combine(m_start, time.min, tzinfo=TZ)
        end_local = datetime.combine(m_end, time.min, tzinfo=TZ)
        label = f"this month ({m_start.isoformat()} to {(m_end - timedelta(days=1)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label, False

    if _matches_any(q, _NEXT_MONTH_PATTERNS):
        m_start, m_end = _month_bounds(today)
        # next month bounds
        nm_start = m_end
        nm_end = _month_bounds(nm_start)[1]
        start_local = datetime.combine(nm_start, time.min, tzinfo=TZ)
        end_local = datetime.combine(nm_end, time.min, tzinfo=TZ)
        label = f"next month ({nm_start.isoformat()} to {(nm_end - timedelta(days=1)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label, False

    # OVERDUE (open-ended past)
    if overdue_mode:
        label = "overdue assessments"
        return None, _to_utc_naive(now_local), label, True

    # default upcoming (open-ended future)
    label = "upcoming assessments"
    return _to_utc_naive(now_local), None, label, False

def _to_utc_naive(dt_local: datetime) -> datetime:
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)

def run(db: Session, question: str, limit: int = 8) -> Dict[str, Any]:
    limit = _parse_limit(question, limit)
    start_dt, end_dt, label, overdue_mode = _range_for_question(question)

    ck = make_key("assessments", label, start_dt.isoformat() if start_dt else "None", end_dt.isoformat() if end_dt else "None", limit)
    cached = cache().get(ck)
    if cached is not None:
        return cached

    qry = (
        db.query(
            Assessment.title,
            Assessment.due_date,
            Assessment.weight,
            Module.code,
            Module.name,
        )
        .join(Module, Module.id == Assessment.moduleId)
    )

    if overdue_mode:
        # due_date < end_dt (now)
        qry = qry.filter(Assessment.due_date < end_dt).order_by(Assessment.due_date.desc())
    else:
        if start_dt is not None:
            qry = qry.filter(Assessment.due_date >= start_dt)
        if end_dt is not None:
            qry = qry.filter(Assessment.due_date < end_dt)
        qry = qry.order_by(Assessment.due_date.asc())

    rows = qry.limit(limit).all()

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

    out = {
        "range_label": label,
        "start": start_dt.isoformat() if start_dt else None,
        "end": end_dt.isoformat() if end_dt else None,
        "count": len(items),
        "upcoming": items if not overdue_mode else [],
        "overdue": items if overdue_mode else [],
        "summary": f"Found {len(items)} assessment(s) for {label}.",
    }
    cache().set(ck, out, ttl_seconds=30)
    return out
