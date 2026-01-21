# Intent detection + tool selection:
# heuristic routing (fast)
# optional “LLM router” later if you want
# src/llm/router.py
from __future__ import annotations
from typing import Dict, Optional
import re

# Option selection (rag or booking)
_OPTION_RE = re.compile(r"\b(?:option\s*)?([1-9]\d*)\b", re.I)
_RAG_SELECT_RE = re.compile(r"\b(option\s*[1-9]\d*|first|second|third|fourth|fifth|1st|2nd|3rd)\b", re.I)
_BOOKING_CONTINUE_RE = re.compile(r"\b(book|confirm|reserve|option\s*[1-9]\d*|cancel|stop|yes|yeah|yep|sure|ok|okay|no|nah)\b", re.I)

# keep "time range" separate so it doesn't hijack intent
TIME_RANGE_RE = re.compile(
    r"\b(this\s+week|next[-\s]?week|today|tomorrow|this\s+weekend|weekend|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I,
)

_TIME_REPLY_RE = re.compile(
    r"\b([01]?\d|2[0-3]):[0-5]\d\b|\b(1[0-2]|0?\d)\s*(am|pm)\b|\bnoon\b|\bmidnight\b|\b(now|right\s*now|asap)\b",
    re.I,
)

# strong keywords for each domain
EVENTS_KEYWORDS = re.compile(
    r"\b("
    r"events?|what'?s\s+on|what\s+is\s+on|happening|going\s+on|"
    r"societ(y|ies)|meetups?|networking|social|freshers|"
    r"talks?|guest\s+speaker|workshops?|career\s+fair|job\s+fair|"
    r"hackathon|open\s+day|volunteer(ing)?|"
    r"tournament|sports?\s+event"
    r")\b",
    re.I,
)

TIMETABLE_KEYWORDS = re.compile(
    r"\b(timetable|time\s*table|schedule|my\s+schedule|classes?|lectures?|labs?|tutorials?|seminars?)\b",
    re.I,
)

ASSESSMENTS_KEYWORDS = re.compile(
    r"\b(assessment|assessments|assignment|assignments|coursework|cw|deadline|deadlines|due|submission|submit|hand\s*in|"
    r"overdue|past\s+due|weight|marks?|grades?)\b",
    re.I,
)

SPACES_KEYWORDS = re.compile(
    r"\b(space|study\s+space|quiet\s+place|silent\s+zone|library|where\s+can\s+i\s+study|accessible|wheelchair|"
    r"group\s+space|collab|meeting)\b",
    re.I,
)

SPACE_BOOKING_KEYWORDS = re.compile(
    r"\b(book|booking|reserve|reservation|book\s+a\s+room|reserve\s+a\s+room|study\s*room|pod)\b",
    re.I,
)

NOTIFICATIONS_KEYWORDS = re.compile(r"\b(notification|notifications|remind|reminder|alert)\b", re.I)


def route(question: str, pending_action: Optional[Dict] = None) -> Dict[str, str]:
    q = (question or "").lower()

    # 1) Pending RAG pick (only if they’re actually selecting)
    if pending_action and pending_action.get("type") == "rag_pick":
        if _RAG_SELECT_RE.search(q):
            return {"tool": "rag", "reason": "pending_rag_pick_selection"}

    # 2) Pending booking (only if continuing booking flow)
    if pending_action and pending_action.get("type") == "space_booking":
        if _BOOKING_CONTINUE_RE.search(q) or _OPTION_RE.search(q) or _TIME_REPLY_RE.search(q):
            return {"tool": "space_booking", "reason": "pending_booking_continue"}

    # 3) Normal routing (order matters)
    if NOTIFICATIONS_KEYWORDS.search(q):
        return {"tool": "notifications", "reason": "notification_intent"}

    # booking has to be before spaces list
    if SPACE_BOOKING_KEYWORDS.search(q):
        return {"tool": "space_booking", "reason": "booking_intent"}

    # ✅ events before timetable, and events needs “events-ish” words
    if EVENTS_KEYWORDS.search(q):
        return {"tool": "events", "reason": "events_intent"}

    # timetable: either explicit timetable words, or “day/range” questions that look like schedule
    if TIMETABLE_KEYWORDS.search(q):
        return {"tool": "timetable", "reason": "timetable_intent"}

    # optional: if they only say “next week monday” (no keywords), treat as timetable
    if TIME_RANGE_RE.search(q):
        return {"tool": "timetable", "reason": "time_range_default_to_timetable"}

    if ASSESSMENTS_KEYWORDS.search(q):
        return {"tool": "assessments", "reason": "assessments_intent"}

    if SPACES_KEYWORDS.search(q):
        return {"tool": "spaces", "reason": "spaces_intent"}

    return {"tool": "rag", "reason": "fallback_to_rag"}