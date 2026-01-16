# src/llm/tools/space_booking_tool.py
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from src.module.models import Space, SpaceBooking, BookingStatus  # adjust path


def _default_window(question: str) -> tuple[datetime, datetime]:
    # basic fallback: next hour
    start = datetime.utcnow() + timedelta(minutes=10)
    end = start + timedelta(hours=1)
    return start, end


def find_available_spaces(
    db: Session,
    start_time: datetime,
    end_time: datetime,
    limit: int = 5,
    location_hint: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # spaces that have NO overlapping bookings in that window
    # overlap condition: booking.start < end AND booking.end > start
    subq = (
        db.query(SpaceBooking.space_id)
        .filter(
            SpaceBooking.status != BookingStatus.CANCELLED,
            SpaceBooking.start_time < end_time,
            SpaceBooking.end_time > start_time,
        )
        .subquery()
    )

    q = db.query(Space).filter(~Space.id.in_(subq))
    if location_hint:
        q = q.filter(Space.location.ilike(f"%{location_hint}%"))

    spaces = q.limit(limit).all()

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


def create_booking(
    db: Session,
    user_id: str,
    space_id: str,
    start_time: datetime,
    end_time: datetime,
) -> Dict[str, Any]:
    booking = SpaceBooking(
        space_id=space_id,
        user_id=user_id,
        start_time=start_time,
        end_time=end_time,
        status=BookingStatus.CONFIRMED,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return {"booking_id": str(booking.id), "status": booking.status}


def run_find(db: Session, question: str) -> Dict[str, Any]:
    start, end = _default_window(question)
    location_hint = "library" if "library" in (question or "").lower() else None
    options = find_available_spaces(db, start, end, limit=5, location_hint=location_hint)
    return {
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "options": options,
        "requires_user_choice": True,
        "instruction": "Choose an option number to confirm the booking (e.g. 'book option 2').",
    }


def run_confirm(
    db: Session,
    user_id: str,
    selection_index: int,
    pending: Dict[str, Any],
) -> Dict[str, Any]:
    options = pending.get("options") or []
    if not options or selection_index < 1 or selection_index > len(options):
        return {"error": "invalid_selection", "message": "Please choose a valid option number."}

    chosen = options[selection_index - 1]
    start_time = datetime.fromisoformat(pending["start_time"])
    end_time = datetime.fromisoformat(pending["end_time"])

    result = create_booking(db, user_id, chosen["space_id"], start_time, end_time)
    return {
        "confirmed": True,
        "chosen": chosen,
        "start_time": pending["start_time"],
        "end_time": pending["end_time"],
        **result,
    }
