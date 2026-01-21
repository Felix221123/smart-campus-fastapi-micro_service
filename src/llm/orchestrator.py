# main orchestrator
# Main entrypoint:
# loads conversation history (DB)
# checks pending actions (eg booking flow)
# routes question → tool(s)
# calls tool(s)
# uses LLM to turn tool output into a friendly answer
# writes messages + events back to DB


# src/llm/orchestrator.py
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, Optional

import openai
from sqlalchemy.orm import Session

from src.llm.cache import cache, make_key
from src.module.models import User

from src.llm.router import route
from src.llm.prompts import ANSWER_SYSTEM
from src.llm.memory import (
    ensure_conversation,
    append_message,
    recent_messages,
    log_event,
    get_metadata,
    set_metadata,
)

from src.llm.tools import (
    timetable_tool,
    assessments_tool,
    events_tool,
    spaces_tool,
    rag_tool,
    notifications_tool,
    space_booking_tool,
)

client = openai.OpenAI()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


_ORDINAL = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
}


# Repeat / cancel phrases (so you can escape or re-show options without getting stuck)
_REPEAT_OPTIONS_RE = re.compile(r"\b(options|show options|repeat|list them|what are the options|show me the options)\b", re.I)
_CANCEL_RE = re.compile(r"\b(cancel|stop|nevermind|never mind|forget it|abort|drop it)\b", re.I)

# Default TTLs (seconds)
_PENDING_TTL_DEFAULT = {
    "rag_pick": 300,        
    "space_booking": 600,   
}


_YES_RE = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|confirm|book it)\b", re.I)
_NO_RE = re.compile(r"\b(no|nah|nope|cancel)\b", re.I)



def _build_rag_options(hits: list[dict], max_options: int = 5) -> list[dict]:
    opts = []
    for h in (hits or [])[:max_options]:
        opts.append({
            "title": h.get("document_title"),
            "uri": h.get("uri"),
            "snippet": h.get("text"),
            "chunk_id": h.get("chunk_id"),
            "document_id": h.get("document_id"),
            "similarity": h.get("similarity"),
        })
    return opts

def _extract_option_number(text: str) -> Optional[int]:
    t = (text or "").lower()

    # "option 2"
    m = re.search(r"\boption\s*(\d+)\b", t)
    if m:
        return int(m.group(1))

    # "the second one"
    for k, v in _ORDINAL.items():
        if re.search(rf"\b{k}\b", t):
            return v

    # plain "2"
    m2 = re.search(r"\b(\d+)\b", t)
    if m2:
        return int(m2.group(1))

    return None


def _pending_is_expired(pending: Optional[Dict[str, Any]]) -> bool:
    if not pending:
        return False
    created_at = pending.get("created_at")
    ttl_sec = pending.get("ttl_sec")
    if created_at is None:
        return False
    if ttl_sec is None:
        ttl_sec = _PENDING_TTL_DEFAULT.get(pending.get("type", ""), 300)
    return (time.time() - float(created_at)) > float(ttl_sec)

def _clear_pending(db: Session, cid: str, meta: Dict[str, Any], reason: str) -> None:
    meta["pending_action"] = None
    set_metadata(db, cid, meta)
    log_event(db, cid, meta.get("user_id"), "pending_cleared", {"reason": reason})




def _get_user_ctx(db: Session, user_id: Optional[str]) -> Dict[str, Any]:
    if not user_id:
        return {}
    ck = make_key("user_ctx", user_id)
    cached = cache().get(ck)
    if cached:
        return cached

    u = db.query(User).filter(User.id == user_id).first()
    ctx = {"full_name": u.full_name if u else None}
    cache().set(ck, ctx, ttl_seconds=300)
    return ctx




