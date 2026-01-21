# src/llm/tools/space_booking_tool.py
from __future__ import annotations

import os
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.module.models import Space, SpaceBooking, BookingStatus  


TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/London"))


# opening times and slots availability
SLOT_LEN = timedelta(hours=1)

WEEKDAY_OPEN = time(9, 0)
WEEKDAY_CLOSE = time(22, 0)  
WEEKEND_OPEN = time(9, 0)
WEEKEND_CLOSE = time(17, 0)  


_DAY_TO_INT = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_DAY_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}

_TIME_HHMM_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_TIME_AMPM_RE = re.compile(r"\b(1[0-2]|0?\d)\s*(am|pm)\b", re.I)

_DURATION_RE = re.compile(r"\bfor\s+(\d{1,2})\s*(hour|hours|hr|hrs|minute|minutes|min|mins)\b", re.I)

_TIME_TOKEN_RE = re.compile(
    r"(\b([01]?\d|2[0-3]):([0-5]\d)\b)|(\b(1[0-2]|0?\d)\s*(am|pm)\b)|(\bnoon\b)",
    re.I,
)

_LOCATION_HINTS = [
    ("library", ["library", "boots", "nls"]),
    ("clifton", ["clifton"]),
    ("city", ["city campus"]),
    ("brackenhurst", ["brackenhurst"]),
]

_RIGHT_NOW_RE = re.compile(r"\b(right\s*now|now|asap)\b", re.I)


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _open_close(d: date) -> tuple[time, time]:
    if _is_weekend(d):
        return WEEKEND_OPEN, WEEKEND_CLOSE
    return WEEKDAY_OPEN, WEEKDAY_CLOSE


def _opening_hours_text(d: date) -> str:
    o, c = _open_close(d)
    return f"{o.strftime('%-I%p').lower()}–{c.strftime('%-I%p').lower()}"


def _ceil_to_next_hour(dt: datetime) -> datetime:
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _last_slot_start_local(d: date) -> datetime:
    _, close_t = _open_close(d)
    close_dt = datetime.combine(d, close_t, tzinfo=TZ)
    return close_dt - SLOT_LEN


def _ensure_valid_slot_start(start_local: datetime) -> datetime:
    """
    Ensure start_local is:
    - aligned to hour boundary
    - within opening hours (or moved to next day opening)
    """
    start_local = _ceil_to_next_hour(start_local)

    d = start_local.date()
    open_t, _ = _open_close(d)
    open_dt = datetime.combine(d, open_t, tzinfo=TZ)
    last_start = _last_slot_start_local(d)

    if start_local < open_dt:
        start_local = open_dt

    if start_local > last_start:
        # jump to next day opening
        d2 = d + timedelta(days=1)
        open_t2, _ = _open_close(d2)
        start_local = datetime.combine(d2, open_t2, tzinfo=TZ)

    return start_local


def _advance_slot(start_local: datetime) -> datetime:
    """Next 1h slot respecting daily closing."""
    d = start_local.date()
    nxt = start_local + SLOT_LEN
    if nxt > _last_slot_start_local(d):
        d2 = d + timedelta(days=1)
        open_t2, _ = _open_close(d2)
        return datetime.combine(d2, open_t2, tzinfo=TZ)
    return nxt


def _to_utc_naive(dt_local: datetime) -> datetime:
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_day_offset(question: str, today: date) -> Optional[date]:
    q = (question or "").lower()

    if "today" in q:
        return today
    if "tomorrow" in q:
        return today + timedelta(days=1)

    if re.search(r"\bnext[-\s]?week\b", q):
        start_next_week = (today - timedelta(days=today.weekday())) + timedelta(days=7)
        for name, idx in _DAY_TO_INT.items():
            if re.search(rf"\b{name}\b", q):
                return start_next_week + timedelta(days=idx)
        for abbr, idx in _DAY_ABBR.items():
            if re.search(rf"\b{abbr}\b", q):
                return start_next_week + timedelta(days=idx)
        return start_next_week  # default Monday

    # next occurrence of a named day
    for name, idx in _DAY_TO_INT.items():
        if re.search(rf"\b{name}\b", q):
            d0 = today - timedelta(days=today.weekday()) + timedelta(days=idx)
            if d0 < today:
                d0 += timedelta(days=7)
            return d0

    for abbr, idx in _DAY_ABBR.items():
        if re.search(rf"\b{abbr}\b", q):
            d0 = today - timedelta(days=today.weekday()) + timedelta(days=idx)
            if d0 < today:
                d0 += timedelta(days=7)
            return d0

    return None



