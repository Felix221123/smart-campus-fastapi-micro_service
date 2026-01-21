# src/llm/tools/events_tool.py
from __future__ import annotations

import os
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple
from datetime import timezone

from sqlalchemy.orm import Session

from src.llm.cache import cache, make_key
from src.module.models import Event, Society 

TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/London"))

_THIS_WEEK_PATTERNS = [r"\bthis\s+week\b", r"\bfor\s+this\s+week\b"]
_NEXT_WEEK_PATTERNS = [r"\bnext[-\s]?week\b", r"\bfor\s+next[-\s]?week\b", r"\bfollowing\s+week\b"]
_TODAY_PATTERNS = [r"\btoday\b"]
_TOMORROW_PATTERNS = [r"\btomorrow\b"]
_WEEKEND_PATTERNS = [r"\bthis\s+weekend\b", r"\bweekend\b"]

_MODE_ONLINE = [r"\bonline\b", r"\bvirtual\b", r"\bzoom\b", r"\bteams\b"]
_MODE_INPERSON = [r"\bin[-\s]?person\b", r"\bon\s*campus\b", r"\bface[-\s]?to[-\s]?face\b"]
_MODE_HYBRID = [r"\bhybrid\b"]

_LIMIT_RE = re.compile(r"\b(?:next|show|list|get)\s+(\d{1,2})\b", re.I)


def _matches_any(q: str, patterns: List[str]) -> bool:
    return any(re.search(p, q) for p in patterns)

def _parse_limit(question: str, default: int) -> int:
    m = _LIMIT_RE.search(question or "")
    if not m:
        return default
    n = int(m.group(1))
    return max(1, min(n, 50))

def _start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday

def _to_utc_naive(dt_local: datetime) -> datetime:
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def _to_local_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).isoformat()



def _range_for_question(question: str) -> Tuple[datetime, datetime, str]:
    q = (question or "").lower()
    now_local = datetime.now(TZ)
    today = now_local.date()

    if _matches_any(q, _TODAY_PATTERNS):
        start_local = datetime.combine(today, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), f"today ({today.isoformat()})"

    if _matches_any(q, _TOMORROW_PATTERNS):
        d = today + timedelta(days=1)
        start_local = datetime.combine(d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), f"tomorrow ({d.isoformat()})"

    if _matches_any(q, _WEEKEND_PATTERNS):
        start_week = _start_of_week(today)
        saturday = start_week + timedelta(days=5)
        monday_after = start_week + timedelta(days=7)
        start_local = datetime.combine(saturday, time.min, tzinfo=TZ)
        end_local = datetime.combine(monday_after, time.min, tzinfo=TZ)
        label = f"this weekend ({saturday.isoformat()} to {(monday_after - timedelta(days=1)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label

    if _matches_any(q, _THIS_WEEK_PATTERNS):
        start_d = _start_of_week(today)
        start_local = datetime.combine(start_d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=7)
        label = f"this week ({start_d.isoformat()} to {(start_d + timedelta(days=6)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label

    if _matches_any(q, _NEXT_WEEK_PATTERNS):
        start_d = _start_of_week(today) + timedelta(days=7)
        start_local = datetime.combine(start_d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=7)
        label = f"next week ({start_d.isoformat()} to {(start_d + timedelta(days=6)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label

    # default: upcoming next 30 days
    start_local = now_local
    end_local = now_local + timedelta(days=30)
    return _to_utc_naive(start_local), _to_utc_naive(end_local), "upcoming events (next 30 days)"

def _mode_filter(question: str) -> Optional[str]:
    q = (question or "").lower()
    if _matches_any(q, _MODE_HYBRID):
        return "hybrid"
    if _matches_any(q, _MODE_ONLINE):
        return "online"
    if _matches_any(q, _MODE_INPERSON):
        return "in_person"
    return None

def run(db: Session, question: str, limit: int = 8) -> Dict[str, Any]:
    limit = _parse_limit(question, limit)
    start_dt, end_dt, label = _range_for_question(question)
    mode = _mode_filter(question)

    ck = make_key("events", label, start_dt.isoformat(), end_dt.isoformat(), mode or "any", limit)
    cached = cache().get(ck)
    if cached is not None:
        return cached

    qry = (
        db.query(
            Event.title,
            Event.description,  
            Event.start_time,
            Event.end_time,
            Event.location,
            Event.mode,
            Society.name,
        )
        .outerjoin(Society, Society.id == Event.organiser_society_id)
        .filter(Event.start_time >= start_dt)
        .filter(Event.start_time < end_dt)
        .order_by(Event.start_time.asc())
    )

    if mode:
        # assumes Event.mode holds strings like "online"/"in_person"/"hybrid"
        qry = qry.filter(Event.mode.ilike(f"%{mode}%"))

    rows = qry.limit(limit).all()

    events: List[Dict[str, Any]] = []
    for title, desc, start_t, end_t, loc, mode_val, organiser in rows:
        events.append(
            {
                "title": title,
                "description": (desc[:220] + "…") if desc and len(desc) > 220 else desc,
                "start_time": _to_local_iso(start_t),
                "end_time": _to_local_iso(end_t),
                "location": loc,
                "mode": mode_val,
                "organiser": organiser,
            }
        )

    out = {
        "range_label": label,
        "mode_filter": mode,
        "count": len(events),
        "events": events, 
        "summary": f"Found {len(events)} campus event(s) for {label}.",
    }
    cache().set(ck, out, ttl_seconds=30)
    return out