def _compose_answer(question: str, tool: str, tool_output: Dict[str, Any], history: list[dict], user_ctx: Dict[str, Any]) -> str:
    messages = [{"role": "system", "content": ANSWER_SYSTEM}]

    # getting the users name to be mentioned in the application
    if user_ctx.get("full_name"):
        first = user_ctx["full_name"].split(' ')[0]
        messages.append({"role": "system", "content": f"The user's first name is {first}. You may address them by name occasionally."})


    for h in history[-8:]:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append(
        {
            "role": "user",
            "content": f"""
            User question: {question}

            Tool used: {tool}
            Tool output (JSON):
            {tool_output}

            Write a short, spoken-style answer.
            - If tool_output.requires_user_choice is true, list options as:
            Option 1: ...
            Option 2: ...
            Then ask the user to pick one.
            - If tool_output has a summary field, use it.
            - If tool_output has an error field, explain what to do next.
            """.strip(),
        }
    )

    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=350,
    )
    return resp.choices[0].message.content.strip()




def run(
    db: Session,
    question: str,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    channel: str = "web",
) -> Dict[str, Any]:
    # 1) ensure conversation exists
    cid = ensure_conversation(db, user_id=user_id, channel=channel, conversation_id=conversation_id)

    # 2) load metadata + pending
    meta = get_metadata(db, cid) or {}
    pending = meta.get("pending_action")

    # attach user_id into meta for event logs (optional)
    meta["user_id"] = user_id

    # Expire old pending actions
    if _pending_is_expired(pending):
        _clear_pending(db, cid, meta, reason="pending_expired")
        pending = None

    # 3) store user message
    append_message(db, cid, role="user", content=question)

    # 4) route (router already ignores rag_pick/booking unless user is selecting/continuing)
    routing = route(question, pending_action=pending)
    tool = routing["tool"]
    log_event(db, cid, user_id, "intent_detected", {"tool": tool, "reason": routing.get("reason")})

    tool_output: Dict[str, Any] = {}
    t0 = time.time()
    q_lower = (question or "").lower()
    selection = _extract_option_number(question)

    # --- If user switched topic away from a pending flow, clear it (unless they are asking to repeat options) ---
    if pending and pending.get("type") == "rag_pick" and tool != "rag":
        if selection is None and not _REPEAT_OPTIONS_RE.search(question or ""):
            _clear_pending(db, cid, meta, reason="topic_switched_from_rag_pick")
            pending = None

    if pending and pending.get("type") == "space_booking" and tool != "space_booking":
        if selection is None and not _REPEAT_OPTIONS_RE.search(question or ""):
            _clear_pending(db, cid, meta, reason="topic_switched_from_space_booking")
            pending = None

    # --- handle pending rag pick (user choosing option OR asking to repeat options) ---
    if tool == "rag" and pending and pending.get("type") == "rag_pick":
        options = pending.get("options", [])

        if _REPEAT_OPTIONS_RE.search(question or ""):
            tool_output = {
                "message": "Sure — here are the options again. Which one should I open?",
                "options": options,
                "requires_user_choice": True,
            }
        elif selection is None or selection < 1 or selection > len(options):
            tool_output = {
                "error": "missing_selection",
                "message": "No worries — please say something like: 'option 1' or 'the second one'.",
                "options": options,
                "requires_user_choice": True,
            }
        else:
            chosen = options[selection - 1]
            tool_output = {
                "chosen_option": selection,
                "chosen": chosen,
                "summary": "Got it — here’s the info for that option.",
            }
            # clear pending action once chosen
            meta["pending_action"] = None
            set_metadata(db, cid, meta)
            pending = None

    # --- handle booking confirmation step (select, repeat options, or cancel) ---
    
    if not tool_output and tool == "space_booking" and pending and pending.get("type") == "space_booking":
        step = pending.get("step", "choose_space")
        options = pending.get("options", [])

        # user was asked for time
        if step == "need_time":
            tool_output = space_booking_tool.run_set_time(db, question, pending)
            if tool_output.get("requires_time"):
                # still waiting for a valid time
                meta["pending_action"] = {**pending, "step": "need_time", "target_date": tool_output.get("target_date")}
                set_metadata(db, cid, meta)
            elif tool_output.get("requires_alt_slot_confirm"):
                meta["pending_action"] = {
                    **pending,
                    "step": "alt_slot_time",
                    "alt_start_time": tool_output["alt_start_time"],
                    "alt_end_time": tool_output["alt_end_time"],
                    "alt_options": tool_output.get("alt_options", []),
                    "location_hint": tool_output.get("location_hint"),
                }
                set_metadata(db, cid, meta)
            elif tool_output.get("requires_user_choice"):
                meta["pending_action"] = {
                    "type": "space_booking",
                    "step": "choose_space",
                    "created_at": time.time(),
                    "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                    "start_time": tool_output["start_time"],
                    "end_time": tool_output["end_time"],
                    "options": tool_output.get("options", []),
                }
                set_metadata(db, cid, meta)

        # we suggested an alternative slot (time-level) because slot was fully booked
        elif step == "alt_slot_time":
            if _YES_RE.search(question or ""):
                # accept alt slot -> move user into choosing a space for that alt slot
                alt_opts = pending.get("alt_options", [])
                tool_output = {
                    "summary": "Cool — here are the available spaces for the next slot. Which one should I book?",
                    "start_time": pending["alt_start_time"],
                    "end_time": pending["alt_end_time"],
                    "options": alt_opts,
                    "requires_user_choice": True,
                }
                meta["pending_action"] = {
                    "type": "space_booking",
                    "step": "choose_space",
                    "created_at": time.time(),
                    "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                    "start_time": pending["alt_start_time"],
                    "end_time": pending["alt_end_time"],
                    "options": alt_opts,
                }
                set_metadata(db, cid, meta)
            elif _NO_RE.search(question or ""):
                meta["pending_action"] = {"type": "space_booking", "step": "need_time", "created_at": time.time(), "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"]}
                set_metadata(db, cid, meta)
                tool_output = {"summary": "No worries — what time would you like instead? (e.g. 3pm / 15:00)"}
            else:
                tool_output = {"summary": "Say “yes” to book the next available slot, or “no” to choose a different time."}

        # normal choose-space step
        else:
            if _CANCEL_RE.search(question or ""):
                meta["pending_action"] = None
                set_metadata(db, cid, meta)
                pending = None
                tool_output = {"cancelled": True, "summary": "No problem — I’ve cancelled that booking request."}

            elif _REPEAT_OPTIONS_RE.search(question or ""):
                tool_output = {
                    "message": "Sure — here are the available spaces again. Which option would you like to book?",
                    "options": options,
                    "requires_user_choice": True,
                    "start_time": pending.get("start_time"),
                    "end_time": pending.get("end_time"),
                }

            elif selection is None:
                tool_output = {
                    "error": "missing_selection",
                    "message": "Please say something like 'book option 2'.",
                    "options": options,
                    "requires_user_choice": True,
                }

            else:
                if not user_id:
                    tool_output = {
                        "error": "missing_user",
                        "message": "Please sign in so I can confirm the booking. Then say 'book option 2'.",
                        "options": options,
                        "requires_user_choice": True,
                    }
                else:
                    tool_output = space_booking_tool.run_confirm(db, user_id=user_id, selection_index=selection, pending=pending)

                    # if confirm hit overlap and suggested alt slot for same space
                    if tool_output.get("requires_alt_slot_confirm") and tool_output.get("alt_space"):
                        meta["pending_action"] = {
                            "type": "space_booking",
                            "step": "alt_slot_space",
                            "created_at": time.time(),
                            "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                            "alt_space": tool_output["alt_space"],
                            "alt_start_time": tool_output["alt_start_time"],
                            "alt_end_time": tool_output["alt_end_time"],
                        }
                        set_metadata(db, cid, meta)

                    if tool_output.get("confirmed"):
                        meta["pending_action"] = None
                        set_metadata(db, cid, meta)
                        pending = None

    # handle alt slot confirm for same space (race condition)
    if not tool_output and tool == "space_booking" and pending and pending.get("type") == "space_booking" and pending.get("step") == "alt_slot_space":
        if _YES_RE.search(question or ""):
            tool_output = space_booking_tool.run_confirm_alt_slot(db, user_id=user_id, pending=pending)
            if tool_output.get("confirmed"):
                meta["pending_action"] = None
                set_metadata(db, cid, meta)
        elif _NO_RE.search(question or ""):
            meta["pending_action"] = {"type": "space_booking", "step": "need_time", "created_at": time.time(), "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"]}
            set_metadata(db, cid, meta)
            tool_output = {"summary": "Okay — what time would you like instead? (e.g. 3pm / 15:00)"}
        else:
            tool_output = {"summary": "Say “yes” to book the next available slot, or “no” to choose a different time."}

    # 6) normal tool execution (only if we haven't produced output from pending handlers)
    if not tool_output:
        if tool == "timetable":
            tool_output = timetable_tool.run(db, question)

        elif tool == "assessments":
            tool_output = assessments_tool.run(db, question)

        elif tool == "events":
            tool_output = events_tool.run(db, question)

        elif tool == "spaces":
            tool_output = spaces_tool.run(db, question)

        elif tool == "notifications":
            if not user_id:
                tool_output = {"error": "missing_user", "message": "Please sign in so I can read your notifications."}
            else:
                tool_output = notifications_tool.run(db, user_id=user_id)

        elif tool == "space_booking":
            # new booking find step
            if not user_id:
                tool_output = {"error": "missing_user", "message": "Please sign in so I can book a space for you."}
            else:
                tool_output = space_booking_tool.run_find(db, question)
                if tool_output.get("requires_time"):
                    meta["pending_action"] = {
                        "type": "space_booking",
                        "step": "need_time",
                        "created_at": time.time(),
                        "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                        "target_date": tool_output.get("target_date"),
                        "location_hint": tool_output.get("location_hint"),
                    }
                    set_metadata(db, cid, meta)

                elif tool_output.get("requires_alt_slot_confirm"):
                    meta["pending_action"] = {
                        "type": "space_booking",
                        "step": "alt_slot_time",
                        "created_at": time.time(),
                        "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                        "alt_start_time": tool_output["alt_start_time"],
                        "alt_end_time": tool_output["alt_end_time"],
                        "alt_options": tool_output.get("alt_options", []),
                        "location_hint": tool_output.get("location_hint"),
                    }
                    set_metadata(db, cid, meta)

                elif tool_output.get("requires_user_choice"):
                    meta["pending_action"] = {
                        "type": "space_booking",
                        "step": "choose_space",
                        "created_at": time.time(),
                        "ttl_sec": _PENDING_TTL_DEFAULT["space_booking"],
                        "start_time": tool_output.get("start_time"),
                        "end_time": tool_output.get("end_time"),
                        "options": tool_output.get("options", []),
                    }
                    set_metadata(db, cid, meta)


        else:
            # RAG fallback
            tool_output = rag_tool.run(db, question)

            hits = tool_output.get("hits") or []
            options = _build_rag_options(hits, max_options=5)

            if options:
                tool_output["options"] = options
                tool_output["requires_user_choice"] = True
                meta["pending_action"] = {
                    "type": "rag_pick",
                    "created_at": time.time(),
                    "ttl_sec": _PENDING_TTL_DEFAULT["rag_pick"],
                    "options": options,
                    "original_question": question,
                }
                set_metadata(db, cid, meta)

    latency_ms = int((time.time() - t0) * 1000)
    log_event(db, cid, user_id, "tool_result", {"tool": tool, "latency_ms": latency_ms})

    user_ctx = _get_user_ctx(db, user_id)

    # 7) compose final response via LLM
    history = recent_messages(db, cid, limit=12)
    answer = _compose_answer(question, tool, tool_output, history, user_ctx)

    # 8) store assistant message
    append_message(
        db,
        cid,
        role="assistant",
        content=answer,
        tool_name=tool,
        tool_payload=tool_output,
        latency_ms=latency_ms,
    )

    return {
        "conversation_id": cid,
        "tool": tool,
        "answer": answer,
        "tool_output": tool_output,
    }