def _parse_time_of_day(question: str) -> Optional[time]:
    q = (question or "").lower()

    m = _TIME_HHMM_RE.search(q)
    if m:
        return time(int(m.group(1)), int(m.group(2)))

    m = _TIME_AMPM_RE.search(q)
    if m:
        hh = int(m.group(1))
        ampm = m.group(2).lower()
        if ampm == "pm" and hh != 12:
            hh += 12
        if ampm == "am" and hh == 12:
            hh = 0
        return time(hh, 0)

    if "noon" in q:
        return time(12, 0)

    return None



def _parse_duration(question: str) -> timedelta:
    m = _DURATION_RE.search(question or "")
    if not m:
        return timedelta(hours=1)

    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("hour") or unit in ("hr", "hrs"):
        return timedelta(hours=n)
    return timedelta(minutes=n)


def _extract_location_hint(question: str) -> Optional[str]:
    q = (question or "").lower()
    for canonical, words in _LOCATION_HINTS:
        if any(w in q for w in words):
            return canonical
    return None



def _default_window(question: str) -> tuple[datetime, datetime]:
    # fallback: next hour
    start = datetime.utcnow() + timedelta(minutes=10)
    end = start + timedelta(hours=1)
    return start, end


# checking if space has overlap bookings
def _space_has_overlap(
    db: Session,
    space_id: str,
    start_utc: datetime,  # naive UTC
    end_utc: datetime,    # naive UTC
) -> bool:
    hit = (
        db.query(SpaceBooking.id)
        .filter(
            SpaceBooking.space_id == space_id,
            SpaceBooking.status != BookingStatus.CANCELLED,
            SpaceBooking.start_time < end_utc,
            SpaceBooking.end_time > start_utc,
        )
        .first()
    )
    return hit is not None


