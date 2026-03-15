from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from src.analytics.models import AssistantQueryAnalytics


_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
_NUM_RE = re.compile(r"\b\d+\b")
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b|\b(1[0-2]|0?\d)\s?(am|pm)\b", re.I)
_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b")
_SPACE_RE = re.compile(r"\s+")


def _normalise_question(question: str) -> str:
    q = (question or "").strip().lower()
    q = _UUID_RE.sub("<uuid>", q)
    q = _DATE_RE.sub("<date>", q)
    q = _TIME_RE.sub("<time>", q)
    q = _NUM_RE.sub("<num>", q)
    q = _SPACE_RE.sub(" ", q)
    return q.strip()


def _hash_question(normalized_question: str) -> str:
    return hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()


def _answer_status(tool_name: str, tool_output: Dict[str, Any]) -> Tuple[str, bool, Optional[str]]:
    error_code = tool_output.get("error")
    has_error = bool(error_code)

    if tool_output.get("cancelled"):
        return "cancelled", False, None

    if tool_output.get("requires_user_choice"):
        return "needs_choice", has_error, error_code

    if tool_output.get("requires_time") or tool_output.get("requires_alt_slot_confirm"):
        return "partial", has_error, error_code

    if tool_name == "rag" and not (tool_output.get("hits") or []):
        return "no_hit", has_error, error_code

    if has_error:
        return "error", True, str(error_code)

    return "answered", False, None


def _extract_entity_refs(tool_name: str, tool_output: Dict[str, Any], pending_action: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Optional[str], Dict[str, Any], Optional[float], Optional[int], Optional[str]]:
    meta_information: Dict[str, Any] = {}
    module_id: Optional[str] = None
    assessment_id: Optional[str] = None
    space_id: Optional[str] = None
    rag_top_similarity: Optional[float] = None
    rag_hit_count: Optional[int] = None
    booking_step: Optional[str] = None

    if tool_name == "timetable":
        sessions = tool_output.get("sessions") or []
        module_ids = sorted({str(s.get("module_id")) for s in sessions if s.get("module_id")})
        module_codes = sorted({str(s.get("module_code")) for s in sessions if s.get("module_code")})
        meta_information.update({
            "module_ids": module_ids,
            "module_codes": module_codes,
            "primary_module_code": module_codes[0] if len(module_codes) == 1 else None,
            "range_kind": tool_output.get("range_kind"),
            "range_label": tool_output.get("range_label"),
            "session_count": tool_output.get("session_count", len(sessions)),
        })
        if len(module_ids) == 1:
            module_id = module_ids[0]

    elif tool_name == "assessments":
        items = (tool_output.get("upcoming") or []) + (tool_output.get("overdue") or [])
        module_codes = sorted({str(i.get("module_code")) for i in items if i.get("module_code")})
        meta_information.update({
            "module_codes": module_codes,
            "primary_module_code": module_codes[0] if len(module_codes) == 1 else None,
            "range_label": tool_output.get("range_label"),
            "assessment_titles": [i.get("title") for i in items[:10] if i.get("title")],
            "assessment_count": tool_output.get("count", len(items)),
        })

    elif tool_name == "spaces":
        spaces = tool_output.get("spaces") or []
        space_ids = [str(s.get("space_id")) for s in spaces if s.get("space_id")]
        meta_information.update({
            "space_ids": space_ids,
            "filters": tool_output.get("filters") or {},
            "space_count": tool_output.get("count", len(spaces)),
            "primary_location": spaces[0].get("location") if len(spaces) == 1 else None,
        })
        if len(space_ids) == 1:
            space_id = space_ids[0]

    elif tool_name == "space_booking":
        chosen = tool_output.get("chosen") or tool_output.get("alt_space")
        options = tool_output.get("options") or tool_output.get("alt_options") or []
        booking_step = (pending_action or {}).get("step")
        option_space_ids = [str(s.get("space_id")) for s in options if s.get("space_id")]
        meta_information.update({
            "confirmed": bool(tool_output.get("confirmed")),
            "booking_id": tool_output.get("booking_id"),
            "location_hint": tool_output.get("location_hint") or (pending_action or {}).get("location_hint"),
            "space_ids": option_space_ids,
            "start_time": tool_output.get("start_time") or (pending_action or {}).get("start_time"),
            "end_time": tool_output.get("end_time") or (pending_action or {}).get("end_time"),
            "range_label": tool_output.get("range_label"),
        })
        if chosen and chosen.get("space_id"):
            space_id = str(chosen.get("space_id"))
            meta_information["primary_space_name"] = chosen.get("name")
            meta_information["primary_location"] = chosen.get("location")
        elif len(option_space_ids) == 1:
            space_id = option_space_ids[0]

    elif tool_name == "rag":
        hits = tool_output.get("hits") or []
        rag_hit_count = len(hits)
        rag_top_similarity = float(hits[0].get("similarity")) if hits and hits[0].get("similarity") is not None else None
        meta_information.update({
            "document_ids": [str(h.get("document_id")) for h in hits if h.get("document_id")],
            "document_titles": [h.get("document_title") for h in hits if h.get("document_title")],
            "uris": [h.get("uri") for h in hits if h.get("uri")],
        })

    elif tool_name == "events":
        events = tool_output.get("events") or tool_output.get("items") or []
        meta_information.update({
            "event_count": tool_output.get("count", len(events)),
            "range_label": tool_output.get("range_label") or tool_output.get("window"),
        })

    elif tool_name == "notifications":
        items = tool_output.get("notifications") or tool_output.get("items") or []
        meta_information.update({
            "notification_count": len(items),
        })

    return module_id, assessment_id, space_id, meta_information, rag_top_similarity, rag_hit_count, booking_step


def log_query_analytics(
    db: Session,
    *,
    conversation_id: str,
    user_id: Optional[str],
    channel: str,
    question_text: str,
    detected_intent: str,
    route_reason: Optional[str],
    tool_name: str,
    tool_output: Dict[str, Any],
    latency_ms: Optional[int] = None,
    pending_action: Optional[Dict[str, Any]] = None,
    selected_option: Optional[int] = None,
) -> None:
    """Persist one analytics row per student query.
    Analytics must never break the assistant, so failures are swallowed.
    """
    try:
        normalized_question = _normalise_question(question_text)
        question_hash = _hash_question(normalized_question)
        answer_status, has_error, error_code = _answer_status(tool_name, tool_output)
        module_id, assessment_id, space_id, meta_information, rag_top_similarity, rag_hit_count, booking_step = _extract_entity_refs(
            tool_name, tool_output, pending_action
        )

        record = AssistantQueryAnalytics(
            conversation_id=conversation_id,
            user_id=user_id,
            channel=channel or "web",
            question_text=question_text,
            normalized_question=normalized_question,
            question_hash=question_hash,
            detected_intent=detected_intent,
            route_reason=route_reason,
            tool_name=tool_name,
            answer_status=answer_status,
            requires_user_choice=bool(tool_output.get("requires_user_choice")),
            has_error=has_error,
            error_code=error_code,
            latency_ms=latency_ms,
            rag_hit_count=rag_hit_count,
            rag_top_similarity=rag_top_similarity,
            selected_option=selected_option,
            pending_action_type=(pending_action or {}).get("type"),
            booking_step=booking_step,
            module_id=module_id,
            assessment_id=assessment_id,
            space_id=space_id,
            meta_information=meta_information or None,
        )
        db.add(record)
        db.commit()
    except Exception:
        db.rollback()
