# Intent detection + tool selection:
# heuristic routing (fast)
# optional “LLM router” later if you want
# src/llm/router.py
from __future__ import annotations
from typing import Dict, Optional

_DAY_WORDS = {"today","tomorrow","monday","tuesday","wednesday","thursday","friday","saturday","sunday"}

def route(question: str, pending_action: Optional[Dict] = None) -> Dict[str, str]:
    q = (question or "").lower()

    # If we have an active multi-turn flow, always prioritize it
    if pending_action:
        if pending_action.get("type") == "space_booking":
            return {"tool": "space_booking", "reason": "pending_booking"}
        if pending_action.get("type") == "rag_pick":
            return {"tool": "rag", "reason": "pending_rag_pick"}   

    if "notification" in q or "notifications" in q or "remind" in q:
        return {"tool": "notifications", "reason": "notification_keywords"}

    if "book" in q and ("room" in q or "space" in q or "pod" in q or "study" in q):
        return {"tool": "space_booking", "reason": "booking_keywords"}

    if any(w in q for w in ["timetable","class","classes","lecture","lab","tutorial"]) or any(w in q for w in _DAY_WORDS):
        return {"tool": "timetable", "reason": "timetable_keywords"}

    if any(w in q for w in ["assessment","coursework","deadline","due","submission","grade","mark"]):
        return {"tool": "assessments", "reason": "assessments_keywords"}

    if any(w in q for w in ["event","events","society","freshers","meetup"]):
        return {"tool": "events", "reason": "events_keywords"}

    if any(w in q for w in ["space","study space","quiet","library","pod","room"]):
        return {"tool": "spaces", "reason": "spaces_keywords"}

    return {"tool": "rag", "reason": "fallback_to_rag"}