def _find_available_spaces_for_slot(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
    location_hint: Optional[str],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    # exclude spaces with any overlapping booking in this slot
    subq = (
        db.query(SpaceBooking.space_id)
        .filter(
            SpaceBooking.status != BookingStatus.CANCELLED,
            SpaceBooking.start_time < end_utc,
            SpaceBooking.end_time > start_utc,
        )
        .subquery()
    )

    qry = db.query(Space).filter(~Space.id.in_(subq))
    if location_hint:
        qry = qry.filter(Space.location.ilike(f"%{location_hint}%"))

    spaces = qry.limit(limit).all()

    return [
        {
            "space_id": str(s.id),
            "name": s.name,
            "type": s.type,
            "location": s.location,
            "capacity": s.capacity,
        }
        for s in spaces
    ]


def _next_slot_with_any_space(
    db: Session,
    start_local: datetime,
    location_hint: Optional[str],
    max_checks: int = 12,
) -> Optional[Tuple[datetime, List[Dict[str, Any]]]]:
    """
    Finds the next slot (local) that has at least 1 available space.
    Returns (slot_start_local, spaces)
    """
    cur = start_local
    for _ in range(max_checks):
        cur = _ensure_valid_slot_start(cur)
        s_utc = _to_utc_naive(cur)
        e_utc = _to_utc_naive(cur + SLOT_LEN)
        opts = _find_available_spaces_for_slot(db, s_utc, e_utc, location_hint, limit=5)
        if opts:
            return cur, opts
        cur = _advance_slot(cur)
    return None


def _create_booking(
    db: Session,
    user_id: str,
    space_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> Dict[str, Any]:
    booking = SpaceBooking(
        space_id=space_id,
        user_id=user_id,
        start_time=start_utc,
        end_time=end_utc,
        status=BookingStatus.CONFIRMED,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return {"booking_id": str(booking.id), "status": str(booking.status)}


# def _parse_booking_window(question: str) -> tuple[datetime, datetime]:
#     """
#     Returns naive UTC start/end suitable for DB filters.
#     Understands: today/tomorrow/next week/day name + time + duration.
#     """
#     q = question or ""
#     now_local = datetime.now(TZ)
#     today = now_local.date()

#     target_date = _parse_day_offset(q, today)
#     tod = _parse_time_of_day(q)
#     dur = _parse_duration(q)

#     # If user provided a date/day/time
#     if target_date and tod:
#         start_local = datetime.combine(target_date, tod, tzinfo=TZ)
#         end_local = start_local + dur
#         return _to_utc_naive(start_local), _to_utc_naive(end_local)

#     # If they only said "tomorrow" etc, assume 10 minutes from now for that day
#     if target_date and not tod:
#         start_local = datetime.combine(target_date, time(9, 0), tzinfo=TZ)
#         end_local = start_local + dur
#         return _to_utc_naive(start_local), _to_utc_naive(end_local)

#     # If they only gave a time (e.g., "at 3pm"), assume today if future else tomorrow
#     if tod and not target_date:
#         start_local = datetime.combine(today, tod, tzinfo=TZ)
#         if start_local < now_local:
#             start_local = start_local + timedelta(days=1)
#         end_local = start_local + dur
#         return _to_utc_naive(start_local), _to_utc_naive(end_local)

#     # fallback
#     return _default_window(q)

def find_available_spaces(
    db: Session,
    start_time: datetime,
    end_time: datetime,
    limit: int = 5,
    location_hint: Optional[str] = None,
) -> List[Dict[str, Any]]:
    subq = (
        db.query(SpaceBooking.space_id)
        .filter(
            SpaceBooking.status != BookingStatus.CANCELLED,
            SpaceBooking.start_time < end_time,
            SpaceBooking.end_time > start_time,
        )
        .subquery()
    )

    qry = db.query(Space).filter(~Space.id.in_(subq))

    if location_hint:
        qry = qry.filter(Space.location.ilike(f"%{location_hint}%"))

    spaces = qry.limit(limit).all()

    out: List[Dict[str, Any]] = []
    for s in spaces:
        out.append(
            {
                "space_id": str(s.id),
                "name": s.name,
                "type": s.type,
                "location": s.location,
                "capacity": s.capacity,
            }
        )
    return out


def _create_booking(
    db: Session,
    user_id: str,
    space_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> Dict[str, Any]:
    booking = SpaceBooking(
        space_id=space_id,
        user_id=user_id,
        start_time=start_utc,
        end_time=end_utc,
        status=BookingStatus.CONFIRMED,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return {"booking_id": str(booking.id), "status": str(booking.status)}


def run_find(db: Session, question: str) -> Dict[str, Any]:
    """
    Step 1:
    If user DID NOT provide a time, ask for it (requires_time=True).
    If user says "now/right now", auto-pick next valid hourly slot within opening hours.
    If user provides a time, return options for that slot (or suggest next slot if full).
    """
    q = (question or "").lower()
    now_local = datetime.now(TZ)
    today = now_local.date()

    location_hint = _extract_location_hint(question)
    target_date = _parse_day_offset(question, today) or today
    tod = _parse_time_of_day(question)

    # ✅ If no time provided, ask (unless "right now/now/asap")
    if tod is None and not _RIGHT_NOW_RE.search(q):
        hours = _opening_hours_text(target_date)
        return {
            "requires_time": True,
            "target_date": target_date.isoformat(),
            "location_hint": location_hint,
            "message": (
                f"What time would you like to book for? "
                f"Try “3pm” or “15:00”. Opening hours on that day are {hours}. "
                f"Bookings are 1-hour slots."
            ),
        }

    # ✅ If "right now", choose next valid slot
    if tod is None and _RIGHT_NOW_RE.search(q):
        start_local = _ensure_valid_slot_start(now_local)
        nxt = _next_slot_with_any_space(db, start_local, location_hint, max_checks=12)
        if not nxt:
            return {"error": "no_availability", "message": "I couldn’t find any availability soon. Try another time."}

        slot_start_local, options = nxt
        slot_end_local = slot_start_local + SLOT_LEN

        label = f"{slot_start_local.strftime('%a %d %b %H:%M')}–{slot_end_local.strftime('%H:%M')}"
        return {
            "range_label": label,
            "start_time": _to_utc_naive(slot_start_local).isoformat(),
            "end_time": _to_utc_naive(slot_end_local).isoformat(),
            "location_hint": location_hint,
            "options": options,
            "requires_user_choice": True,
            "instruction": "Choose an option number to confirm the booking (e.g. “book option 2”).",
            "summary": f"Here are spaces available {label}.",
            "slot_minutes": 60,
        }

    # ✅ Time provided: build slot → options; if none, suggest next slot yes/no
    start_local = datetime.combine(target_date, tod, tzinfo=TZ)
    start_local = _ensure_valid_slot_start(start_local)
    end_local = start_local + SLOT_LEN

    s_utc = _to_utc_naive(start_local)
    e_utc = _to_utc_naive(end_local)

    options = _find_available_spaces_for_slot(db, s_utc, e_utc, location_hint, limit=5)

    if not options:
        nxt = _next_slot_with_any_space(db, _advance_slot(start_local), location_hint, max_checks=12)
        if nxt:
            nxt_start_local, nxt_opts = nxt
            nxt_end_local = nxt_start_local + SLOT_LEN
            return {
                "error": "slot_unavailable",
                "message": (
                    f"That slot ({start_local.strftime('%a %H:%M')}–{end_local.strftime('%H:%M')}) is fully booked. "
                    f"The next available slot is {nxt_start_local.strftime('%a %H:%M')}–{nxt_end_local.strftime('%H:%M')}. "
                    f"Do you want to book that instead? Say “yes” or “no”."
                ),
                "requires_alt_slot_confirm": True,
                "alt_start_time": _to_utc_naive(nxt_start_local).isoformat(),
                "alt_end_time": _to_utc_naive(nxt_end_local).isoformat(),
                "alt_options": nxt_opts,
                "location_hint": location_hint,
            }

        return {"error": "slot_unavailable", "message": "That time is fully booked. Try another time."}

    label = f"{start_local.strftime('%a %d %b %H:%M')}–{end_local.strftime('%H:%M')}"
    return {
        "range_label": label,
        "start_time": s_utc.isoformat(),
        "end_time": e_utc.isoformat(),
        "location_hint": location_hint,
        "options": options,
        "requires_user_choice": True,
        "instruction": "Choose an option number to confirm the booking (e.g. “book option 2”).",
        "summary": f"Found {len(options)} available space(s) for {label}.",
        "slot_minutes": 60,
    }






def run_set_time(db: Session, user_time_message: str, pending: Dict[str, Any]) -> Dict[str, Any]:
    """
    Step 2:
    User replies with a time after we asked for it.
    We reuse stored target_date/location_hint from pending, unless user explicitly says a new day.
    """
    now_local = datetime.now(TZ)
    today = now_local.date()

    # prefer explicit date in the follow-up message, else pending target date
    pending_date = pending.get("target_date")
    base_date = date.fromisoformat(pending_date) if pending_date else today

    target_date = _parse_day_offset(user_time_message, today) or base_date
    tod = _parse_time_of_day(user_time_message)

    if tod is None:
        hours = _opening_hours_text(target_date)
        return {
            "requires_time": True,
            "target_date": target_date.isoformat(),
            "location_hint": pending.get("location_hint"),
            "message": f"Please give a time like “3pm” or “15:00”. Opening hours are {hours}.",
        }

    start_local = datetime.combine(target_date, tod, tzinfo=TZ)
    start_local = _ensure_valid_slot_start(start_local)
    end_local = start_local + SLOT_LEN

    s_utc = _to_utc_naive(start_local)
    e_utc = _to_utc_naive(end_local)

    location_hint = pending.get("location_hint")
    options = _find_available_spaces_for_slot(db, s_utc, e_utc, location_hint, limit=5)

    if not options:
        nxt = _next_slot_with_any_space(db, _advance_slot(start_local), location_hint, max_checks=12)
        if nxt:
            nxt_start_local, nxt_opts = nxt
            nxt_end_local = nxt_start_local + SLOT_LEN
            return {
                "error": "slot_unavailable",
                "message": (
                    f"That slot ({start_local.strftime('%a %H:%M')}–{end_local.strftime('%H:%M')}) is fully booked. "
                    f"The next available slot is {nxt_start_local.strftime('%a %H:%M')}–{nxt_end_local.strftime('%H:%M')}. "
                    f"Do you want to book that instead? Say “yes” or “no”."
                ),
                "requires_alt_slot_confirm": True,
                "alt_start_time": _to_utc_naive(nxt_start_local).isoformat(),
                "alt_end_time": _to_utc_naive(nxt_end_local).isoformat(),
                "alt_options": nxt_opts,
                "location_hint": location_hint,
            }

        return {"error": "slot_unavailable", "message": "That time is fully booked. Try another time."}

    label = f"{start_local.strftime('%a %d %b %H:%M')}–{end_local.strftime('%H:%M')}"
    return {
        "range_label": label,
        "start_time": s_utc.isoformat(),
        "end_time": e_utc.isoformat(),
        "location_hint": location_hint,
        "options": options,
        "requires_user_choice": True,
        "instruction": "Choose an option number to confirm the booking (e.g. “book option 2”).",
        "summary": f"Found {len(options)} available space(s) for {label}.",
        "slot_minutes": 60,
    }


def run_confirm(
    db: Session,
    user_id: str,
    selection_index: int,
    pending: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Step 3:
    User chooses a space option -> attempt booking.
    If overlap happens (race condition), suggest next slot and ask to confirm it.
    """
    options = pending.get("options") or []
    if not options or selection_index < 1 or selection_index > len(options):
        return {"error": "invalid_selection", "message": "Please choose a valid option number."}

    chosen = options[selection_index - 1]
    space_id = chosen["space_id"]

    start_utc = datetime.fromisoformat(pending["start_time"])
    end_utc = datetime.fromisoformat(pending["end_time"])

    # Re-check overlap at confirm time (race-safe-ish)
    if _space_has_overlap(db, space_id, start_utc, end_utc):
        # suggest next slot for THIS space (not just any space)
        start_local = start_utc.replace(tzinfo=timezone.utc).astimezone(TZ).replace(tzinfo=TZ)
        alt_local = _advance_slot(start_local)

        # search next few slots for same space_id
        for _ in range(12):
            alt_local = _ensure_valid_slot_start(alt_local)
            alt_s_utc = _to_utc_naive(alt_local)
            alt_e_utc = _to_utc_naive(alt_local + SLOT_LEN)

            if not _space_has_overlap(db, space_id, alt_s_utc, alt_e_utc):
                return {
                    "error": "slot_taken",
                    "message": (
                        f"That slot was just booked by someone else. "
                        f"The next available slot for {chosen['name']} is "
                        f"{alt_local.strftime('%a %H:%M')}–{(alt_local + SLOT_LEN).strftime('%H:%M')}. "
                        f"Do you want to book that instead? Say “yes” or “no”."
                    ),
                    "requires_alt_slot_confirm": True,
                    "alt_space": chosen,
                    "alt_start_time": alt_s_utc.isoformat(),
                    "alt_end_time": alt_e_utc.isoformat(),
                }

            alt_local = _advance_slot(alt_local)

        return {"error": "slot_taken", "message": "That slot was taken, and I couldn’t find another slot soon. Try a different time."}

    # Create booking
    result = _create_booking(db, user_id, space_id, start_utc, end_utc)
    return {
        "confirmed": True,
        "chosen": chosen,
        "start_time": pending["start_time"],
        "end_time": pending["end_time"],
        **result,
    }



def run_confirm_alt_slot(db: Session, user_id: str, pending: Dict[str, Any]) -> Dict[str, Any]:
    """
    If we suggested an alternative slot and the user says YES, we book it.
    """
    alt_space = pending.get("alt_space")
    if not alt_space:
        # "alt_options" path: book flow returns to choosing a space
        return {"error": "missing_alt_space", "message": "Please choose a space option to book."}

    start_utc = datetime.fromisoformat(pending["alt_start_time"])
    end_utc = datetime.fromisoformat(pending["alt_end_time"])
    space_id = alt_space["space_id"]

    if _space_has_overlap(db, space_id, start_utc, end_utc):
        return {"error": "slot_taken", "message": "That alternative slot was also taken. Please try another time."}

    result = _create_booking(db, user_id, space_id, start_utc, end_utc)
    return {
        "confirmed": True,
        "chosen": alt_space,
        "start_time": pending["alt_start_time"],
        "end_time": pending["alt_end_time"],
        **result,
    }
