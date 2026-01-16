# src/llm/tools/timetable_tool.py
from __future__ import annotations

import os
import re
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from src.module.models import TimetableEntry, Module

TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/London"))

_DAY_TO_INT = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

def _start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday

def _parse_day_name(q: str) -> Optional[int]:
    q = (q or "").lower()
    for name, idx in _DAY_TO_INT.items():
        if name in q:
            return idx
    return None

def _range_for_question(question: str) -> tuple[datetime, datetime, str]:
    q = (question or "").lower()
    today = datetime.now(TZ).date()

    day_idx = _parse_day_name(q)

    # next week
    if "next week" in q:
        start_next_week = _start_of_week(today) + timedelta(days=7)
        if day_idx is None:
            # whole next week range
            start = datetime.combine(start_next_week, time.min, tzinfo=TZ)
            end = datetime.combine(start_next_week + timedelta(days=7), time.min, tzinfo=TZ)
            return start, end, f"next week ({start_next_week.isoformat()} to {(start_next_week+timedelta(days=6)).isoformat()})"
        target_date = start_next_week + timedelta(days=day_idx)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat()

    # tomorrow / today
    if "tomorrow" in q:
        target_date = today + timedelta(days=1)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat()

    if "today" in q:
        start = datetime.combine(today, time.min, tzinfo=TZ)
        end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, today.isoformat()

    # just a day name (e.g. "monday") -> next occurrence
    if day_idx is not None:
        start_this_week = _start_of_week(today)
        target_date = start_this_week + timedelta(days=day_idx)
        if target_date <= today:
            target_date += timedelta(days=7)
        start = datetime.combine(target_date, time.min, tzinfo=TZ)
        end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=TZ)
        return start, end, target_date.isoformat()

    # fallback: default to next 7 days
    start = datetime.combine(today, time.min, tzinfo=TZ)
    end = datetime.combine(today + timedelta(days=7), time.min, tzinfo=TZ)
    return start, end, "next 7 days"

def run(db: Session, question: str) -> Dict[str, Any]:
    start_dt, end_dt, label = _range_for_question(question)

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
    for room, start_time, end_time, session_type, day_of_week, module_code, module_name, module_id in rows:
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

    return {
        "range_label": label,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "sessions": sessions,
    }
