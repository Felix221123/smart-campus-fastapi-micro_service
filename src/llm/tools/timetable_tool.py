# src/llm/tools/timetable_tool.py
from __future__ import annotations

import os
import re
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional, Tuple, List

from sqlalchemy.orm import Session
from src.module.models import TimetableEntry, Module

TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/London"))

_DAY_TO_INT = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Common abbreviations users type
_DAY_ABBR = {
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


_THIS_WEEK_PATTERNS = [
    r"\bthis week\b",
    r"\bfor this week\b",
    r"\bfor the week\b",
    r"\bthis week's\b",
    r"\bcurrent week\b",
    r"\bmy week\b",
]

_NEXT_WEEK_PATTERNS = [
    r"\bnext[-\s]?week\b",
    r"\bfor\s+next[-\s]?week\b",
    r"\bthe\s+week\s+after\b",
    r"\bweek\s+after\s+next\b",
    r"\bfollowing\s+week\b",
]

_LAST_WEEK_PATTERNS = [
    r"\blast[-\s]?week\b",
    r"\bfor\s+last[-\s]?week\b",
    r"\bprevious\s+week\b",
]

_LAST_MONTH_PATTERNS = [
    r"\blast\s+month\b",
    r"\bprevious\s+month\b",
]


_NEXT_7_DAYS_PATTERNS = [
    r"\bnext 7 days\b",
    r"\bover the next week\b",
    r"\bin the next week\b",
    r"\bweek ahead\b",
    r"\bcoming 7 days\b",
]

_NEXT_DAY_PATTERNS = [
    r"\bnext\s+day\b",
    r"\bthe\s+next\s+day\b",
]

_WEEKEND_PATTERNS = [
    r"\bthis weekend\b",
    r"\bweekend\b",
    r"\bsat(urday)?\s*(and|&)\s*sun(day)?\b",
]


_FREE_DAY_PATTERNS = [
    r"\bdays?\s+off\b",
    r"\bday\s+off\b",
    r"\bfree\s+days?\b",
    r"\bwhen\s+am\s+i\s+free\b",
    r"\bwhat\s+days?\s+am\s+i\s+free\b",
    r"\bno\s+(classes|lectures|sessions)\b",
    r"\bnothing\s+on\b",
    r"\bdo\s+i\s+have\s+anything\s+on\b",
    r"\bno\s+lectures\b",
]

_DAYS_AGO_RE = re.compile(r"\b(\d{1,2})\s+days?\s+ago\b", re.I)
_NEXT_N_DAYS_RE = re.compile(r"\bnext\s+(\d{1,2})\s+days?\b", re.I)
_NEXT_DAY_NAME_RE = re.compile(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", re.I)

def _start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday

def _parse_day_name(q: str) -> Optional[int]:
    q = (q or "").lower()

    # full day names
    for name, idx in _DAY_TO_INT.items():
        if re.search(rf"\b{name}\b", q):
            return idx

    # abbreviations
    for abbr, idx in _DAY_ABBR.items():
        if re.search(rf"\b{abbr}\b", q):
            return idx

    return None


def _matches_any(q: str, patterns: List[str]) -> bool:
    return any(re.search(p, q) for p in patterns)


def _wants_free_days(q: str) -> bool:
    return _matches_any(q, _FREE_DAY_PATTERNS)


def _day_index_from_token(token: str) -> Optional[int]:
    t = (token or "").lower()
    if t in _DAY_TO_INT:
        return _DAY_TO_INT[t]
    if t in _DAY_ABBR:
        return _DAY_ABBR[t]
    return None



def _range_for_question(question: str) -> Tuple[datetime, datetime, str, str]:
    """
    Returns (start_dt, end_dt, range_label, range_kind)
    range_kind is one of: "day", "week", "next_7_days", "weekend"
    """
    q = (question or "").lower()
    today = datetime.now(TZ).date()
    day_idx = _parse_day_name(q)

    # explicit "next friday" style phrasing
    m_next_day_name = _NEXT_DAY_NAME_RE.search(q)
    if m_next_day_name:
        target_idx = _day_index_from_token(m_next_day_name.group(1))
        if target_idx is not None:
            days_ahead = (target_idx - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = today + timedelta(days=days_ahead)
            start = datetime.combine(target_date, time.min, tzinfo=TZ)
            end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
            return start, end, target_date.isoformat(), "day"

    # relative past day e.g. "3 days ago"
    m_days_ago = _DAYS_AGO_RE.search(q)
    if m_days_ago:
        days_back = max(1, min(int(m_days_ago.group(1)), 60))
        target_date = today - timedelta(days=days_back)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    # relative future window e.g. "next 2 days"
    m_next_days = _NEXT_N_DAYS_RE.search(q)
    if m_next_days:
        days = max(1, min(int(m_next_days.group(1)), 60))
        start = datetime.combine(today, time.min, tzinfo=TZ)
        end = datetime.combine(today + timedelta(days=days), time.min, tzinfo=TZ)
        return start, end, f"next {days} days", "next_n_days"

    # previous month window
    if _matches_any(q, _LAST_MONTH_PATTERNS):
        first_of_this_month = today.replace(day=1)
        last_day_prev_month = first_of_this_month - timedelta(days=1)
        first_of_prev_month = last_day_prev_month.replace(day=1)
        start = datetime.combine(first_of_prev_month, time.min, tzinfo=TZ)
        end = datetime.combine(first_of_this_month, time.min, tzinfo=TZ)
        label = f"last month ({first_of_prev_month.isoformat()} to {last_day_prev_month.isoformat()})"
        return start, end, label, "month"

    # LAST WEEK
    if _matches_any(q, _LAST_WEEK_PATTERNS):
        start_week = _start_of_week(today) - timedelta(days=7)
        if day_idx is None:
            start = datetime.combine(start_week, time.min, tzinfo=TZ)
            end = datetime.combine(start_week + timedelta(days=7), time.min, tzinfo=TZ)
            label = f"last week ({start_week.isoformat()} to {(start_week + timedelta(days=6)).isoformat()})"
            return start, end, label, "week"

        target_date = start_week + timedelta(days=day_idx)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    # NEXT WEEK
    if _matches_any(q, _NEXT_WEEK_PATTERNS):
        start_week = _start_of_week(today) + timedelta(days=7)
        if day_idx is None:
            start = datetime.combine(start_week, time.min, tzinfo=TZ)
            end = datetime.combine(start_week + timedelta(days=7), time.min, tzinfo=TZ)
            label = f"next week ({start_week.isoformat()} to {(start_week + timedelta(days=6)).isoformat()})"
            return start, end, label, "week"

        target_date = start_week + timedelta(days=day_idx)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    # THIS WEEK (calendar week Mon-Sun)
    if _matches_any(q, _THIS_WEEK_PATTERNS):
        start_week = _start_of_week(today)
        if day_idx is None:
            start = datetime.combine(start_week, time.min, tzinfo=TZ)
            end = datetime.combine(start_week + timedelta(days=7), time.min, tzinfo=TZ)
            label = f"this week ({start_week.isoformat()} to {(start_week + timedelta(days=6)).isoformat()})"
            return start, end, label, "week"

        # "monday this week" -> monday of current week (even if in the past)
        target_date = start_week + timedelta(days=day_idx)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    # WEEKEND (upcoming weekend in this calendar week)
    if _matches_any(q, _WEEKEND_PATTERNS):
        start_week = _start_of_week(today)
        saturday = start_week + timedelta(days=5)
        monday_after = start_week + timedelta(days=7)
        start = datetime.combine(saturday, time.min, tzinfo=TZ)
        end = datetime.combine(monday_after, time.min, tzinfo=TZ)
        label = f"this weekend ({saturday.isoformat()} to {(monday_after - timedelta(days=1)).isoformat()})"
        return start, end, label, "weekend"

    # TOMORROW / TODAY
    if "tomorrow" in q:
        target_date = today + timedelta(days=1)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    if "yesterday" in q:
        target_date = today - timedelta(days=1)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    if _matches_any(q, _NEXT_DAY_PATTERNS):
        target_date = today + timedelta(days=1)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    if "today" in q:
        start = datetime.combine(today, time.min, tzinfo=TZ)
        end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, today.isoformat(), "day"

    # Explicit "next 7 days" phrasing
    if _matches_any(q, _NEXT_7_DAYS_PATTERNS):
        start = datetime.combine(today, time.min, tzinfo=TZ)
        end = datetime.combine(today + timedelta(days=7), time.min, tzinfo=TZ)
        return start, end, "next 7 days", "next_7_days"

    # Just a day name (default: next occurrence)
    if day_idx is not None:
        start_this_week = _start_of_week(today)
        target_date = start_this_week + timedelta(days=day_idx)
        if target_date < today:
            target_date += timedelta(days=7)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat(), "day"

    # fallback: next 7 days
    start = datetime.combine(today, time.min, tzinfo=TZ)
    end = datetime.combine(today + timedelta(days=7), time.min, tzinfo=TZ)
    return start, end, "next 7 days", "next_7_days"

def run(db: Session, question: str) -> Dict[str, Any]:
    start_dt, end_dt, label, kind = _range_for_question(question)
    wants_free = _wants_free_days((question or "").lower())

    rows = (
        db.query(
            TimetableEntry.room,
            TimetableEntry.start_time,
            TimetableEntry.end_time,
            TimetableEntry.session_type,
            TimetableEntry.day_of_week,
            Module.code,
            Module.name,
            Module.id,
        )
        .join(Module, TimetableEntry.module_id == Module.id)
        .filter(TimetableEntry.start_time >= start_dt)
        .filter(TimetableEntry.start_time < end_dt)
        .order_by(TimetableEntry.start_time.asc())
        .all()
    )

    sessions = []
    occupied_dates = set()

    for room, start_time, end_time, session_type, day_of_week, module_code, module_name, module_id in rows:
        # Ensure date is computed in the app TZ
        st_local = start_time.astimezone(TZ) if start_time.tzinfo else start_time.replace(tzinfo=TZ)
        occupied_dates.add(st_local.date().isoformat())

        sessions.append({
            "module_id": str(module_id),
            "module_code": module_code,
            "module_name": module_name,
            "room": room,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "session_type": session_type,
            "day_of_week": day_of_week,
        })

    payload: Dict[str, Any] = {
        "range_label": label,
        "range_kind": kind,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "sessions": sessions,
        "session_count": len(sessions),
        "summary": f"Found {len(sessions)} session(s) for {label}.",
    }

    # Extra: "days off / free days" support
    if wants_free:
        start_d = start_dt.astimezone(TZ).date()
        end_d_exclusive = end_dt.astimezone(TZ).date()

        # If the query is a single day range, just say if it's free
        if (end_dt - start_dt) <= timedelta(days=1, seconds=1):
            payload["is_free_day"] = (len(sessions) == 0)
        else:
            free_days = []
            d = start_d
            while d < end_d_exclusive:
                iso = d.isoformat()
                if iso not in occupied_dates:
                    free_days.append({
                        "date": iso,
                        "day_name": d.strftime("%A"),
                    })
                d += timedelta(days=1)

            payload["free_days"] = free_days
            payload["free_days_count"] = len(free_days)

    return payload
