# src/llm/tools/events_tool.py
from __future__ import annotations

import os
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.llm.cache import cache, make_key
from src.module.models import Event, Society

TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/London"))

_THIS_WEEK_PATTERNS = [r"\bthis\s+week\b", r"\bfor\s+this\s+week\b"]
_NEXT_WEEK_PATTERNS = [r"\bnext[-\s]?week\b", r"\bfor\s+next[-\s]?week\b", r"\bfollowing\s+week\b"]
_LAST_WEEK_PATTERNS = [r"\blast[-\s]?week\b", r"\bfor\s+last[-\s]?week\b", r"\bprevious\s+week\b"]
_TODAY_PATTERNS = [r"\btoday\b"]
_TOMORROW_PATTERNS = [r"\btomorrow\b"]
_YESTERDAY_PATTERNS = [r"\byesterday\b"]
_NEXT_DAY_PATTERNS = [r"\bnext\s+day\b", r"\bthe\s+next\s+day\b"]
_WEEKEND_PATTERNS = [r"\bthis\s+weekend\b", r"\bweekend\b"]
_LAST_MONTH_PATTERNS = [r"\blast\s+month\b", r"\bprevious\s+month\b"]

_MODE_ONLINE = [r"\bonline\b", r"\bvirtual\b", r"\bzoom\b", r"\bteams\b"]
_MODE_INPERSON = [r"\bin[-\s]?person\b", r"\bon\s*campus\b", r"\bface[-\s]?to[-\s]?face\b"]
_MODE_HYBRID = [r"\bhybrid\b"]

_LIMIT_RE = re.compile(r"\b(?:show|list|get)\s+(\d{1,2})\b", re.I)
_DAYS_AGO_RE = re.compile(r"\b(\d{1,2})\s+days?\s+ago\b", re.I)
_NEXT_N_DAYS_RE = re.compile(r"\bnext\s+(\d{1,2})\s+days?\b", re.I)
_LAST_WEEKDAY_RE = re.compile(r"\blast\s+week\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", re.I)
_NEXT_WEEKDAY_RE = re.compile(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", re.I)

_DAY_TO_INT = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_DAY_ABBR = {
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def _matches_any(q: str, patterns: List[str]) -> bool:
    return any(re.search(p, q) for p in patterns)

def _parse_limit(question: str, default: int) -> int:
    if _NEXT_N_DAYS_RE.search(question or ""):
        return default

    m = _LIMIT_RE.search(question or "")
    if not m:
        return default
    n = int(m.group(1))
    return max(1, min(n, 50))

def _start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday

def _day_index_from_token(token: str) -> Optional[int]:
    t = (token or "").lower()
    if t in _DAY_TO_INT:
        return _DAY_TO_INT[t]
    if t in _DAY_ABBR:
        return _DAY_ABBR[t]
    return None

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

    m_last_weekday = _LAST_WEEKDAY_RE.search(q)
    if m_last_weekday:
        idx = _day_index_from_token(m_last_weekday.group(1))
        if idx is not None:
            start_week = _start_of_week(today) - timedelta(days=7)
            target = start_week + timedelta(days=idx)
            start_local = datetime.combine(target, time.min, tzinfo=TZ)
            end_local = start_local + timedelta(days=1)
            return _to_utc_naive(start_local), _to_utc_naive(end_local), f"last week {target.strftime('%A')} ({target.isoformat()})"

    m_next_weekday = _NEXT_WEEKDAY_RE.search(q)
    if m_next_weekday:
        idx = _day_index_from_token(m_next_weekday.group(1))
        if idx is not None:
            days_ahead = (idx - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = today + timedelta(days=days_ahead)
            start_local = datetime.combine(target, time.min, tzinfo=TZ)
            end_local = start_local + timedelta(days=1)
            return _to_utc_naive(start_local), _to_utc_naive(end_local), f"next {target.strftime('%A')} ({target.isoformat()})"

    m_days_ago = _DAYS_AGO_RE.search(q)
    if m_days_ago:
        days_back = max(1, min(int(m_days_ago.group(1)), 60))
        target = today - timedelta(days=days_back)
        start_local = datetime.combine(target, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), f"{days_back} days ago ({target.isoformat()})"

    m_next_days = _NEXT_N_DAYS_RE.search(q)
    if m_next_days:
        days = max(1, min(int(m_next_days.group(1)), 60))
        start_local = now_local
        end_local = now_local + timedelta(days=days)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), f"next {days} days"

    if _matches_any(q, _LAST_MONTH_PATTERNS):
        first_this_month = today.replace(day=1)
        last_prev_month = first_this_month - timedelta(days=1)
        first_prev_month = last_prev_month.replace(day=1)
        start_local = datetime.combine(first_prev_month, time.min, tzinfo=TZ)
        end_local = datetime.combine(first_this_month, time.min, tzinfo=TZ)
        label = f"last month ({first_prev_month.isoformat()} to {last_prev_month.isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label

    if _matches_any(q, _LAST_WEEK_PATTERNS):
        start_d = _start_of_week(today) - timedelta(days=7)
        start_local = datetime.combine(start_d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=7)
        label = f"last week ({start_d.isoformat()} to {(start_d + timedelta(days=6)).isoformat()})"
        return _to_utc_naive(start_local), _to_utc_naive(end_local), label

    if _matches_any(q, _TODAY_PATTERNS):
        start_local = datetime.combine(today, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), f"today ({today.isoformat()})"

    if _matches_any(q, _TOMORROW_PATTERNS):
        d = today + timedelta(days=1)
        start_local = datetime.combine(d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), f"tomorrow ({d.isoformat()})"

    if _matches_any(q, _YESTERDAY_PATTERNS):
        d = today - timedelta(days=1)
        start_local = datetime.combine(d, time.min, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), f"yesterday ({d.isoformat()})"

    if _matches_any(q, _NEXT_DAY_PATTERNS):
        start_local = now_local
        end_local = now_local + timedelta(days=1)
        return _to_utc_naive(start_local), _to_utc_naive(end_local), "next day"

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
